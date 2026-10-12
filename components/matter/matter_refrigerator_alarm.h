#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER_REFRIGERATOR_ALARM) && defined(USE_BINARY_SENSOR)

#include "esphome/components/binary_sensor/binary_sensor.h"
#include "matter_endpoints.h"

#include <app/clusters/refrigerator-alarm-server/refrigerator-alarm-server.h>

#include <cstdint>

namespace esphome::matter {

// Refrigerator Alarm: DoorOpen (bit 0 of State) follows a binary_sensor
// (ON = door open). A change updates the State attribute and generates the
// Notify event with the Active, Inactive, State and Mask bitmaps.
class MatterRefrigeratorAlarmMapping : public MatterEndpointMappingBase {
public:
  MatterRefrigeratorAlarmMapping(uint16_t endpoint_id,
                                 binary_sensor::BinarySensor *door_open)
      : MatterEndpointMappingBase(endpoint_id), door_open_(door_open) {}

  void initialize() override;

protected:
  void update_(bool open);

  binary_sensor::BinarySensor *door_open_;
};

} // namespace esphome::matter

#endif // USE_MATTER_REFRIGERATOR_ALARM && USE_BINARY_SENSOR
