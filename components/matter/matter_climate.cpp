#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_CLIMATE)

#include "esphome/core/log.h"
#include "matter_climate.h"
#include "matter_attributes.h"
#include "matter_component.h"
#include "matter_conversions.h"

#include <algorithm>
#include <cmath>
#include <esp_matter_cluster.h>
#include <platform/CHIPDeviceLayer.h>

static const char *const TAG = "matter";

namespace esphome::matter {

namespace {

using namespace chip::app::Clusters;
using climate::ClimateMode;

// Thermostat::SystemModeEnum values (spec 1.6).
constexpr uint8_t SYSTEM_MODE_OFF = 0;
constexpr uint8_t SYSTEM_MODE_AUTO = 1;
constexpr uint8_t SYSTEM_MODE_COOL = 3;
constexpr uint8_t SYSTEM_MODE_HEAT = 4;
constexpr uint8_t SYSTEM_MODE_FAN_ONLY = 7;
constexpr uint8_t SYSTEM_MODE_DRY = 8;

// Thermostat::ControlSequenceOfOperationEnum values.
constexpr uint8_t SEQUENCE_COOLING_ONLY = 0;
constexpr uint8_t SEQUENCE_HEATING_ONLY = 2;
constexpr uint8_t SEQUENCE_COOLING_AND_HEATING = 4;

// Thermostat::RelayStateBitmap (ThermostatRunningState).
constexpr uint16_t RUNNING_HEAT = 0x1;
constexpr uint16_t RUNNING_COOL = 0x2;
constexpr uint16_t RUNNING_FAN = 0x4;

// MinSetpointDeadBand is in 0.1 C.
constexpr int DEFAULT_DEAD_BAND = 20;

int16_t to_centi(float celsius) {
  float scaled = std::round(celsius * 100.0f);
  return static_cast<int16_t>(std::clamp(scaled, -27315.0f, 32767.0f));
}

bool same_centi(float celsius, int16_t centi) {
  return std::isfinite(celsius) && to_centi(celsius) == centi;
}

// ESPHome modes are mapped by name. HEAT_COOL and AUTO both become Matter Auto.
bool to_system_mode(ClimateMode mode, uint8_t &system_mode) {
  switch (mode) {
  case climate::CLIMATE_MODE_OFF:
    system_mode = SYSTEM_MODE_OFF;
    return true;
  case climate::CLIMATE_MODE_HEAT:
    system_mode = SYSTEM_MODE_HEAT;
    return true;
  case climate::CLIMATE_MODE_COOL:
    system_mode = SYSTEM_MODE_COOL;
    return true;
  case climate::CLIMATE_MODE_HEAT_COOL:
  case climate::CLIMATE_MODE_AUTO:
    system_mode = SYSTEM_MODE_AUTO;
    return true;
  case climate::CLIMATE_MODE_FAN_ONLY:
    system_mode = SYSTEM_MODE_FAN_ONLY;
    return true;
  case climate::CLIMATE_MODE_DRY:
    system_mode = SYSTEM_MODE_DRY;
    return true;
  default:
    return false;
  }
}

// Returns false for Matter modes without an ESPHome equivalent (EmergencyHeat,
// Precooling, Sleep) and for modes the climate does not support.
bool from_system_mode(uint8_t system_mode, const climate::ClimateTraits &traits,
                      ClimateMode &mode) {
  switch (system_mode) {
  case SYSTEM_MODE_OFF:
    mode = climate::CLIMATE_MODE_OFF;
    break;
  case SYSTEM_MODE_HEAT:
    mode = climate::CLIMATE_MODE_HEAT;
    break;
  case SYSTEM_MODE_COOL:
    mode = climate::CLIMATE_MODE_COOL;
    break;
  case SYSTEM_MODE_AUTO:
    mode = traits.supports_mode(climate::CLIMATE_MODE_HEAT_COOL)
               ? climate::CLIMATE_MODE_HEAT_COOL
               : climate::CLIMATE_MODE_AUTO;
    break;
  case SYSTEM_MODE_FAN_ONLY:
    mode = climate::CLIMATE_MODE_FAN_ONLY;
    break;
  case SYSTEM_MODE_DRY:
    mode = climate::CLIMATE_MODE_DRY;
    break;
  default:
    return false;
  }
  return traits.supports_mode(mode);
}

// ClimateAction FAN is 6 while ClimateMode FAN_ONLY is 4, so go by name.
uint16_t to_running_state(climate::ClimateAction action) {
  switch (action) {
  case climate::CLIMATE_ACTION_HEATING:
    return RUNNING_HEAT;
  case climate::CLIMATE_ACTION_COOLING:
  case climate::CLIMATE_ACTION_DRYING:
    return RUNNING_COOL;
  case climate::CLIMATE_ACTION_FAN:
    return RUNNING_FAN;
  default: // OFF, IDLE, DEFROSTING
    return 0;
  }
}

} // namespace

int16_t MatterClimateSpec::clamp_heating(int16_t centi) const {
  return std::clamp(centi, this->heat_min, this->heat_max);
}

int16_t MatterClimateSpec::clamp_cooling(int16_t centi) const {
  return std::clamp(centi, this->cool_min, this->cool_max);
}

MatterClimateSpec derive_climate_spec(climate::Climate *climate,
                                      bool prefer_cooling,
                                      bool heating_only) {
  auto traits = climate->get_traits();
  MatterClimateSpec spec;
  bool heat_cool = traits.supports_mode(climate::CLIMATE_MODE_HEAT_COOL);
  bool auto_ = traits.supports_mode(climate::CLIMATE_MODE_AUTO);
  spec.two_point = traits.has_feature_flags(
      climate::CLIMATE_SUPPORTS_TWO_POINT_TARGET_TEMPERATURE |
      climate::CLIMATE_REQUIRES_TWO_POINT_TARGET_TEMPERATURE);
  spec.has_action = traits.has_feature_flags(climate::CLIMATE_SUPPORTS_ACTION);
  spec.heating = traits.supports_mode(climate::CLIMATE_MODE_HEAT) ||
                 heat_cool || auto_ || spec.two_point;
  spec.cooling = traits.supports_mode(climate::CLIMATE_MODE_COOL) ||
                 heat_cool || auto_ || spec.two_point;
  if (heating_only) {
    if (spec.cooling)
      ESP_LOGW(TAG,
               "Climate can cool, but this device type can only heat: cooling "
               "modes are not exposed to Matter");
    spec.heating = true;
    spec.cooling = false;
  }
  if (!spec.heating && !spec.cooling) {
    // Matter requires at least one of Heating / Cooling.
    ESP_LOGW(TAG,
             "Climate supports neither heating nor cooling; exposing %s only",
             prefer_cooling ? "cooling" : "heating");
    (prefer_cooling ? spec.cooling : spec.heating) = true;
  }
  spec.auto_mode = spec.heating && spec.cooling && (heat_cool || auto_);
  spec.control_sequence =
      spec.heating && spec.cooling
          ? SEQUENCE_COOLING_AND_HEATING
          : (spec.cooling ? SEQUENCE_COOLING_ONLY : SEQUENCE_HEATING_ONLY);
  int16_t min_centi = to_centi(traits.get_visual_min_temperature());
  int16_t max_centi = to_centi(traits.get_visual_max_temperature());
  spec.heat_min = spec.cool_min = min_centi;
  spec.heat_max = spec.cool_max = max_centi;
  if (spec.auto_mode) {
    int range_dc = (max_centi - min_centi) / 10;
    spec.dead_band = static_cast<int8_t>(
        std::clamp(std::min(DEFAULT_DEAD_BAND, range_dc / 2), 0, 127));
    spec.heat_max = static_cast<int16_t>(max_centi - spec.dead_band * 10);
    spec.cool_min = static_cast<int16_t>(min_centi + spec.dead_band * 10);
  }
  return spec;
}

void configure_climate_thermostat(
    esp_matter::cluster::thermostat::config_t &config,
    climate::Climate *climate, bool prefer_cooling, bool heating_only) {
  namespace thermostat = esp_matter::cluster::thermostat;
  auto spec = derive_climate_spec(climate, prefer_cooling, heating_only);

  uint32_t flags = 0;
  if (spec.heating)
    flags |= thermostat::feature::heating::get_id();
  if (spec.cooling)
    flags |= thermostat::feature::cooling::get_id();
  // esp-matter's create path adds Heating and Cooling itself for AutoMode.
  if (spec.auto_mode)
    flags |= thermostat::feature::auto_mode::get_id();
  config.feature_flags = flags;
  config.control_sequence_of_operation = spec.control_sequence;
  config.local_temperature = nullable<int16_t>(); // null until first update

  // Initial setpoints, only used until the first state is pushed.
  float low = climate->target_temperature;
  float high = climate->target_temperature;
  if (spec.two_point) {
    low = climate->target_temperature_low;
    high = climate->target_temperature_high;
  }
  int16_t heating =
      spec.clamp_heating(std::isfinite(low) ? to_centi(low) : 2000);
  int16_t cooling =
      spec.clamp_cooling(std::isfinite(high) ? to_centi(high) : 2600);
  // A single point climate has one target for both; keep the dead band anyway.
  if (spec.auto_mode)
    cooling = spec.clamp_cooling(std::max<int16_t>(
        cooling, static_cast<int16_t>(heating + spec.dead_band * 10)));
  config.features.heating.occupied_heating_setpoint = heating;
  config.features.heating.unoccupied_heating_setpoint = heating;
  config.features.cooling.occupied_cooling_setpoint = cooling;
  config.features.cooling.unoccupied_cooling_setpoint = cooling;
  config.features.auto_mode.min_setpoint_dead_band = spec.dead_band;
}

// Creates the attributes esp-matter does not create on its own: the absolute
// setpoint limits (from the climate's visual min/max) and ThermostatRunningState
// (only if the climate reports actions). The user limits (Min/Max*SetpointLimit)
// are left out: they default to the absolute ones, and esp-matter makes them
// writable and persistent, so a stale stored value would outlive a changed
// visual range.
bool extend_climate_thermostat(esp_matter::endpoint_t *endpoint,
                               climate::Climate *climate,
                               bool prefer_cooling, bool heating_only) {
  namespace thermostat = esp_matter::cluster::thermostat;
  auto *cluster = esp_matter::cluster::get(endpoint, Thermostat::Id);
  if (cluster == nullptr)
    return false;
  auto spec = derive_climate_spec(climate, prefer_cooling, heating_only);
  if (spec.heating) {
    thermostat::attribute::create_abs_min_heat_setpoint_limit(cluster,
                                                              spec.heat_min);
    thermostat::attribute::create_abs_max_heat_setpoint_limit(cluster,
                                                              spec.heat_max);
  }
  if (spec.cooling) {
    thermostat::attribute::create_abs_min_cool_setpoint_limit(cluster,
                                                              spec.cool_min);
    thermostat::attribute::create_abs_max_cool_setpoint_limit(cluster,
                                                              spec.cool_max);
  }
  if (spec.has_action)
    thermostat::attribute::create_thermostat_running_state(cluster, 0);
  return true;
}

void MatterComponent::register_climate(climate::Climate *climate,
                                       uint16_t endpoint_id,
                                       bool prefer_cooling,
                                       bool heating_only) {
  this->mappings_.push_back(new MatterClimateMapping(
      climate, endpoint_id, prefer_cooling, heating_only));
}

MatterClimateMapping::MatterClimateMapping(climate::Climate *climate,
                                           uint16_t endpoint_id,
                                           bool prefer_cooling,
                                           bool heating_only)
    : MatterEndpointMappingBase(endpoint_id), climate_(climate),
      prefer_cooling_(prefer_cooling), heating_only_(heating_only) {}

void MatterClimateMapping::initialize() {
  if (this->climate_ == nullptr)
    return;

  this->spec_ = derive_climate_spec(this->climate_, this->prefer_cooling_,
                                    this->heating_only_);
  this->has_on_off_ = this->has_server_cluster(OnOff::Id);
  this->has_running_state_ =
      this->spec_.has_action &&
      esp_matter::attribute::get(
          this->endpoint_id(), Thermostat::Id,
          Thermostat::Attributes::ThermostatRunningState::Id) != nullptr;

  global_matter_component->register_attribute_callback(
      this->endpoint_id(), Thermostat::Id,
      Thermostat::Attributes::SystemMode::Id,
      [this](const esp_matter_attr_val_t &value) {
        uint8_t mode = value.val.u8;
        global_matter_component->defer_to_main_loop(
            [this, mode]() { this->apply_system_mode_(mode); });
      });
  if (this->spec_.heating) {
    global_matter_component->register_attribute_callback(
        this->endpoint_id(), Thermostat::Id,
        Thermostat::Attributes::OccupiedHeatingSetpoint::Id,
        [this](const esp_matter_attr_val_t &value) {
          int16_t centi = value.val.i16;
          global_matter_component->defer_to_main_loop(
              [this, centi]() { this->apply_setpoint_(true, centi); });
        });
  }
  if (this->spec_.cooling) {
    global_matter_component->register_attribute_callback(
        this->endpoint_id(), Thermostat::Id,
        Thermostat::Attributes::OccupiedCoolingSetpoint::Id,
        [this](const esp_matter_attr_val_t &value) {
          int16_t centi = value.val.i16;
          global_matter_component->defer_to_main_loop(
              [this, centi]() { this->apply_setpoint_(false, centi); });
        });
  }
  if (this->has_on_off_) {
    global_matter_component->register_attribute_callback(
        this->endpoint_id(), OnOff::Id, OnOff::Attributes::OnOff::Id,
        [this](const esp_matter_attr_val_t &value) {
          bool on = value.val.b;
          global_matter_component->defer_to_main_loop(
              [this, on]() { this->apply_on_off_(on); });
        });
  }

  this->climate_->add_on_state_callback(
      [this](climate::Climate &) { this->push_state_to_matter(); });

  // The climate platform owns state restore, so push ESPHome state at boot
  // rather than replaying Matter's persisted attributes.
  global_matter_component->defer_to_main_loop(
      [this]() { this->push_state_to_matter(); });
}

void MatterClimateMapping::push_state_to_matter() {
  uint16_t eid = this->endpoint_id();
  auto *climate = this->climate_;
  auto traits = climate->get_traits();

  if (climate->mode != climate::CLIMATE_MODE_OFF)
    this->last_active_mode_ = climate->mode;

  uint8_t system_mode = SYSTEM_MODE_OFF;
  to_system_mode(climate->mode, system_mode);
  // A heating only device type has no Cool, Auto, ... SystemModes.
  if (this->heating_only_ && system_mode != SYSTEM_MODE_HEAT)
    system_mode = SYSTEM_MODE_OFF;

  auto local_temperature =
      traits.has_feature_flags(climate::CLIMATE_SUPPORTS_CURRENT_TEMPERATURE)
          ? conversion::temperature(climate->current_temperature)
          : conversion::temperature(NAN);

  // Which setpoints to write. For single point climates only the setpoint
  // belonging to the active mode follows the target; the other is untouched.
  // Values are clamped to the absolute limits: the Thermostat cluster rejects
  // reports outside them (and, with AutoMode, inside the dead band margins).
  bool write_heating = false;
  bool write_cooling = false;
  int16_t heating = 0;
  int16_t cooling = 0;
  if (this->spec_.two_point) {
    if (std::isfinite(climate->target_temperature_low)) {
      write_heating = this->spec_.heating;
      heating = to_centi(climate->target_temperature_low);
    }
    if (std::isfinite(climate->target_temperature_high)) {
      write_cooling = this->spec_.cooling;
      cooling = to_centi(climate->target_temperature_high);
    }
  } else if (std::isfinite(climate->target_temperature)) {
    int16_t centi = to_centi(climate->target_temperature);
    switch (climate->mode) {
    case climate::CLIMATE_MODE_HEAT:
      write_heating = this->spec_.heating;
      heating = centi;
      break;
    case climate::CLIMATE_MODE_COOL:
      write_cooling = this->spec_.cooling;
      cooling = centi;
      break;
    case climate::CLIMATE_MODE_HEAT_COOL:
    case climate::CLIMATE_MODE_AUTO:
      break; // Target temperature is not adjustable in these modes.
    default:
      write_heating = this->spec_.heating && !this->spec_.cooling;
      write_cooling = this->spec_.cooling && !this->spec_.heating;
      heating = cooling = centi;
      break;
    }
  }

  heating = this->spec_.clamp_heating(heating);
  cooling = this->spec_.clamp_cooling(cooling);

  bool has_running_state = this->has_running_state_;
  uint16_t running_state = to_running_state(climate->action);
  bool has_on_off = this->has_on_off_;
  bool on = climate->mode != climate::CLIMATE_MODE_OFF;

  // The captures (an esp_matter_attr_val_t alone is 16 bytes) exceed
  // CHIP_CONFIG_LAMBDA_EVENT_SIZE, so ScheduleLambda would not compile.
  run_on_matter_thread(
      [this, eid, system_mode, local_temperature, write_heating, write_cooling,
       heating, cooling, has_running_state, running_state, has_on_off, on]() {
        using namespace chip::app::Clusters;
        // The Thermostat setpoints and SystemMode are writable: esp-matter runs
        // the attribute callbacks for these reports (and the cluster may shift the
        // other setpoint to keep the dead band). None of that is a controller
        // request.
        MatterPushGuard push_guard;
        auto mode_val = esp_matter_enum8(system_mode);
        esp_matter::attribute::report(
            eid, Thermostat::Id, Thermostat::Attributes::SystemMode::Id,
            &mode_val);
        auto temp_val = local_temperature;
        esp_matter::attribute::report(
            eid, Thermostat::Id, Thermostat::Attributes::LocalTemperature::Id,
            &temp_val);
        if (write_heating) {
          auto val = esp_matter_int16(heating);
          esp_matter::attribute::report(
              eid, Thermostat::Id,
              Thermostat::Attributes::OccupiedHeatingSetpoint::Id, &val);
        }
        if (write_cooling) {
          auto val = esp_matter_int16(cooling);
          esp_matter::attribute::report(
              eid, Thermostat::Id,
              Thermostat::Attributes::OccupiedCoolingSetpoint::Id, &val);
        }
        if (has_running_state) {
          auto val = esp_matter_bitmap16(running_state);
          esp_matter::attribute::report(
              eid, Thermostat::Id,
              Thermostat::Attributes::ThermostatRunningState::Id, &val);
        }
        if (has_on_off) {
          auto val = esp_matter_bool(on);
          esp_matter::attribute::report(eid, OnOff::Id,
                                        OnOff::Attributes::OnOff::Id, &val);
        }
      });
}

void MatterClimateMapping::apply_system_mode_(uint8_t system_mode) {
  auto traits = this->climate_->get_traits();
  ClimateMode mode;
  // ClimateCall::validate_() silently drops unsupported modes, so check first
  // and put the Matter attribute back when the request cannot be honoured.
  if ((this->heating_only_ && system_mode != SYSTEM_MODE_OFF &&
       system_mode != SYSTEM_MODE_HEAT) ||
      !from_system_mode(system_mode, traits, mode)) {
    ESP_LOGW(TAG, "Endpoint %u: SystemMode %u is not supported by climate",
             this->endpoint_id(), system_mode);
    this->push_state_to_matter();
    return;
  }
  // Also stops the echo of our own report from turning AUTO into HEAT_COOL.
  uint8_t current = 0;
  if (to_system_mode(this->climate_->mode, current) && current == system_mode)
    return;
  auto call = this->climate_->make_call();
  call.set_mode(mode);
  call.perform();
}

void MatterClimateMapping::apply_setpoint_(bool heating, int16_t centi) {
  auto *climate = this->climate_;
  float target = static_cast<float>(centi) / 100.0f;
  auto call = climate->make_call();
  if (this->spec_.two_point) {
    if (same_centi(heating ? climate->target_temperature_low
                           : climate->target_temperature_high,
                   centi))
      return;
    if (heating)
      call.set_target_temperature_low(target);
    else
      call.set_target_temperature_high(target);
  } else {
    bool accept;
    switch (climate->mode) {
    case climate::CLIMATE_MODE_HEAT:
      accept = heating;
      break;
    case climate::CLIMATE_MODE_COOL:
      accept = !heating;
      break;
    case climate::CLIMATE_MODE_HEAT_COOL:
    case climate::CLIMATE_MODE_AUTO:
      accept = false;
      break;
    default:
      accept = heating ? !this->spec_.cooling : !this->spec_.heating;
      break;
    }
    if (!accept) {
      ESP_LOGD(TAG,
               "Endpoint %u: ignoring %s setpoint, the climate's single "
               "target temperature is not active in this mode",
               this->endpoint_id(), heating ? "heating" : "cooling");
      this->push_state_to_matter();
      return;
    }
    if (same_centi(climate->target_temperature, centi))
      return;
    call.set_target_temperature(target);
  }
  call.perform();
}

void MatterClimateMapping::apply_on_off_(bool on) {
  auto *climate = this->climate_;
  if ((climate->mode != climate::CLIMATE_MODE_OFF) == on)
    return;
  ClimateMode mode = climate::CLIMATE_MODE_OFF;
  if (on) {
    auto traits = climate->get_traits();
    mode = this->last_active_mode_;
    if (mode == climate::CLIMATE_MODE_OFF || !traits.supports_mode(mode)) {
      mode = climate::CLIMATE_MODE_OFF;
      for (auto candidate :
           {climate::CLIMATE_MODE_HEAT_COOL, climate::CLIMATE_MODE_AUTO,
            climate::CLIMATE_MODE_COOL, climate::CLIMATE_MODE_HEAT,
            climate::CLIMATE_MODE_FAN_ONLY, climate::CLIMATE_MODE_DRY}) {
        if (traits.supports_mode(candidate)) {
          mode = candidate;
          break;
        }
      }
    }
    if (mode == climate::CLIMATE_MODE_OFF) {
      this->push_state_to_matter();
      return;
    }
  }
  auto call = climate->make_call();
  call.set_mode(mode);
  call.perform();
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_CLIMATE
