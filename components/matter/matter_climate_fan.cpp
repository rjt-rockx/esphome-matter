#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_CLIMATE) && defined(USE_MATTER_CLIMATE_FAN)

#include "esphome/core/log.h"
#include "matter_attributes.h"
#include "matter_climate_fan.h"
#include "matter_component.h"
#include "matter_fan_step.h"

#include <app/clusters/fan-control-server/FanControlCluster.h>
#include <app/clusters/fan-control-server/fan-control-server.h>
#include <data_model_provider/esp_matter_data_model_provider.h>
#include <esp_matter_cluster.h>

static const char *const TAG = "matter";

namespace esphome::matter {

namespace {

namespace FanControl = chip::app::Clusters::FanControl;
using chip::app::Clusters::FanControlCluster;
using climate::ClimateFanMode;
using climate::ClimateSwingMode;
using FanModeEnum = FanControl::FanModeEnum;
using Sequence = FanControl::FanModeSequenceEnum;

FanControlCluster *find_cluster(uint16_t endpoint_id) {
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {endpoint_id, FanControl::Id});
  return static_cast<FanControlCluster *>(server);
}

// The climate fan mode that stands for a Matter fan mode, if any.
struct FanModeMap {
  optional<ClimateFanMode> off, low, medium, high, auto_;

  explicit FanModeMap(const climate::ClimateTraits &traits) {
    auto pick = [&](std::initializer_list<ClimateFanMode> candidates) {
      for (auto mode : candidates) {
        if (traits.supports_fan_mode(mode))
          return optional<ClimateFanMode>(mode);
      }
      return optional<ClimateFanMode>();
    };
    this->off = pick({climate::CLIMATE_FAN_OFF});
    this->low = pick({climate::CLIMATE_FAN_LOW, climate::CLIMATE_FAN_QUIET});
    this->medium =
        pick({climate::CLIMATE_FAN_MEDIUM, climate::CLIMATE_FAN_MIDDLE});
    this->high = pick({climate::CLIMATE_FAN_HIGH, climate::CLIMATE_FAN_ON});
    this->auto_ = pick({climate::CLIMATE_FAN_AUTO});
  }

  optional<ClimateFanMode> climate_mode(FanModeEnum mode) const {
    switch (mode) {
    case FanModeEnum::kOff:
      return this->off;
    case FanModeEnum::kLow:
      return this->low;
    case FanModeEnum::kMedium:
      return this->medium;
    case FanModeEnum::kHigh:
      return this->high;
    case FanModeEnum::kAuto:
      return this->auto_;
    default:
      return {};
    }
  }

  // Not available for ESPHome fan modes without a Matter equivalent.
  static bool matter_mode(ClimateFanMode mode, FanModeEnum &out) {
    switch (mode) {
    case climate::CLIMATE_FAN_OFF:
      out = FanModeEnum::kOff;
      return true;
    case climate::CLIMATE_FAN_LOW:
    case climate::CLIMATE_FAN_QUIET:
      out = FanModeEnum::kLow;
      return true;
    case climate::CLIMATE_FAN_MEDIUM:
    case climate::CLIMATE_FAN_MIDDLE:
      out = FanModeEnum::kMedium;
      return true;
    case climate::CLIMATE_FAN_HIGH:
    case climate::CLIMATE_FAN_ON:
      out = FanModeEnum::kHigh;
      return true;
    case climate::CLIMATE_FAN_AUTO:
      out = FanModeEnum::kAuto;
      return true;
    default:
      return false;
    }
  }
};

// RockSupport / RockSetting bits of the climate swing modes.
constexpr uint8_t ROCK_LEFT_RIGHT =
    static_cast<uint8_t>(FanControl::RockBitmap::kRockLeftRight);
constexpr uint8_t ROCK_UP_DOWN =
    static_cast<uint8_t>(FanControl::RockBitmap::kRockUpDown);

uint8_t rock_support(const climate::ClimateTraits &traits) {
  bool both = traits.supports_swing_mode(climate::CLIMATE_SWING_BOTH);
  uint8_t support = 0;
  if (both || traits.supports_swing_mode(climate::CLIMATE_SWING_HORIZONTAL))
    support |= ROCK_LEFT_RIGHT;
  if (both || traits.supports_swing_mode(climate::CLIMATE_SWING_VERTICAL))
    support |= ROCK_UP_DOWN;
  return support;
}

uint8_t swing_to_rock(ClimateSwingMode mode) {
  switch (mode) {
  case climate::CLIMATE_SWING_BOTH:
    return ROCK_LEFT_RIGHT | ROCK_UP_DOWN;
  case climate::CLIMATE_SWING_VERTICAL:
    return ROCK_UP_DOWN;
  case climate::CLIMATE_SWING_HORIZONTAL:
    return ROCK_LEFT_RIGHT;
  default:
    return 0;
  }
}

// The swing mode for a RockSetting; false if the climate can't do it.
bool rock_to_swing(uint8_t rock, const climate::ClimateTraits &traits,
                   ClimateSwingMode &mode) {
  if (rock == 0) {
    mode = climate::CLIMATE_SWING_OFF;
    return true;
  }
  ClimateSwingMode exact = rock == (ROCK_LEFT_RIGHT | ROCK_UP_DOWN)
                               ? climate::CLIMATE_SWING_BOTH
                               : (rock == ROCK_UP_DOWN
                                      ? climate::CLIMATE_SWING_VERTICAL
                                      : climate::CLIMATE_SWING_HORIZONTAL);
  if (traits.supports_swing_mode(exact)) {
    mode = exact;
    return true;
  }
  // A climate that only swings both ways takes any rock request.
  if (traits.supports_swing_mode(climate::CLIMATE_SWING_BOTH)) {
    mode = climate::CLIMATE_SWING_BOTH;
    return true;
  }
  return false;
}

uint8_t percent_of(FanModeEnum mode) {
  switch (mode) {
  case FanModeEnum::kLow:
    return 33;
  case FanModeEnum::kMedium:
    return 66;
  case FanModeEnum::kHigh:
    return 100;
  default:
    return 0;
  }
}

bool sequence_has(Sequence sequence, FanModeEnum mode) {
  bool three = sequence == Sequence::kOffLowMedHigh ||
               sequence == Sequence::kOffLowMedHighAuto;
  bool low_high = sequence == Sequence::kOffLowHigh ||
                  sequence == Sequence::kOffLowHighAuto;
  bool with_auto = sequence == Sequence::kOffLowMedHighAuto ||
                   sequence == Sequence::kOffLowHighAuto ||
                   sequence == Sequence::kOffHighAuto;
  switch (mode) {
  case FanModeEnum::kOff:
  case FanModeEnum::kHigh:
    return true;
  case FanModeEnum::kLow:
    return three || low_high;
  case FanModeEnum::kMedium:
    return three;
  case FanModeEnum::kAuto:
    return with_auto;
  default:
    return false;
  }
}

} // namespace

