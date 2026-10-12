#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_FAN)

#include "esphome/core/log.h"
#include "matter_component.h"
#include "matter_fan.h"
#include "matter_fan_step.h"

#include <algorithm>
#include <app/clusters/fan-control-server/FanControlCluster.h>
#include <app/clusters/fan-control-server/fan-control-server.h>
#include <cctype>
#include <cstring>
#include <data_model_provider/esp_matter_data_model_provider.h>
#include <esp_matter_cluster.h>
#include <platform/CHIPDeviceLayer.h>

static const char *const TAG = "matter";

namespace esphome::matter {

namespace {

using chip::app::Clusters::FanControlCluster;
namespace FanControl = chip::app::Clusters::FanControl;

// The Fan Control cluster keeps its own state; reads from controllers are
// served from the cluster object and not from the esp-matter attribute store.
// This means state must be written through the cluster object (the same
// approach as code driven sensor attributes).
FanControlCluster *find_fan_control_cluster(uint16_t endpoint_id) {
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {endpoint_id, FanControl::Id});
  return static_cast<FanControlCluster *>(server);
}

uint8_t clamp_speed_count(int speed_count) {
  return static_cast<uint8_t>(std::clamp(speed_count, 1, 100));
}

bool equals_ignore_case(const std::string &a, const char *b) {
  size_t len = strlen(b);
  if (a.size() != len)
    return false;
  for (size_t i = 0; i < len; i++) {
    if (std::tolower(static_cast<unsigned char>(a[i])) !=
        std::tolower(static_cast<unsigned char>(b[i])))
      return false;
  }
  return true;
}

// The fan's own spelling of a preset mode name, or "" when it has none.
std::string find_preset(const fan::FanTraits &traits, const char *name) {
  if (name == nullptr || name[0] == 0)
    return {};
  for (const auto &mode : traits.supported_preset_modes()) {
    std::string candidate(mode);
    if (equals_ignore_case(candidate, name))
      return candidate;
  }
  return {};
}

} // namespace

uint8_t fan_mode_sequence(const fan::FanTraits &traits, bool with_auto) {
  using Sequence = FanControl::FanModeSequenceEnum;
  Sequence sequence = with_auto ? Sequence::kOffHighAuto : Sequence::kOffHigh;
  if (traits.supports_speed()) {
    int speed_count = traits.supported_speed_count();
    if (speed_count >= 3)
      sequence = with_auto ? Sequence::kOffLowMedHighAuto
                           : Sequence::kOffLowMedHigh;
    else if (speed_count == 2)
      sequence = with_auto ? Sequence::kOffLowHighAuto : Sequence::kOffLowHigh;
  }
  return static_cast<uint8_t>(sequence);
}

esp_err_t add_fan_features(esp_matter::cluster_t *cluster, fan::Fan *fan,
                           const FanFeatureOptions &options) {
  if (fan == nullptr)
    return ESP_ERR_INVALID_ARG;
  auto traits = fan->get_traits();
  namespace feature = esp_matter::cluster::fan_control::feature;

  if (traits.supports_speed()) {
    feature::multi_speed::config_t config;
    config.speed_max = clamp_speed_count(traits.supported_speed_count());
    if (feature::multi_speed::add(cluster, &config) != ESP_OK)
      return ESP_FAIL;
  }
  if (traits.supports_oscillation()) {
    feature::rocking::config_t config;
    config.rock_support =
        static_cast<uint8_t>(FanControl::RockBitmap::kRockLeftRight);
    if (feature::rocking::add(cluster, &config) != ESP_OK)
      return ESP_FAIL;
  }
  if (traits.supports_direction()) {
    feature::airflow_direction::config_t config;
    if (feature::airflow_direction::add(cluster, &config) != ESP_OK)
      return ESP_FAIL;
  }
  if (options.step) {
    // Step moves the fan speed one step up or down.
    if (!traits.supports_speed()) {
      // Not fatal: failing the endpoint build would stop Matter altogether.
      ESP_LOGE(TAG,
               "Fan Control: the Step feature needs a fan with speed control, "
               "'%s' has none; Step is not available",
               fan->get_name().c_str());
    } else if (feature::step::add(cluster) != ESP_OK) {
      return ESP_FAIL;
    }
  }
  if (options.wind_natural != nullptr || options.wind_sleep != nullptr) {
    uint8_t support = 0;
    if (!find_preset(traits, options.wind_natural).empty())
      support |= static_cast<uint8_t>(FanControl::WindBitmap::kNaturalWind);
    if (!find_preset(traits, options.wind_sleep).empty())
      support |= static_cast<uint8_t>(FanControl::WindBitmap::kSleepWind);
    if (support == 0) {
      ESP_LOGE(TAG,
               "Fan Control: the Wind feature maps to the preset modes '%s' / "
               "'%s' but fan '%s' has neither; Wind is not available",
               options.wind_natural == nullptr ? "" : options.wind_natural,
               options.wind_sleep == nullptr ? "" : options.wind_sleep,
               fan->get_name().c_str());
    } else {
      feature::wind::config_t config;
      config.wind_support = support;
      if (feature::wind::add(cluster, &config) != ESP_OK)
        return ESP_FAIL;
    }
  }
  return ESP_OK;
}

