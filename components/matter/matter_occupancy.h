#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER_OCCUPANCY_HOLD_TIME

#include "matter_endpoints.h"

#ifdef USE_NUMBER
#include "esphome/components/number/number.h"
#endif

#include <atomic>
#include <cstdint>

namespace esphome::matter {

// Hold time of the Occupancy Sensing cluster of an endpoint.
//
// The Occupancy Sensing cluster of esp-matter (a code driven connectedhomeip
// cluster) implements the hold time itself: when the bound binary_sensor turns
// OFF the Occupancy attribute stays occupied for HoldTime seconds and only then
// becomes unoccupied. A new ON during that time cancels the timer. This mapping
//
//  * sets HoldTimeLimits and restores HoldTime. The SDK constructs the cluster
//    with limits 1/300/30 and its Startup() rejects a persisted value outside
//    them, so HoldTime is also persisted here, in the Matter key value store
//    (which a Matter factory reset erases) together with the limits it was
//    stored for. A record that matches the configured min/max/default wins;
//    without one the number's state is used, then the configured default.
//  * persists every change (controller writes of HoldTime and of the legacy
//    *OccupiedToUnoccupiedDelay aliases, number changes), and
//  * optionally mirrors HoldTime to an ESPHome number.
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
  // Matter thread.
  void apply_(bool number_has_state, uint16_t number_value);
  void on_controller_write_(uint16_t seconds);
  bool set_hold_time_(uint16_t seconds);
  void save_(uint16_t seconds) const;

#ifdef USE_NUMBER
  // Main loop.
  void on_number_(float value);
  void publish_to_number_(uint16_t seconds, uint32_t seq);
#endif

  uint16_t min_;
  uint16_t max_;
  uint16_t default_;
#ifdef USE_NUMBER
  number::Number *number_{nullptr};
  // True while this mapping itself changes the number, so the state callback
  // does not send the value back to Matter.
  bool publishing_{false};
  // Incremented for every change of the number. A value that Matter wants to
  // show is dropped when the number changed after it was read (a newer ESPHome
  // value is on its way to Matter).
  std::atomic<uint32_t> number_seq_{0};
#endif
};

} // namespace esphome::matter

#endif // USE_MATTER_OCCUPANCY_HOLD_TIME
