#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER_REFRIGERATOR_ALARM) && defined(USE_BINARY_SENSOR)

#include "esphome/components/binary_sensor/binary_sensor.h"
#include "matter_binding.h"
#include "matter_endpoints.h"

#include <app/clusters/refrigerator-alarm-server/refrigerator-alarm-server.h>

#include <cstdint>

namespace esphome::matter {

// Refrigerator Alarm: DoorOpen (bit 0 of State) follows a binary_sensor
// (ON = door open). A change updates the State attribute and generates the
// Notify event with the Active, Inactive, State and Mask bitmaps.
//
// Every door transition raises its own Notify event, so the transitions go
// through a MatterEventQueue (ordered, never coalesced) instead of a latest
// state push.
class MatterRefrigeratorAlarmMapping : public MatterEndpointMappingBase {
public:
  MatterRefrigeratorAlarmMapping(uint16_t endpoint_id,
                                 binary_sensor::BinarySensor *door_open)
      : MatterEndpointMappingBase(endpoint_id), door_open_(door_open),
        doors_([this](uint16_t, const bool &open) { this->apply_(open); }) {}

  void initialize() override;

protected:
  // Matter thread, in order with the transitions posted before it.
  void apply_(bool open);

  binary_sensor::BinarySensor *door_open_;
  MatterEventQueue<bool> doors_;
};

} // namespace esphome::matter

#endif // USE_MATTER_REFRIGERATOR_ALARM && USE_BINARY_SENSOR
