#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_CLIMATE)

#include "esphome/core/log.h"
#include "matter_climate.h"
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
  // Unoccupied setpoints belong to the Occupancy feature (not enabled), so
  // only the occupied ones are set here.
  config.features.heating.occupied_heating_setpoint = heating;
  config.features.cooling.occupied_cooling_setpoint = cooling;
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

// ESPHome wins at boot: the climate platform owns state restore, so its state is
// pushed rather than replaying Matter's persisted attributes.
MatterClimateMapping::MatterClimateMapping(climate::Climate *climate,
                                           uint16_t endpoint_id,
                                           bool prefer_cooling,
                                           bool heating_only)
    : MatterBinding<MatterClimateState, MatterClimateInbound>(
          endpoint_id, BindingPolicy{BootSync::ESPHOME_WINS, SETTLE_MS, true}),
      climate_(climate), prefer_cooling_(prefer_cooling),
      heating_only_(heating_only) {}

void MatterClimateMapping::setup_() {
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

  this->watch_attribute(Thermostat::Id, Thermostat::Attributes::SystemMode::Id);
  if (this->spec_.heating)
    this->watch_attribute(Thermostat::Id,
                          Thermostat::Attributes::OccupiedHeatingSetpoint::Id);
  if (this->spec_.cooling)
    this->watch_attribute(Thermostat::Id,
                          Thermostat::Attributes::OccupiedCoolingSetpoint::Id);
  if (this->has_on_off_)
    this->watch_attribute(OnOff::Id, OnOff::Attributes::OnOff::Id);

  this->climate_->add_on_state_callback(
      [this](climate::Climate &) { this->request_push(); });
}

// Runs on the CHIP task, for every write that is not ESPHome's own.
void MatterClimateMapping::on_attribute_(uint32_t cluster, uint32_t attribute,
                                         const esp_matter_attr_val_t &v) {
  MatterClimateInbound in;
  if (cluster == Thermostat::Id &&
      attribute == Thermostat::Attributes::SystemMode::Id) {
    in.has_mode = true;
    in.system_mode = v.val.u8;
  } else if (cluster == Thermostat::Id &&
             attribute == Thermostat::Attributes::OccupiedHeatingSetpoint::Id) {
    in.has_heating = true;
    in.heating_centi = v.val.i16;
  } else if (cluster == Thermostat::Id &&
             attribute == Thermostat::Attributes::OccupiedCoolingSetpoint::Id) {
    in.has_cooling = true;
    in.cooling_centi = v.val.i16;
  } else if (cluster == OnOff::Id &&
             attribute == OnOff::Attributes::OnOff::Id) {
    in.has_on_off = true;
    in.on = v.val.b;
  } else {
    return;
  }
  this->submit_inbound(in);
}

void MatterClimateMapping::merge_inbound_(MatterClimateInbound &acc,
                                          const MatterClimateInbound &in) {
  climate_merge_inbound(acc, in);
}

// Runs on the ESPHome main loop. The order is fixed: the mode first, so that a
// setpoint of the same request is judged against the new mode.
void MatterClimateMapping::apply_inbound_(const MatterClimateInbound &in) {
  if (in.has_mode)
    this->apply_system_mode_(in.system_mode);
  if (in.has_on_off)
    this->apply_on_off_(in.on);
  if (in.has_heating)
    this->apply_setpoint_(true, in.heating_centi);
  if (in.has_cooling)
    this->apply_setpoint_(false, in.cooling_centi);
}

// Runs on the ESPHome main loop.
MatterClimateState MatterClimateMapping::capture_() {
  auto *climate = this->climate_;
  if (climate == nullptr)
    return {};
  auto traits = climate->get_traits();
  MatterClimateState s;

  if (climate->mode != climate::CLIMATE_MODE_OFF)
    this->last_active_mode_ = climate->mode;

  s.system_mode = SYSTEM_MODE_OFF;
  to_system_mode(climate->mode, s.system_mode);
  // A heating only device type has no Cool, Auto, ... SystemModes.
  if (this->heating_only_ && s.system_mode != SYSTEM_MODE_HEAT)
    s.system_mode = SYSTEM_MODE_OFF;

  s.local_temperature =
      traits.has_feature_flags(climate::CLIMATE_SUPPORTS_CURRENT_TEMPERATURE)
          ? conversion::temperature(climate->current_temperature)
          : conversion::temperature(NAN);

  // Which setpoints to write. For single point climates only the setpoint
  // belonging to the active mode follows the target; the other is untouched.
  // Values are clamped to the absolute limits: the Thermostat cluster rejects
  // reports outside them (and, with AutoMode, inside the dead band margins).
  int16_t heating = 0;
  int16_t cooling = 0;
  if (this->spec_.two_point) {
    if (std::isfinite(climate->target_temperature_low)) {
      s.write_heating = this->spec_.heating;
      heating = to_centi(climate->target_temperature_low);
    }
    if (std::isfinite(climate->target_temperature_high)) {
      s.write_cooling = this->spec_.cooling;
      cooling = to_centi(climate->target_temperature_high);
    }
  } else if (std::isfinite(climate->target_temperature)) {
    int16_t centi = to_centi(climate->target_temperature);
    switch (climate->mode) {
    case climate::CLIMATE_MODE_HEAT:
      s.write_heating = this->spec_.heating;
      heating = centi;
      break;
    case climate::CLIMATE_MODE_COOL:
      s.write_cooling = this->spec_.cooling;
      cooling = centi;
      break;
    case climate::CLIMATE_MODE_HEAT_COOL:
    case climate::CLIMATE_MODE_AUTO:
      break; // Target temperature is not adjustable in these modes.
    default:
      s.write_heating = this->spec_.heating && !this->spec_.cooling;
      s.write_cooling = this->spec_.cooling && !this->spec_.heating;
      heating = cooling = centi;
      break;
    }
  }
  s.heating = this->spec_.clamp_heating(heating);
  s.cooling = this->spec_.clamp_cooling(cooling);

  s.has_running_state = this->has_running_state_;
  s.running_state = to_running_state(climate->action);
  s.has_on_off = this->has_on_off_;
  s.on = climate->mode != climate::CLIMATE_MODE_OFF;
  return s;
}