uint8_t climate_fan_mode_sequence(climate::Climate *climate) {
  FanModeMap modes(climate->get_traits());
  bool with_auto = modes.auto_.has_value();
  Sequence sequence;
  if (modes.medium.has_value())
    sequence = with_auto ? Sequence::kOffLowMedHighAuto : Sequence::kOffLowMedHigh;
  else if (modes.low.has_value())
    sequence = with_auto ? Sequence::kOffLowHighAuto : Sequence::kOffLowHigh;
  else
    sequence = with_auto ? Sequence::kOffHighAuto : Sequence::kOffHigh;
  return static_cast<uint8_t>(sequence);
}

esp_err_t add_climate_fan_features(esp_matter::cluster_t *cluster,
                                   climate::Climate *climate, bool step) {
  if (climate == nullptr)
    return ESP_ERR_INVALID_ARG;
  auto traits = climate->get_traits();
  FanModeMap modes(traits);
  namespace feature = esp_matter::cluster::fan_control::feature;

  if (!modes.low.has_value() && !modes.medium.has_value() &&
      !modes.high.has_value()) {
    ESP_LOGE(TAG,
             "Fan Control: climate '%s' supports none of the fan modes "
             "low, medium or high",
             climate->get_name().c_str());
    return ESP_ERR_NOT_SUPPORTED;
  }
  if (modes.auto_.has_value() && feature::fan_auto::add(cluster) != ESP_OK)
    return ESP_FAIL;
  if (uint8_t support = rock_support(traits)) {
    feature::rocking::config_t config;
    config.rock_support = support;
    if (feature::rocking::add(cluster, &config) != ESP_OK)
      return ESP_FAIL;
  }
  if (step && feature::step::add(cluster) != ESP_OK)
    return ESP_FAIL;
  return ESP_OK;
}

void MatterComponent::register_climate_fan(climate::Climate *climate,
                                           uint16_t endpoint_id) {
  this->mappings_.push_back(new MatterClimateFanMapping(climate, endpoint_id));
}

MatterClimateFanMapping::MatterClimateFanMapping(climate::Climate *climate,
                                                 uint16_t endpoint_id)
    : MatterEndpointMappingBase(endpoint_id),
      chip::app::Clusters::FanControl::Delegate(endpoint_id),
      climate_(climate) {}

void MatterClimateFanMapping::initialize() {
  if (this->climate_ == nullptr)
    return;
  auto *cluster = find_cluster(this->endpoint_id());
  if (cluster == nullptr) {
    ESP_LOGW(TAG, "Fan Control cluster not found on endpoint %u",
             this->endpoint_id());
    return;
  }
  auto features = cluster->GetFeatureMap();
  this->has_rocking_ = features.Has(FanControl::Feature::kRocking);
  this->has_step_ = features.Has(FanControl::Feature::kStep);
  FanControl::SetDefaultDelegate(this->endpoint_id(), this);

  this->climate_->add_on_state_callback(
      [this](climate::Climate &) { this->push_state_to_matter(); });
  // The climate platform owns the state, so push it at boot rather than
  // replaying the FanMode Matter persisted.
  global_matter_component->defer_to_main_loop(
      [this]() { this->push_state_to_matter(); });
}

