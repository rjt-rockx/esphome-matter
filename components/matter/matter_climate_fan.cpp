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

#include <cstdlib>
#include <vector>

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

// Rank of a climate fan mode: 0 low, 1 medium, 2 high, -1 no Matter equivalent
// (Off and Auto are handled separately).
int rank_of(ClimateFanMode mode) {
  switch (mode) {
  case climate::CLIMATE_FAN_LOW:
  case climate::CLIMATE_FAN_QUIET:
    return 0;
  case climate::CLIMATE_FAN_MEDIUM:
  case climate::CLIMATE_FAN_MIDDLE:
    return 1;
  case climate::CLIMATE_FAN_HIGH:
  case climate::CLIMATE_FAN_ON:
    return 2;
  default:
    return -1;
  }
}

// The fan modes a climate supports, as the Matter modes of its FanModeSequence.
struct FanLevels {
  struct Level {
    int rank;
    ClimateFanMode mode;
    FanModeEnum matter;
  };
  std::vector<Level> levels; // ascending rank
  optional<ClimateFanMode> off;
  optional<ClimateFanMode> auto_;

  explicit FanLevels(const climate::ClimateTraits &traits) {
    if (traits.supports_fan_mode(climate::CLIMATE_FAN_OFF))
      this->off = climate::CLIMATE_FAN_OFF;
    if (traits.supports_fan_mode(climate::CLIMATE_FAN_AUTO))
      this->auto_ = climate::CLIMATE_FAN_AUTO;
    const ClimateFanMode candidates[3][2] = {
        {climate::CLIMATE_FAN_LOW, climate::CLIMATE_FAN_QUIET},
        {climate::CLIMATE_FAN_MEDIUM, climate::CLIMATE_FAN_MIDDLE},
        {climate::CLIMATE_FAN_HIGH, climate::CLIMATE_FAN_ON}};
    for (int rank = 0; rank < 3; rank++) {
      for (auto mode : candidates[rank]) {
        if (traits.supports_fan_mode(mode)) {
          this->levels.push_back({rank, mode, FanModeEnum::kHigh});
          break;
        }
      }
    }
    // 3 levels: Low, Medium, High. 2 levels: Low, High. 1 level: High.
    size_t n = this->levels.size();
    for (size_t i = 0; i < n; i++) {
      this->levels[i].matter = i == n - 1  ? FanModeEnum::kHigh
                               : i == 0    ? FanModeEnum::kLow
                                           : FanModeEnum::kMedium;
    }
  }

  Sequence sequence() const {
    bool with_auto = this->auto_.has_value();
    if (this->levels.size() >= 3)
      return with_auto ? Sequence::kOffLowMedHighAuto : Sequence::kOffLowMedHigh;
    if (this->levels.size() == 2)
      return with_auto ? Sequence::kOffLowHighAuto : Sequence::kOffLowHigh;
    return with_auto ? Sequence::kOffHighAuto : Sequence::kOffHigh;
  }

  // The climate fan mode for a Matter fan mode. Off is empty when the climate
  // has no OFF fan mode (see the file header).
  optional<ClimateFanMode> climate_mode(FanModeEnum mode) const {
    switch (mode) {
    case FanModeEnum::kOff:
      return this->off;
    case FanModeEnum::kAuto:
      return this->auto_;
    case FanModeEnum::kLow:
    case FanModeEnum::kMedium:
    case FanModeEnum::kHigh: {
      if (this->levels.empty())
        return {};
      // The sequence only offers modes that exist; snap anyway for a cluster
      // value that is outside of it.
      int want = mode == FanModeEnum::kLow ? 0 : (mode == FanModeEnum::kMedium ? 1 : 2);
      return this->nearest_(want).mode;
    }
    default:
      return {};
    }
  }

  // The Matter fan mode that reports a climate fan mode; false if it has none.
  bool matter_mode(ClimateFanMode mode, FanModeEnum &out) const {
    if (mode == climate::CLIMATE_FAN_OFF) {
      out = FanModeEnum::kOff;
      return true;
    }
    if (mode == climate::CLIMATE_FAN_AUTO) {
      out = FanModeEnum::kAuto;
      return this->auto_.has_value();
    }
    int rank = rank_of(mode);
    if (rank < 0 || this->levels.empty())
      return false;
    out = this->nearest_(rank).matter;
    return true;
  }

private:
  const Level &nearest_(int rank) const {
    const Level *best = &this->levels[0];
    for (const auto &level : this->levels) {
      if (std::abs(level.rank - rank) < std::abs(best->rank - rank))
        best = &level;
    }
    return *best;
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

} // namespace

uint8_t climate_fan_mode_sequence(climate::Climate *climate) {
  return static_cast<uint8_t>(FanLevels(climate->get_traits()).sequence());
}

// A mismatch between the configuration and the climate must not fail the
// endpoint build (Matter would not start): the feature is skipped with an error.
esp_err_t add_climate_fan_features(esp_matter::cluster_t *cluster,
                                   climate::Climate *climate, bool step) {
  if (climate == nullptr)
    return ESP_ERR_INVALID_ARG;
  auto traits = climate->get_traits();
  FanLevels levels(traits);
  namespace feature = esp_matter::cluster::fan_control::feature;

  if (levels.levels.empty())
    ESP_LOGE(TAG,
             "Fan Control: climate '%s' supports none of the fan modes low, "
             "medium or high; only Off%s is exposed",
             climate->get_name().c_str(), levels.auto_ ? " and Auto" : "");
  if (levels.auto_.has_value() && feature::fan_auto::add(cluster) != ESP_OK)
    return ESP_FAIL;
  if (uint8_t support = rock_support(traits)) {
    feature::rocking::config_t config;
    config.rock_support = support;
    if (feature::rocking::add(cluster, &config) != ESP_OK)
      return ESP_FAIL;
  }
  if (step) {
    if (levels.levels.empty())
      ESP_LOGE(TAG, "Fan Control: the Step feature needs fan modes, skipped");
    else if (feature::step::add(cluster) != ESP_OK)
      return ESP_FAIL;
  }
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
      [this](climate::Climate &) { this->push_state_(false); });
  // The climate platform owns the state, so push it at boot rather than
  // replaying the FanMode Matter persisted.
  global_matter_component->defer_to_main_loop(
      [this]() { this->push_state_(true); });
}