// Runs on the CHIP task, inside a PushScope: esp-matter runs the attribute
// callbacks for these reports (the Thermostat setpoints and SystemMode are
// writable) and the cluster may shift the other setpoint to keep the dead band.
// None of that is a controller request.
void MatterClimateMapping::write_matter_(const MatterClimateState &s) {
  if (this->climate_ == nullptr)
    return;
  uint16_t eid = this->endpoint_id();
  auto *platform = binding_platform();
  auto report = [&](uint32_t cluster, uint32_t attribute,
                    esp_matter_attr_val_t value) {
    platform->write_attribute(eid, cluster, attribute, value, true);
  };
  report(Thermostat::Id, Thermostat::Attributes::SystemMode::Id,
         esp_matter_enum8(s.system_mode));
  report(Thermostat::Id, Thermostat::Attributes::LocalTemperature::Id,
         s.local_temperature);
  if (s.write_heating)
    report(Thermostat::Id, Thermostat::Attributes::OccupiedHeatingSetpoint::Id,
           esp_matter_int16(s.heating));
  if (s.write_cooling)
    report(Thermostat::Id, Thermostat::Attributes::OccupiedCoolingSetpoint::Id,
           esp_matter_int16(s.cooling));
  if (s.has_running_state)
    report(Thermostat::Id, Thermostat::Attributes::ThermostatRunningState::Id,
           esp_matter_bitmap16(s.running_state));
  if (s.has_on_off)
    report(OnOff::Id, OnOff::Attributes::OnOff::Id, esp_matter_bool(s.on));
}

// The requests below that the climate can't take are dropped: the reconcile
// after the apply puts the Matter attribute back to the climate's state.

void MatterClimateMapping::apply_system_mode_(uint8_t system_mode) {
  auto traits = this->climate_->get_traits();
  ClimateMode mode;
  // ClimateCall::validate_() silently drops unsupported modes, so check first
  // and log when the request cannot be honoured.
  if ((this->heating_only_ && system_mode != SYSTEM_MODE_OFF &&
       system_mode != SYSTEM_MODE_HEAT) ||
      !from_system_mode(system_mode, traits, mode)) {
    ESP_LOGW(TAG, "Endpoint %u: SystemMode %u is not supported by climate",
             this->endpoint_id(), system_mode);
    return;
  }
  // No call needed when the climate is there already (HEAT_COOL / AUTO both
  // report as Matter Auto).
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
    SetpointTarget active;
    switch (climate->mode) {
    case climate::CLIMATE_MODE_HEAT:
      active = SetpointTarget::Heat;
      break;
    case climate::CLIMATE_MODE_COOL:
      active = SetpointTarget::Cool;
      break;
    case climate::CLIMATE_MODE_HEAT_COOL:
    case climate::CLIMATE_MODE_AUTO:
      active = SetpointTarget::None;
      break;
    default:
      active = SetpointTarget::Neutral;
      break;
    }
    if (!climate_accepts_setpoint(heating, active, this->spec_.heating,
                                  this->spec_.cooling)) {
      ESP_LOGD(TAG,
               "Endpoint %u: ignoring %s setpoint, the climate's single "
               "target temperature is not active in this mode",
               this->endpoint_id(), heating ? "heating" : "cooling");
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
    if (mode == climate::CLIMATE_MODE_OFF)
      return; // nothing to turn on; the reconcile reports OnOff off
  }
  auto call = climate->make_call();
  call.set_mode(mode);
  call.perform();
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_CLIMATE