void MatterComponent::register_fan(fan::Fan *fan, uint16_t endpoint_id,
                                   const char *wind_natural,
                                   const char *wind_sleep) {
  this->mappings_.push_back(
      new MatterFanMapping(fan, endpoint_id, wind_natural, wind_sleep));
}

MatterFanMapping::MatterFanMapping(fan::Fan *fan, uint16_t endpoint_id,
                                   const char *wind_natural,
                                   const char *wind_sleep)
    : MatterEndpointMappingBase(endpoint_id),
      chip::app::Clusters::FanControl::Delegate(endpoint_id), fan_(fan),
      wind_natural_request_(wind_natural == nullptr ? "" : wind_natural),
      wind_sleep_request_(wind_sleep == nullptr ? "" : wind_sleep) {}

void MatterFanMapping::initialize() {
  if (this->fan_ == nullptr)
    return;

  auto *cluster = find_fan_control_cluster(this->endpoint_id());
  if (cluster == nullptr) {
    ESP_LOGW(TAG, "Fan Control cluster not found on endpoint %u",
             this->endpoint_id());
    return;
  }

  // Features are selected from the fan traits when the endpoint is built.
  // Only map what the cluster actually has.
  auto traits = this->fan_->get_traits();
  auto features = cluster->GetFeatureMap();
  this->has_speed_ = traits.supports_speed() &&
                     features.Has(FanControl::Feature::kMultiSpeed);
  this->speed_count_ = this->has_speed_ ? cluster->GetSpeedMax() : 1;
  this->has_rocking_ = traits.supports_oscillation() &&
                       features.Has(FanControl::Feature::kRocking);
  this->has_direction_ = traits.supports_direction() &&
                         features.Has(FanControl::Feature::kAirflowDirection);
  this->has_step_ =
      this->has_speed_ && features.Has(FanControl::Feature::kStep);
  this->has_wind_ = features.Has(FanControl::Feature::kWind);
  if (this->has_wind_) {
    this->wind_natural_ =
        find_preset(traits, this->wind_natural_request_.c_str());
    this->wind_sleep_ = find_preset(traits, this->wind_sleep_request_.c_str());
    if (this->wind_natural_.empty() && this->wind_sleep_.empty()) {
      ESP_LOGW(TAG, "Endpoint %u: no preset mode of the fan matches Wind",
               this->endpoint_id());
      this->has_wind_ = false;
    }
  }
  // Without a delegate the cluster rejects the Step command, and WindSetting
  // would change without any effect.
  if (this->has_step_ || this->has_wind_)
    FanControl::SetDefaultDelegate(this->endpoint_id(), this);

  // Controller writes to FanMode, PercentSetting and SpeedSetting all change
  // the same cluster state, but only the written attribute itself is passed to
  // the callbacks. The callbacks therefore don't use the value but read the
  // settled cluster state (which includes the cascaded changes). The callbacks
  // never fire for the writes done in push_state_to_matter() because those go
  // to the cluster object directly.
  MatterAttributeCallback callback = [this](const esp_matter_attr_val_t &) {
    this->sync_from_cluster_();
  };
  global_matter_component->register_attribute_callback(
      this->endpoint_id(), FanControl::Id, FanControl::Attributes::FanMode::Id,
      callback);
  global_matter_component->register_attribute_callback(
      this->endpoint_id(), FanControl::Id,
      FanControl::Attributes::PercentSetting::Id, callback);
  if (this->has_speed_)
    global_matter_component->register_attribute_callback(
        this->endpoint_id(), FanControl::Id,
        FanControl::Attributes::SpeedSetting::Id, callback);
  if (this->has_rocking_)
    global_matter_component->register_attribute_callback(
        this->endpoint_id(), FanControl::Id,
        FanControl::Attributes::RockSetting::Id, callback);
  if (this->has_direction_)
    global_matter_component->register_attribute_callback(
        this->endpoint_id(), FanControl::Id,
        FanControl::Attributes::AirflowDirection::Id, std::move(callback));

  // Push the ESPHome state to Matter on changes. The state callback is called
  // synchronously from publish_state().
  this->last_ = this->snapshot_();
  this->fan_->add_on_state_callback([this]() {
    // Only real ESPHome-side changes are pushed. A repeated state would
    // overwrite a controller write that apply_() has not handled yet.
    auto now = this->snapshot_();
    if (now == this->last_)
      return;
    this->last_ = now;
    // synchronizing_from_matter_ is only true while apply_() runs
    // call.perform(). A fan that publishes its state later is treated as an
    // ESPHome-side change and pushed back, settings included.
    this->push_state_to_matter(!this->synchronizing_from_matter_);
  });

  // Restore the Matter state (Matter persists the FanMode).
  chip::DeviceLayer::SystemLayer().ScheduleLambda(
      [this]() { this->sync_from_cluster_(); });
}

