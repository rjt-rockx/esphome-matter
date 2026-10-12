#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER_OCCUPANCY_HOLD_TIME

#include "matter_endpoints.h"
#include "matter_sync_bindings.h"

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
//  * sets HoldTimeLimits and restores HoldTime. The SDK constructs the cluster
//    with limits 1/300/30 and its Startup() rejects a persisted value outside
//    them, so HoldTime is also persisted here, in the Matter key value store
//    (which a Matter factory reset erases) together with the limits it was
//    stored for. A record that matches the configured min/max/default wins;
//    without one the number's state is used, then the configured default.
//  * persists every change (controller writes of HoldTime and of the legacy
//    *OccupiedToUnoccupiedDelay aliases, number changes), and
//  * optionally mirrors HoldTime to an ESPHome number, in both directions,
//    through the binding framework (MatterSettingBinding): a number change
//    is pushed (coalesced, PushScope echo suppression), a controller write
//    is applied to the number on the main loop, and a push captured before
//    such a write cannot overwrite it.
class MatterOccupancyHoldTimeMapping : public MatterSettingBinding {
public:
  MatterOccupancyHoldTimeMapping(uint16_t endpoint_id, uint16_t min,
                                 uint16_t max, uint16_t default_value)
      : MatterSettingBinding(
            endpoint_id,
            // The boot value is chosen on the CHIP task (see apply_()).
            BindingPolicy{BootSync::NONE, 0, true}),
        min_(min), max_(max), default_(default_value),
        boot_slot_(&MatterOccupancyHoldTimeMapping::boot_trampoline_,
                   reinterpret_cast<intptr_t>(this)) {}

#ifdef USE_NUMBER
  void set_number(number::Number *number) { this->number_ = number; }
#endif

protected:
  void setup_() override;
  // CHIP task: a controller wrote HoldTime (or an alias).
  void on_attribute_(uint32_t cluster, uint32_t attribute,
                     const esp_matter_attr_val_t &v) override;
  bool entity_value_(int32_t &out) override;
  void command_value_(int32_t value) override;
  void write_value_(int32_t value) override;

  // CHIP task.
  static void boot_trampoline_(intptr_t ctx);
  void apply_();
  bool set_hold_time_(uint16_t seconds);
  void save_(uint16_t seconds) const;

#ifdef USE_NUMBER
  // Main loop.
  void on_number_(float value);
#endif

  uint16_t min_;
  uint16_t max_;
  uint16_t default_;
  ChipWorkSlot boot_slot_;
  // The state of the number at boot, read on the main loop for apply_().
  bool boot_number_has_state_{false};
  uint16_t boot_number_value_{0};
#ifdef USE_NUMBER
  number::Number *number_{nullptr};
#endif
};

} // namespace esphome::matter

#endif // USE_MATTER_OCCUPANCY_HOLD_TIME
