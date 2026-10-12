#include "matter_occupancy.h"

#ifdef USE_MATTER_OCCUPANCY_HOLD_TIME

#include "esphome/core/log.h"
#include "matter_attributes.h"
#include "matter_component.h"

#include <app/clusters/occupancy-sensor-server/OccupancySensingCluster.h>
#include <clusters/OccupancySensing/Attributes.h>
#include <clusters/OccupancySensing/ClusterId.h>
#include <data_model_provider/esp_matter_data_model_provider.h>
#include <platform/KeyValueStoreManager.h>

#include <algorithm>
#include <cmath>
#include <cstdio>

static const char *const TAG = "matter.occupancy";

namespace esphome::matter {

namespace {

using chip::app::Clusters::OccupancySensingCluster;
namespace OS = chip::app::Clusters::OccupancySensing;

// HoldTime as persisted by this mapping, with the configuration it is valid for.
struct HoldTimeRecord {
  uint16_t min;
  uint16_t max;
  uint16_t default_value;
  uint16_t value;
};

OccupancySensingCluster *find_cluster(uint16_t endpoint_id) {
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {endpoint_id, OS::Id});
  return static_cast<OccupancySensingCluster *>(server);
}

void storage_key(uint16_t endpoint_id, char *key, size_t size) {
  snprintf(key, size, "esphome-hold-%u", static_cast<unsigned>(endpoint_id));
}

// HoldTime and the deprecated per sensor-type aliases of it.
bool is_hold_time_attribute(uint32_t attribute_id) {
  return attribute_id == OS::Attributes::HoldTime::Id ||
         attribute_id == OS::Attributes::PIROccupiedToUnoccupiedDelay::Id ||
         attribute_id ==
             OS::Attributes::UltrasonicOccupiedToUnoccupiedDelay::Id ||
         attribute_id ==
             OS::Attributes::PhysicalContactOccupiedToUnoccupiedDelay::Id;
}

} // namespace

void MatterOccupancyHoldTimeMapping::initialize() {
  bool number_has_state = false;
  uint16_t number_value = 0;

#ifdef USE_NUMBER
  if (this->number_ != nullptr) {
    if (this->number_->has_state() && !std::isnan(this->number_->state)) {
      number_has_state = true;
      number_value = static_cast<uint16_t>(std::clamp<long>(
          std::lround(this->number_->state), this->min_, this->max_));
    }
    this->number_->add_on_state_callback(
        [this](float value) { this->on_number_(value); });
  }
#endif

  // Writes of HoldTime by a controller (and of the deprecated aliases): the
  // callbacks run on the Matter thread.
  for (uint32_t attribute_id :
       {OS::Attributes::HoldTime::Id,
        OS::Attributes::PIROccupiedToUnoccupiedDelay::Id,
        OS::Attributes::UltrasonicOccupiedToUnoccupiedDelay::Id,
        OS::Attributes::PhysicalContactOccupiedToUnoccupiedDelay::Id}) {
    global_matter_component->register_attribute_callback(
        this->endpoint_id_, OS::Id, attribute_id,
        [this](const esp_matter_attr_val_t &value) {
          this->on_controller_write_(value.val.u16);
        });
  }

  run_on_matter_thread([this, number_has_state, number_value]() {
    this->apply_(number_has_state, number_value);
  });
}

// Matter thread. Applies HoldTime after the cluster started: the Matter
// persisted value wins (when it was stored for this configuration), then the
// number, then the default.
void MatterOccupancyHoldTimeMapping::apply_(bool number_has_state,
                                            uint16_t number_value) {
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

  OS::Structs::HoldTimeLimitsStruct::Type limits;
  limits.holdTimeMin = this->min_;
  limits.holdTimeMax = this->max_;
  limits.holdTimeDefault = this->default_;
  cluster->SetHoldTimeLimits(limits);

  char key[24];
  storage_key(this->endpoint_id_, key, sizeof(key));
  HoldTimeRecord record{};
  size_t read = 0;
  bool have_record =
      chip::DeviceLayer::PersistedStorage::KeyValueStoreMgr().Get(
          key, &record, sizeof(record), &read) == CHIP_NO_ERROR &&
      read == sizeof(record) && record.min == this->min_ &&
      record.max == this->max_ && record.default_value == this->default_;

  uint16_t value = this->default_;
  const char *origin = "default";
  if (have_record) {
    value = record.value;
    origin = "Matter";
  } else if (number_has_state) {
    value = number_value;
    origin = "number";
  }
  value = std::clamp(value, this->min_, this->max_);
  this->set_hold_time_(value);
  ESP_LOGD(TAG, "Endpoint %u: HoldTime %u s from %s (limits %u-%u s)",
           this->endpoint_id_, static_cast<unsigned>(value), origin,
           this->min_, this->max_);

#ifdef USE_NUMBER
  if (this->number_ != nullptr) {
    uint32_t seq = this->number_seq_.load();
    global_matter_component->defer_to_main_loop(
        [this, value, seq]() { this->publish_to_number_(value, seq); });
  }
#endif
}