// Runs on the CHIP task.
void MatterFanMapping::sync_from_cluster_() {
  auto *cluster = find_fan_control_cluster(this->endpoint_id());
  if (cluster == nullptr)
    return;

  MatterFanState state;
  auto percent = cluster->GetPercentSetting();
  // A null PercentSetting means Auto, which has no ESPHome equivalent.
  state.has_speed_info = !percent.IsNull();
  if (state.has_speed_info) {
    state.percent = percent.Value();
    state.off = state.percent == 0 ||
                cluster->GetFanMode() == FanControl::FanModeEnum::kOff;
    if (this->has_speed_) {
      // SpeedSetting is more accurate than PercentSetting for the Low, Medium
      // and High FanModes.
      auto speed = cluster->GetSpeedSetting();
      int value = speed.IsNull()
                      ? (this->speed_count_ * state.percent + 99) / 100
                      : speed.Value();
      state.speed = static_cast<uint8_t>(
          std::clamp(value, state.off ? 0 : 1, this->speed_count_));
    }
  }
  if (this->has_rocking_) {
    state.has_rock = true;
    state.rocking =
        cluster->GetRockSetting().Has(FanControl::RockBitmap::kRockLeftRight);
  }
  if (this->has_direction_) {
    state.has_direction = true;
    state.reverse = cluster->GetAirflowDirection() ==
                    FanControl::AirflowDirectionEnum::kReverse;
  }

  global_matter_component->defer_to_main_loop(
      [this, state]() { this->apply_(state); });
}

// Runs on the ESPHome main loop.
void MatterFanMapping::apply_(const MatterFanState &state) {
  auto call = this->fan_->make_call();
  bool changed = false;

  if (state.has_speed_info) {
    if (state.off) {
      // Never use set_speed(0), ESPHome clamps this to 1.
      if (this->fan_->state) {
        call.set_state(false);
        changed = true;
      }
    } else if (!this->fan_->state ||
               (this->has_speed_ && this->fan_->speed != state.speed)) {
      call.set_state(true);
      // Always pass the speed explicitly, otherwise ESPHome turns on the fan
      // at the maximum speed.
      if (this->has_speed_)
        call.set_speed(state.speed);
      changed = true;
    }
  }
  if (state.has_rock && this->fan_->oscillating != state.rocking) {
    call.set_oscillating(state.rocking);
    changed = true;
  }
  if (state.has_direction) {
    auto direction = state.reverse ? fan::FanDirection::REVERSE
                                   : fan::FanDirection::FORWARD;
    if (this->fan_->direction != direction) {
      call.set_direction(direction);
      changed = true;
    }
  }
  if (!changed)
    return;

  this->synchronizing_from_matter_ = true;
  call.perform();
  this->synchronizing_from_matter_ = false;
  this->last_ = this->snapshot_();
}

