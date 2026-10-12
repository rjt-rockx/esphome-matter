#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER_BLE
#include "esphome/core/component.h"

#include <platform/CHIPDeviceEvent.h>

namespace esphome::matter {

// Diagnostics for BLE commissioning: logs CHIPoBLE advertising / connection
// events so a failed commissioning attempt is visible in the ESPHome log.
// Only compiled into the BLE commissioning build (no wifi/openthread).
class MatterBle : public Component {
public:
  void setup() override;
  void dump_config() override;
  // After MatterComponent (AFTER_CONNECTION - 5) so the CHIP stack exists
  // when the event handler is registered.
  float get_setup_priority() const override {
    return setup_priority::AFTER_CONNECTION - 6.0f;
  }

protected:
  static void handle_event_(const chip::DeviceLayer::ChipDeviceEvent *event,
                            intptr_t arg);
};

} // namespace esphome::matter

#endif // USE_MATTER_BLE