void MatterClimateFanMapping::push_state_to_matter() {
  uint16_t eid = this->endpoint_id();
  auto *climate = this->climate_;
  bool has_mode = false;
  FanModeEnum mode = FanModeEnum::kOff;
  // A custom fan mode has no Matter equivalent, nothing to report then.
  if (climate->fan_mode.has_value() && !climate->has_custom_fan_mode())
    has_mode = FanModeMap::matter_mode(*climate->fan_mode, mode);
  bool has_rock = this->has_rocking_;
  uint8_t rock = swing_to_rock(climate->swing_mode);

  run_on_matter_thread([eid, has_mode, mode, has_rock, rock]() {
    auto *cluster = find_cluster(eid);
    if (cluster == nullptr)
      return;
    if (has_mode && sequence_has(cluster->GetFanModeSequence(), mode)) {
      // Calls OnFanDriveStateChanged(), which finds the climate in this mode.
      cluster->SetFanMode(mode);
      if (mode != FanModeEnum::kAuto)
        cluster->SetPercentCurrent(percent_of(mode));
    }
    if (has_rock)
      cluster->SetRockSetting(chip::BitMask<FanControl::RockBitmap>(
          rock & cluster->GetRockSupport().Raw()));
  });
}

// Runs on the CHIP task.
void MatterClimateFanMapping::OnFanDriveStateChanged(
    const FanControl::FanDriveState &state) {
  uint8_t mode = static_cast<uint8_t>(state.mode);
  global_matter_component->defer_to_main_loop(
      [this, mode]() { this->apply_fan_mode_(mode); });
}

// Runs on the CHIP task.
void MatterClimateFanMapping::OnRockSettingChanged(
    chip::BitMask<FanControl::RockBitmap> value) {
  uint8_t rock = value.Raw();
  global_matter_component->defer_to_main_loop(
      [this, rock]() { this->apply_rock_(rock); });
}

// Runs on the CHIP task, from the cluster command handler.
chip::Protocols::InteractionModel::Status MatterClimateFanMapping::HandleStep(
    FanControl::StepDirectionEnum direction, bool wrap, bool lowest_off) {
  using Status = chip::Protocols::InteractionModel::Status;
  auto *cluster = find_cluster(this->endpoint_id());
  if (cluster == nullptr || !this->has_step_)
    return Status::Failure;

  // The levels are Off followed by the modes of the sequence, lowest first.
  // Auto is not a level: stepping from Auto starts at Off.
  FanModeEnum levels[4];
  int count = 0;
  levels[count++] = FanModeEnum::kOff;
  auto sequence = cluster->GetFanModeSequence();
  for (auto mode :
       {FanModeEnum::kLow, FanModeEnum::kMedium, FanModeEnum::kHigh}) {
    if (sequence_has(sequence, mode))
      levels[count++] = mode;
  }
  int current = 0;
  for (int i = 0; i < count; i++) {
    if (levels[i] == cluster->GetFanMode())
      current = i;
  }
  int target = fan_step_target(
      current, count - 1, direction == FanControl::StepDirectionEnum::kIncrease,
      wrap, lowest_off);
  if (target == current)
    return Status::Success;
  // Calls OnFanDriveStateChanged(), which applies the mode to the climate.
  return cluster->SetFanMode(levels[target]);
}

// Runs on the ESPHome main loop.
void MatterClimateFanMapping::apply_fan_mode_(uint8_t fan_mode) {
  auto *climate = this->climate_;
  auto traits = climate->get_traits();
  auto target = FanModeMap(traits).climate_mode(static_cast<FanModeEnum>(fan_mode));
  if (!target.has_value()) {
    ESP_LOGW(TAG, "Endpoint %u: the climate does not support fan mode %u",
             this->endpoint_id(), fan_mode);
    this->push_state_to_matter();
    return;
  }
  FanModeEnum current_matter;
  if (climate->fan_mode.has_value() && !climate->has_custom_fan_mode() &&
      FanModeMap::matter_mode(*climate->fan_mode, current_matter) &&
      current_matter == static_cast<FanModeEnum>(fan_mode))
    return; // Already in this mode, also stops the echo of our own report.
  auto call = climate->make_call();
  call.set_fan_mode(*target);
  call.perform();
}

// Runs on the ESPHome main loop.
void MatterClimateFanMapping::apply_rock_(uint8_t rock) {
  auto *climate = this->climate_;
  ClimateSwingMode mode;
  if (!rock_to_swing(rock, climate->get_traits(), mode)) {
    ESP_LOGW(TAG, "Endpoint %u: the climate does not support RockSetting %u",
             this->endpoint_id(), rock);
    this->push_state_to_matter();
    return;
  }
  if (climate->swing_mode == mode)
    return;
  auto call = climate->make_call();
  call.set_swing_mode(mode);
  call.perform();
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_CLIMATE && USE_MATTER_CLIMATE_FAN