MatterFanMapping::FanSnapshot MatterFanMapping::snapshot_() const {
  FanSnapshot snapshot;
  snapshot.on = this->fan_->state;
  snapshot.speed = this->fan_->speed;
  snapshot.oscillating = this->fan_->oscillating;
  snapshot.reverse = this->fan_->direction == fan::FanDirection::REVERSE;
  snapshot.preset = this->active_preset_();
  return snapshot;
}

void MatterFanMapping::push_state_to_matter(bool update_settings) {
  uint16_t eid = this->endpoint_id();
  bool on = this->fan_->state;
  int speed = this->fan_->speed;
  bool oscillating = this->fan_->oscillating;
  bool reverse = this->fan_->direction == fan::FanDirection::REVERSE;
  bool has_speed = this->has_speed_;
  bool has_rocking = this->has_rocking_;
  bool has_direction = this->has_direction_;
  int speed_count = this->speed_count_;
  bool has_wind = this->has_wind_;
  uint8_t wind = 0;
  if (has_wind) {
    std::string preset = this->active_preset_();
    if (!preset.empty() && preset == this->wind_natural_)
      wind |= static_cast<uint8_t>(FanControl::WindBitmap::kNaturalWind);
    else if (!preset.empty() && preset == this->wind_sleep_)
      wind |= static_cast<uint8_t>(FanControl::WindBitmap::kSleepWind);
  }

  // The capture exceeds CHIP_CONFIG_LAMBDA_EVENT_SIZE, so not ScheduleLambda.
  run_on_matter_thread([=, this]() {
    auto *cluster = find_fan_control_cluster(eid);
    if (cluster == nullptr)
      return;
    this->pushing_ = true;

    uint8_t current_speed = 0;
    uint8_t current_percent = 0;
    if (on && has_speed) {
      current_speed = static_cast<uint8_t>(std::clamp(speed, 1, speed_count));
      current_percent = static_cast<uint8_t>(current_speed * 100 / speed_count);
    } else if (on) {
      current_percent = 100;
    }

    // The Setting attributes make the cluster derive FanMode and the other
    // setting. Never call SetFanMode(), it overwrites PercentSetting.
    if (update_settings) {
      if (has_speed)
        cluster->SetSpeedSetting(
            chip::app::DataModel::MakeNullable<uint8_t>(current_speed));
      else
        cluster->SetPercentSetting(
            chip::app::DataModel::MakeNullable<chip::Percent>(current_percent));
    }
    cluster->SetPercentCurrent(current_percent);
    if (has_speed)
      cluster->SetSpeedCurrent(current_speed);
    // A manual speed change clears the preset mode, also when it comes from
    // Matter. Calls OnWindSettingChanged() when the value changes, which finds
    // the preset already active.
    if (has_wind)
      cluster->SetWindSetting(chip::BitMask<FanControl::WindBitmap>(wind));

    if (!update_settings) {
      this->pushing_ = false;
      return;
    }
    if (has_rocking) {
      chip::BitMask<FanControl::RockBitmap> rock;
      rock.Set(FanControl::RockBitmap::kRockLeftRight, oscillating);
      cluster->SetRockSetting(rock);
    }
    if (has_direction)
      cluster->SetAirflowDirection(
          reverse ? FanControl::AirflowDirectionEnum::kReverse
                  : FanControl::AirflowDirectionEnum::kForward);
    this->pushing_ = false;
  });
}

std::string MatterFanMapping::active_preset_() const {
  auto preset = this->fan_->get_preset_mode();
  return std::string(preset.c_str(), preset.size());
}

