#include "matter_occupancy.h"

#ifdef USE_MATTER_OCCUPANCY_HOLD_TIME

#include "esphome/core/helpers.h"
#include "esphome/core/log.h"
#include "esphome/core/preferences.h"
#include "matter_attributes.h"
#include "matter_component.h"

#include <app/clusters/occupancy-sensor-server/OccupancySensingCluster.h>
#include <clusters/OccupancySensing/Attributes.h>
#include <clusters/OccupancySensing/ClusterId.h>
#include <data_model_provider/esp_matter_data_model_provider.h>

#include <algorithm>
#include <cmath>
#include <string>

static const char *const TAG = "matter.occupancy";

namespace esphome::matter {

namespace {

using chip::app::Clusters::OccupancySensingCluster;

OccupancySensingCluster *find_cluster(uint16_t endpoint_id) {
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {endpoint_id, chip::app::Clusters::OccupancySensing::Id});
  return static_cast<OccupancySensingCluster *>(server);
}

} // namespace

// Returns true once for every combination of endpoint and limits/default, so a
// changed configuration applies its default again but a reboot does not undo
// what a controller wrote.
bool MatterOccupancyHoldTimeMapping::default_not_applied_yet_() const {
  std::string key = "matter_hold_time_" + std::to_string(this->endpoint_id_);
  std::string value = std::to_string(this->min_) + "/" +
                      std::to_string(this->max_) + "/" +
                      std::to_string(this->default_);
  uint32_t marker = fnv1_hash(value);
  auto preference =
      global_preferences->make_preference<uint32_t>(fnv1_hash(key));
  uint32_t stored = 0;
  if (preference.load(&stored) && stored == marker)
    return false;
  preference.save(&marker);
  return true;
}

void MatterOccupancyHoldTimeMapping::initialize() {
  bool apply_default = this->default_not_applied_yet_();
  bool number_has_state = false;
  uint16_t number_value = 0;

#ifdef USE_NUMBER
  if (this->number_ != nullptr) {
    if (this->number_->has_state()) {
      number_has_state = true;
      number_value = static_cast<uint16_t>(
          std::clamp<long>(std::lround(this->number_->state), this->min_,
                           this->max_));
    }
    this->number_->add_on_state_callback(
        [this](float value) { this->on_number_(value); });
    // Writes of HoldTime by a controller reach the number.
    global_matter_component->register_attribute_callback(
        this->endpoint_id_, chip::app::Clusters::OccupancySensing::Id,
        chip::app::Clusters::OccupancySensing::Attributes::HoldTime::Id,
        [this](const esp_matter_attr_val_t &value) {
          uint16_t seconds = value.val.u16;
          global_matter_component->defer_to_main_loop(
              [this, seconds]() { this->publish_to_number_(seconds); });
        });
  }
#endif

  run_on_matter_thread(
      [this, apply_default, number_has_state, number_value]() {
        this->apply_on_matter_thread_(apply_default, number_has_state,
                                      number_value);
      });
}

// Matter thread.
void MatterOccupancyHoldTimeMapping::apply_on_matter_thread_(
    bool apply_default, bool number_has_state, uint16_t number_value) {
  auto *cluster = find_cluster(this->endpoint_id_);
  if (cluster == nullptr) {
    ESP_LOGW(TAG, "Occupancy Sensing cluster of endpoint %u not found",
             this->endpoint_id_);
    return;
  }
  if (!cluster->IsHoldTimeEnabled()) {
    ESP_LOGW(TAG, "HoldTime is not enabled on endpoint %u",
             this->endpoint_id_);
    return;
  }

  chip::app::Clusters::OccupancySensing::Structs::HoldTimeLimitsStruct::Type
      limits;
  limits.holdTimeMin = this->min_;
  limits.holdTimeMax = this->max_;
  limits.holdTimeDefault = this->default_;
  cluster->SetHoldTimeLimits(limits);

  uint16_t value = cluster->GetHoldTime();
  if (number_has_state)
    value = number_value;
  else if (apply_default)
    value = this->default_;
  value = std::clamp(value, this->min_, this->max_);

  auto status = cluster->SetHoldTime(value);
  if (!status.IsSuccess()) {
    ESP_LOGW(TAG, "Failed to set HoldTime %u on endpoint %u",
             static_cast<unsigned>(value), this->endpoint_id_);
    return;
  }
  ESP_LOGD(TAG, "Endpoint %u: HoldTime %u s (limits %u-%u s)",
           this->endpoint_id_, static_cast<unsigned>(cluster->GetHoldTime()),
           this->min_, this->max_);
#ifdef USE_NUMBER
  if (this->number_ != nullptr && !number_has_state) {
    uint16_t current = cluster->GetHoldTime();
    global_matter_component->defer_to_main_loop(
        [this, current]() { this->publish_to_number_(current); });
  }
#endif
}

#ifdef USE_NUMBER
// Main loop.
void MatterOccupancyHoldTimeMapping::on_number_(float value) {
  if (std::isnan(value))
    return;
  uint16_t seconds = static_cast<uint16_t>(
      std::clamp<long>(std::lround(value), this->min_, this->max_));
  run_on_matter_thread([this, seconds]() {
    auto *cluster = find_cluster(this->endpoint_id_);
    if (cluster == nullptr || !cluster->IsHoldTimeEnabled())
      return;
    // A no-op when a controller wrote the value that the number now shows.
    auto status = cluster->SetHoldTime(seconds);
    if (!status.IsSuccess())
      ESP_LOGW(TAG, "Failed to set HoldTime %u on endpoint %u",
               static_cast<unsigned>(seconds), this->endpoint_id_);
  });
}

// Main loop.
void MatterOccupancyHoldTimeMapping::publish_to_number_(uint16_t seconds) {
  if (this->number_->has_state() &&
      std::lround(this->number_->state) == static_cast<long>(seconds))
    return;
  auto call = this->number_->make_call();
  call.set_value(static_cast<float>(seconds));
  call.perform();
}
#endif // USE_NUMBER

} // namespace esphome::matter

#endif // USE_MATTER_OCCUPANCY_HOLD_TIME
