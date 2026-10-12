#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_FAN)

#include "esphome/core/log.h"
#include "matter_component.h"
#include "matter_fan.h"

#include <algorithm>
#include <app/clusters/fan-control-server/FanControlCluster.h>
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

esp_err_t add_fan_features(esp_matter::cluster_t *cluster, fan::Fan *fan) {
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
  return ESP_OK;
}

void MatterComponent::register_fan(fan::Fan *fan, uint16_t endpoint_id) {
  this->mappings_.push_back(new MatterFanMapping(fan, endpoint_id));
}

MatterFanMapping::MatterFanMapping(fan::Fan *fan, uint16_t endpoint_id)
    : MatterEndpointMappingBase(endpoint_id), fan_(fan) {}

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
  this->fan_->add_on_state_callback([this]() {
    // TODO: Fix this race-condition
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

  chip::DeviceLayer::SystemLayer().ScheduleLambda([=]() {
    auto *cluster = find_fan_control_cluster(eid);
    if (cluster == nullptr)
      return;

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

    if (!update_settings)
      return;
    if (has_rocking) {
      chip::BitMask<FanControl::RockBitmap> rock;
      rock.Set(FanControl::RockBitmap::kRockLeftRight, oscillating);
      cluster->SetRockSetting(rock);
    }
    if (has_direction)
      cluster->SetAirflowDirection(
          reverse ? FanControl::AirflowDirectionEnum::kReverse
                  : FanControl::AirflowDirectionEnum::kForward);
  });
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_FAN