void MatterClimateFanMapping::push_state_to_matter() { this->push_state_(false); }

void MatterClimateFanMapping::push_state_(bool force) {
  uint16_t eid = this->endpoint_id();
  auto *climate = this->climate_;
  FanLevels levels(climate->get_traits());
  bool has_mode = false;
  FanModeEnum mode = FanModeEnum::kOff;
  if (!levels.off.has_value() && climate->mode == climate::CLIMATE_MODE_OFF) {
    // No fan mode for Off: the climate being off is the fan being off.
    has_mode = true;
  } else if (climate->fan_mode.has_value() && !climate->has_custom_fan_mode()) {
    // A custom fan mode has no Matter equivalent, nothing to report then.
    has_mode = levels.matter_mode(*climate->fan_mode, mode);
  }
  bool has_rock = this->has_rocking_;
  uint8_t rock = swing_to_rock(climate->swing_mode);

  // Climate state callbacks come for every temperature update: report only
  // what changed.
  uint8_t mode_value = static_cast<uint8_t>(mode);
  if (!force && this->pushed_ && has_mode == this->last_has_mode_ &&
      (!has_mode || mode_value == this->last_mode_) &&
      (!has_rock || rock == this->last_rock_))
    return;
  this->pushed_ = true;
  this->last_has_mode_ = has_mode;
  this->last_mode_ = mode_value;
  this->last_rock_ = rock;

  run_on_matter_thread([this, eid, has_mode, mode, has_rock, rock]() {
    auto *cluster = find_cluster(eid);
    if (cluster == nullptr)
      return;
    // The delegate callbacks run synchronously inside the setters.
    this->pushing_ = true;
    if (has_mode) {
      cluster->SetFanMode(mode);
      if (mode != FanModeEnum::kAuto)
        cluster->SetPercentCurrent(percent_of(mode));
    }
    if (has_rock)
      cluster->SetRockSetting(chip::BitMask<FanControl::RockBitmap>(
          rock & cluster->GetRockSupport().Raw()));
    this->pushing_ = false;
  });
}

// Runs on the CHIP task.
void MatterClimateFanMapping::OnFanDriveStateChanged(
    const FanControl::FanDriveState &state) {
  if (this->pushing_)
    return;
  uint8_t mode = static_cast<uint8_t>(state.mode);
  global_matter_component->defer_to_main_loop(
      [this, mode]() { this->apply_fan_mode_(mode); });
}

// Runs on the CHIP task.
void MatterClimateFanMapping::OnRockSettingChanged(
    chip::BitMask<FanControl::RockBitmap> value) {
  if (this->pushing_)
    return;
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
  FanLevels fan_levels(this->climate_->get_traits());
  std::vector<FanModeEnum> levels{FanModeEnum::kOff};
  for (const auto &level : fan_levels.levels)
    levels.push_back(level.matter);
  int current = 0;
  for (size_t i = 0; i < levels.size(); i++) {
    if (levels[i] == cluster->GetFanMode())
      current = static_cast<int>(i);
  }
  int target = fan_step_target(
      current, static_cast<int>(levels.size()) - 1,
      direction == FanControl::StepDirectionEnum::kIncrease, wrap, lowest_off);
  if (target == current)
    return Status::Success;
  // Calls OnFanDriveStateChanged(), which applies the mode to the climate.
  return cluster->SetFanMode(levels[target]);
}

// Runs on the ESPHome main loop.
void MatterClimateFanMapping::apply_fan_mode_(uint8_t fan_mode) {
  auto *climate = this->climate_;
  FanLevels levels(climate->get_traits());
  auto matter_mode = static_cast<FanModeEnum>(fan_mode);
  auto target = levels.climate_mode(matter_mode);
  if (!target.has_value()) {
    if (matter_mode == FanModeEnum::kOff) {
      // No OFF fan mode: Off turns the climate off.
      if (climate->mode != climate::CLIMATE_MODE_OFF) {
        auto call = climate->make_call();
        call.set_mode(climate::CLIMATE_MODE_OFF);
        call.perform();
      }
      return;
    }
    ESP_LOGW(TAG, "Endpoint %u: the climate has no fan mode for Matter mode %u",
             this->endpoint_id(), fan_mode);
    this->push_state_(true);
    return;
  }
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
    // Report the settled RockSetting back.
    this->push_state_(true);
    return;
  }
  auto call = climate->make_call();
  call.set_swing_mode(mode);
  call.perform();
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_CLIMATE && USE_MATTER_CLIMATE_FAN
