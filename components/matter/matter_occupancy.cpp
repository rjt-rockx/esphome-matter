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

void MatterOccupancyHoldTimeMapping::setup_() {
#ifdef USE_NUMBER
  if (this->number_ != nullptr) {
    if (this->number_->has_state() && !std::isnan(this->number_->state)) {
      this->boot_number_has_state_ = true;
      this->boot_number_value_ = static_cast<uint16_t>(std::clamp<long>(
          std::lround(this->number_->state), this->min_, this->max_));
    }
    this->number_->add_on_state_callback(
        [this](float value) { this->on_number_(value); });
  }
#endif

  // Writes of HoldTime by a controller (and of the deprecated aliases): the
  // callbacks run on the Matter thread. Our own writes never reach them.
  for (uint32_t attribute_id :
       {OS::Attributes::HoldTime::Id,
        OS::Attributes::PIROccupiedToUnoccupiedDelay::Id,
        OS::Attributes::UltrasonicOccupiedToUnoccupiedDelay::Id,
        OS::Attributes::PhysicalContactOccupiedToUnoccupiedDelay::Id})
    this->watch_attribute(OS::Id, attribute_id);

  // Queued before any push of the number, so it runs first on the CHIP task.
  this->boot_slot_.request();
}

void MatterOccupancyHoldTimeMapping::boot_trampoline_(intptr_t ctx) {
  auto *self = reinterpret_cast<MatterOccupancyHoldTimeMapping *>(ctx);
  self->boot_slot_.begin_run();
  self->apply_();
}

// Matter thread. Applies HoldTime after the cluster started: the Matter
// persisted value wins (when it was stored for this configuration), then the
// number, then the default.
void MatterOccupancyHoldTimeMapping::apply_() {
  const bool number_has_state = this->boot_number_has_state_;
  const uint16_t number_value = this->boot_number_value_;
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
  {
    PushScope scope(this->endpoint_id_);
    this->set_hold_time_(value);
  }
  ESP_LOGD(TAG, "Endpoint %u: HoldTime %u s from %s (limits %u-%u s)",
           this->endpoint_id_, static_cast<unsigned>(value), origin,
           this->min_, this->max_);

  // Show the chosen value in the number (a no-op when it is already there).
#ifdef USE_NUMBER
  if (this->number_ != nullptr)
    this->matter_changed_(value);
#endif
}

// Matter thread, inside a PushScope. Returns false when the cluster refused
// the value.
bool MatterOccupancyHoldTimeMapping::set_hold_time_(uint16_t seconds) {
  auto *cluster = find_cluster(this->endpoint_id_);
  if (cluster == nullptr || !cluster->IsHoldTimeEnabled())
    return false;
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
void MatterOccupancyHoldTimeMapping::on_attribute_(
    uint32_t, uint32_t attribute_id, const esp_matter_attr_val_t &value) {
  if (!is_hold_time_attribute(attribute_id))
    return;
  this->save_(value.val.u16);
#ifdef USE_NUMBER
  if (this->number_ != nullptr)
    this->matter_changed_(value.val.u16);
#endif
}

// Main loop.
bool MatterOccupancyHoldTimeMapping::entity_value_(int32_t &out) {
#ifdef USE_NUMBER
  if (this->number_ != nullptr && this->number_->has_state() &&
      !std::isnan(this->number_->state)) {
    out = std::clamp<long>(std::lround(this->number_->state), this->min_,
                           this->max_);
    return true;
  }
#endif
  return false;
}

// Main loop. Shows the value that Matter holds (without sending it back: the
// push that follows carries the same value).
void MatterOccupancyHoldTimeMapping::command_value_(int32_t value) {
#ifdef USE_NUMBER
  if (this->number_ == nullptr)
    return;
  if (this->number_->has_state() &&
      std::lround(this->number_->state) == static_cast<long>(value))
    return;
  auto call = this->number_->make_call();
  call.set_value(static_cast<float>(value));
  call.perform();
#endif
}

// CHIP task, inside the PushScope.
void MatterOccupancyHoldTimeMapping::write_value_(int32_t value) {
  this->set_hold_time_(static_cast<uint16_t>(value));
}

#ifdef USE_NUMBER
// Main loop.
void MatterOccupancyHoldTimeMapping::on_number_(float value) {
  if (std::isnan(value))
    return;
  long raw = std::lround(value);
  long seconds = std::clamp<long>(raw, this->min_, this->max_);
  if (raw != seconds) {
    // Outside the limits: Matter keeps the clamped value, show it.
    ESP_LOGW(TAG, "Number value %.0f is outside the HoldTime limits %u-%u s",
             value, this->min_, this->max_);
    this->command_value_(static_cast<int32_t>(seconds));
  }
  this->request_push();
}
#endif // USE_NUMBER

} // namespace esphome::matter

#endif // USE_MATTER_OCCUPANCY_HOLD_TIME
