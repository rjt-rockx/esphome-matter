#include "matter_refrigerator_alarm.h"

#if defined(USE_MATTER_REFRIGERATOR_ALARM) && defined(USE_BINARY_SENSOR)

#include "esphome/core/log.h"
#include "matter_attributes.h"
#include "matter_component.h"

static const char *const TAG = "matter.refrigerator_alarm";

namespace esphome::matter {

void MatterRefrigeratorAlarmMapping::initialize() {
  if (this->door_open_ == nullptr)
    return;
  this->door_open_->add_on_state_callback(
      [this](bool open) { this->update_(open); });
  if (this->door_open_->has_state())
    this->update_(this->door_open_->state);
}

// Main loop; the attribute and the event are handled on the Matter thread.
void MatterRefrigeratorAlarmMapping::update_(bool open) {
  uint16_t endpoint_id = this->endpoint_id_;
  run_on_matter_thread([endpoint_id, open]() {
    using chip::app::Clusters::RefrigeratorAlarm::AlarmMap;
    auto &server = RefrigeratorAlarmServer::Instance();
    chip::BitMask<AlarmMap> state;
    if (server.GetStateValue(endpoint_id, &state) !=
        chip::Protocols::InteractionModel::Status::Success) {
      ESP_LOGW(TAG, "Refrigerator Alarm of endpoint %u not found",
               endpoint_id);
      return;
    }
    chip::BitMask<AlarmMap> new_state = state;
    if (open)
      new_state.Set(AlarmMap::kDoorOpen);
    else
      new_state.Clear(AlarmMap::kDoorOpen);
    if (new_state == state)
      return;
    // Writes State (a POST_UPDATE callback follows, so this is an ESPHome ->
    // Matter push) and generates the Notify event (Active, Inactive, State and
    // Mask). DoorOpen is the only alarm and always in the Mask.
    MatterPushGuard push_guard;
    auto status = server.SetStateValue(endpoint_id, new_state);
    if (status != chip::Protocols::InteractionModel::Status::Success)
      ESP_LOGW(TAG, "Cannot update the Refrigerator Alarm State: %u",
               static_cast<unsigned>(status));
  });
}

} // namespace esphome::matter

#endif // USE_MATTER_REFRIGERATOR_ALARM && USE_BINARY_SENSOR
