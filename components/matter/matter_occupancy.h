#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER_OCCUPANCY_HOLD_TIME

#include "matter_endpoints.h"

#ifdef USE_NUMBER
#include "esphome/components/number/number.h"
#endif

#include <cstdint>

namespace esphome::matter {

// Hold time of the Occupancy Sensing cluster of an endpoint.
//
// The Occupancy Sensing cluster of esp-matter (a code driven connectedhomeip
// cluster) implements the hold time itself: when the bound binary_sensor turns
// OFF the Occupancy attribute stays occupied for HoldTime seconds and only then
// becomes unoccupied. A new ON during that time cancels the timer. This mapping
//
//  * sets HoldTimeLimits and the configured default (once per configuration;
//    afterwards the value that a controller wrote and the cluster persisted
//    stays), clamping an older stored value into the limits, and
//  * optionally mirrors HoldTime to an ESPHome number: writes from Matter set
//    the number, changes of the number (including its restored state at boot)
//    set HoldTime.
class MatterOccupancyHoldTimeMapping : public MatterEndpointMappingBase {
public:
  MatterOccupancyHoldTimeMapping(uint16_t endpoint_id, uint16_t min,
                                 uint16_t max, uint16_t default_value)
      : MatterEndpointMappingBase(endpoint_id), min_(min), max_(max),
        default_(default_value) {}

#ifdef USE_NUMBER
  void set_number(number::Number *number) { this->number_ = number; }
#endif

  void initialize() override;

protected:
  // Main loop.
  bool default_not_applied_yet_() const;
#ifdef USE_NUMBER
  void on_number_(float value);
  void publish_to_number_(uint16_t seconds);
#endif
  // Matter thread.
  void apply_on_matter_thread_(bool apply_default, bool number_has_state,
                               uint16_t number_value);

  uint16_t min_;
  uint16_t max_;
  uint16_t default_;
#ifdef USE_NUMBER
  number::Number *number_{nullptr};
#endif
};

} // namespace esphome::matter

#endif // USE_MATTER_OCCUPANCY_HOLD_TIME