// Runs on the CHIP task, from the cluster command handler.
chip::Protocols::InteractionModel::Status
MatterFanMapping::HandleStep(FanControl::StepDirectionEnum direction, bool wrap,
                             bool lowest_off) {
  using Status = chip::Protocols::InteractionModel::Status;
  auto *cluster = find_fan_control_cluster(this->endpoint_id());
  if (cluster == nullptr || !this->has_step_)
    return Status::Failure;

  // The speed the cluster reports. Auto (null speed) steps from Off.
  int current = 0;
  auto speed = cluster->GetSpeedSetting();
  if (!speed.IsNull() && cluster->GetFanMode() != FanControl::FanModeEnum::kOff)
    current = speed.Value();
  int target = fan_step_target(
      current, this->speed_count_,
      direction == FanControl::StepDirectionEnum::kIncrease, wrap, lowest_off);
  if (target == current)
    return Status::Success;

  // SpeedSetting makes the cluster derive PercentSetting and FanMode. Writes
  // through the cluster object don't reach the attribute callbacks, so apply
  // the settled state to the fan explicitly.
  auto status = cluster->SetSpeedSetting(
      chip::app::DataModel::MakeNullable<uint8_t>(static_cast<uint8_t>(target)));
  if (status != Status::Success)
    return status;
  this->sync_from_cluster_();
  return Status::Success;
}

// Runs on the CHIP task.
void MatterFanMapping::OnWindSettingChanged(
    chip::BitMask<FanControl::WindBitmap> value) {
  // Echo of push_state_to_matter(), not a controller request.
  if (!this->has_wind_ || this->pushing_)
    return;
  bool natural = value.Has(FanControl::WindBitmap::kNaturalWind);
  bool sleep = value.Has(FanControl::WindBitmap::kSleepWind);
  global_matter_component->defer_to_main_loop(
      [this, natural, sleep]() { this->apply_wind_(natural, sleep); });
}

// Runs on the ESPHome main loop. ESPHome presets are exclusive: with both bits
// set the one that is not active yet wins. Clearing the setting resets the
// preset the way ESPHome does for a manual speed change.
void MatterFanMapping::apply_wind_(bool natural, bool sleep) {
  std::string current = this->active_preset_();
  std::string wanted;
  if (natural && sleep)
    wanted = (current == this->wind_natural_) ? this->wind_sleep_
                                              : this->wind_natural_;
  else if (natural)
    wanted = this->wind_natural_;
  else if (sleep)
    wanted = this->wind_sleep_;
  if ((natural && this->wind_natural_.empty()) ||
      (sleep && this->wind_sleep_.empty()) || (natural && sleep && wanted.empty())) {
    ESP_LOGW(TAG, "Endpoint %u: WindSetting has no matching fan preset mode",
             this->endpoint_id());
    this->push_state_to_matter();
    return;
  }
  if (wanted.empty() && current.empty())
    return;
  // A preset is set on a running fan: the fan is not turned on by WindSetting.
  if (!wanted.empty() && !this->fan_->state) {
    ESP_LOGW(TAG, "Endpoint %u: WindSetting ignored, the fan is off",
             this->endpoint_id());
    this->push_state_to_matter(false); // reports WindSetting back
    return;
  }

  auto call = this->fan_->make_call();
  if (!wanted.empty()) {
    call.set_preset_mode(wanted.c_str());
  } else {
    // ESPHome has no call that clears a preset; a speed change does.
    if (!this->fan_->get_traits().supports_speed()) {
      ESP_LOGW(TAG,
               "Endpoint %u: fan has no speed control, its preset mode can't "
               "be cleared",
               this->endpoint_id());
      this->push_state_to_matter();
      return;
    }
    call.set_speed(std::max(this->fan_->speed, 1));
  }
  this->synchronizing_from_matter_ = true;
  call.perform();
  this->synchronizing_from_matter_ = false;
  this->last_ = this->snapshot_();
  // The Wind setting is correct now; the Current attributes follow the fan.
  this->push_state_to_matter(false);
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_FAN