// Matter thread. Returns false when the cluster refused the value.
bool MatterOccupancyHoldTimeMapping::set_hold_time_(uint16_t seconds) {
  auto *cluster = find_cluster(this->endpoint_id_);
  if (cluster == nullptr || !cluster->IsHoldTimeEnabled())
    return false;
  // ESPHome -> Matter push, not a controller write.
  MatterPushGuard push_guard;
  auto status = cluster->SetHoldTime(seconds);
  if (!status.IsSuccess()) {
    ESP_LOGW(TAG, "Cannot set HoldTime %u on endpoint %u",
             static_cast<unsigned>(seconds), this->endpoint_id_);
    return false;
  }
  this->save_(cluster->GetHoldTime());
  return true;
}

// Matter thread.
void MatterOccupancyHoldTimeMapping::save_(uint16_t seconds) const {
  char key[24];
  storage_key(this->endpoint_id_, key, sizeof(key));
  HoldTimeRecord record{this->min_, this->max_, this->default_, seconds};
  CHIP_ERROR err = chip::DeviceLayer::PersistedStorage::KeyValueStoreMgr().Put(
      key, &record, sizeof(record));
  if (err != CHIP_NO_ERROR)
    ESP_LOGW(TAG, "Cannot persist HoldTime of endpoint %u: %s",
             this->endpoint_id_, err.AsString());
}

// Matter thread, after the cluster accepted the write of a controller.
void MatterOccupancyHoldTimeMapping::on_controller_write_(uint16_t seconds) {
  this->save_(seconds);
#ifdef USE_NUMBER
  if (this->number_ != nullptr) {
    uint32_t seq = this->number_seq_.load();
    global_matter_component->defer_to_main_loop(
        [this, seconds, seq]() { this->publish_to_number_(seconds, seq); });
  }
#endif
}

#ifdef USE_NUMBER
// Main loop.
void MatterOccupancyHoldTimeMapping::on_number_(float value) {
  if (this->publishing_ || std::isnan(value))
    return;
  this->number_seq_.fetch_add(1);
  uint16_t seconds = static_cast<uint16_t>(
      std::clamp<long>(std::lround(value), this->min_, this->max_));
  if (std::lround(value) != static_cast<long>(seconds)) {
    // Outside the limits: Matter keeps the clamped value, show it.
    ESP_LOGW(TAG, "Number value %.0f is outside the HoldTime limits %u-%u s",
             value, this->min_, this->max_);
    uint32_t seq = this->number_seq_.load();
    global_matter_component->defer_to_main_loop(
        [this, seconds, seq]() { this->publish_to_number_(seconds, seq); });
  }
  run_on_matter_thread([this, seconds]() { this->set_hold_time_(seconds); });
}

// Main loop. Shows the value that Matter holds without sending it back.
void MatterOccupancyHoldTimeMapping::publish_to_number_(uint16_t seconds,
                                                        uint32_t seq) {
  if (seq != this->number_seq_.load())
    return;
  if (this->number_->has_state() &&
      std::lround(this->number_->state) == static_cast<long>(seconds))
    return;
  this->publishing_ = true;
  auto call = this->number_->make_call();
  call.set_value(static_cast<float>(seconds));
  call.perform();
  this->publishing_ = false;
}
#endif // USE_NUMBER

} // namespace esphome::matter

#endif // USE_MATTER_OCCUPANCY_HOLD_TIME
