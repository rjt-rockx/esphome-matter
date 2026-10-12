#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "esphome/core/component.h"
#include "matter_behaviours_logic.h"

#ifdef USE_OUTPUT
#include "esphome/components/output/binary_output.h"
#endif
#ifdef USE_LIGHT
#include "esphome/components/light/light_state.h"
#endif
#ifdef USE_BINARY_SENSOR
#include "esphome/components/binary_sensor/binary_sensor.h"
#endif

#include <cstdint>

namespace esphome::matter {

// Commissioning behaviours driven by ESPHome entities (docs/commissioning.md):
//   status_led  shows the commissioning state on an output or light
//   pairing     a binary_sensor with hold durations: open the commissioning
//               window, factory reset
// Main loop only. The CHIP side work is done by MatterCommissioning.
class MatterBehaviours : public Component {
public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override {
    return setup_priority::AFTER_CONNECTION;
  }

#ifdef USE_OUTPUT
  void set_status_output(output::BinaryOutput *output) {
    this->led_output_ = output;
  }
#endif
#ifdef USE_LIGHT
  void set_status_light(light::LightState *light) { this->led_light_ = light; }
#endif
  void set_status_led_enabled(bool enabled, bool commissioned_on) {
    this->led_enabled_ = enabled;
    this->commissioned_on_ = commissioned_on;
  }
#ifdef USE_BINARY_SENSOR
  void set_pairing_sensor(binary_sensor::BinarySensor *sensor) {
    this->pairing_sensor_ = sensor;
  }
#endif
  // factory_reset_hold_ms == 0 disables the factory reset gesture.
  void set_pairing(uint32_t open_window_hold_ms, uint32_t factory_reset_hold_ms,
                   uint32_t open_window_timeout_s) {
    this->pairing_ = PairingHold(open_window_hold_ms, factory_reset_hold_ms);
    this->open_window_hold_ms_ = open_window_hold_ms;
    this->factory_reset_hold_ms_ = factory_reset_hold_ms;
    this->open_window_timeout_s_ = open_window_timeout_s;
  }

protected:
  void update_led_(uint32_t now);
  void write_led_(bool on);

#ifdef USE_OUTPUT
  output::BinaryOutput *led_output_{nullptr};
#endif
#ifdef USE_LIGHT
  light::LightState *led_light_{nullptr};
#endif
#ifdef USE_BINARY_SENSOR
  binary_sensor::BinarySensor *pairing_sensor_{nullptr};
#endif
  bool led_enabled_{false};
  bool commissioned_on_{true};
  int8_t led_last_{-1};
  LedPattern led_pattern_{LedPattern::OFF};
  uint32_t led_pattern_since_{0};
  uint32_t confirm_until_{0};
  bool confirm_active_{false};

  PairingHold pairing_{0, 0};
  uint32_t open_window_hold_ms_{0};
  uint32_t factory_reset_hold_ms_{0};
  uint32_t open_window_timeout_s_{300};
  bool resetting_{false};
};

} // namespace esphome::matter

#endif // USE_MATTER
