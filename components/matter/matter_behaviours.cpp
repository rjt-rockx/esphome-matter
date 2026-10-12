#include "matter_behaviours.h"

#ifdef USE_MATTER

#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "matter_commissioning.h"

namespace esphome::matter {

static const char *const TAG = "matter.behaviours";

void MatterBehaviours::setup() {
  // The LED keeps following the commissioning state in loop(); nothing to hook.
  this->write_led_(false);
}

void MatterBehaviours::dump_config() {
  ESP_LOGCONFIG(TAG, "Matter behaviours:");
  if (this->led_enabled_)
    ESP_LOGCONFIG(TAG, "  Status LED: commissioned = %s",
                  this->commissioned_on_ ? "on" : "off");
#ifdef USE_BINARY_SENSOR
  if (this->pairing_sensor_ != nullptr) {
    ESP_LOGCONFIG(TAG, "  Pairing button: hold %us to open the commissioning window (%us)",
                  (unsigned) (this->open_window_hold_ms_ / 1000),
                  (unsigned) this->open_window_timeout_s_);
    if (this->factory_reset_hold_ms_ != 0)
      ESP_LOGCONFIG(TAG, "  Pairing button: hold %us to factory reset",
                    (unsigned) (this->factory_reset_hold_ms_ / 1000));
    else
      ESP_LOGCONFIG(TAG, "  Pairing button: factory reset gesture disabled");
  }
#endif
}

void MatterBehaviours::write_led_(bool on) {
  if (this->led_last_ == (on ? 1 : 0))
    return;
  this->led_last_ = on ? 1 : 0;
#ifdef USE_OUTPUT
  if (this->led_output_ != nullptr) {
    if (on)
      this->led_output_->turn_on();
    else
      this->led_output_->turn_off();
  }
#endif
#ifdef USE_LIGHT
  if (this->led_light_ != nullptr) {
    auto call = on ? this->led_light_->turn_on() : this->led_light_->turn_off();
    call.set_transition_length(uint32_t{0});
    call.set_save(false);
    call.perform();
  }
#endif
}

void MatterBehaviours::loop() {
  const uint32_t now = millis();
#ifdef USE_BINARY_SENSOR
  if (this->pairing_sensor_ != nullptr && !this->resetting_) {
    const bool pressed =
        this->pairing_sensor_->has_state() && this->pairing_sensor_->state;
    switch (this->pairing_.update(now, pressed)) {
    case PairingHold::Event::OPEN_WINDOW:
      ESP_LOGI(TAG, "Pairing button: opening the commissioning window");
      MatterCommissioning::instance().open_window(this->open_window_timeout_s_);
      this->confirm_active_ = true;
      this->confirm_until_ = now + LED_CONFIRM_MS;
      break;
    case PairingHold::Event::FACTORY_RESET:
      ESP_LOGW(TAG, "Pairing button: factory reset");
      // Keeps strobing until the reboot; the reset itself runs on the CHIP task.
      this->resetting_ = true;
      MatterCommissioning::instance().factory_reset();
      break;
    default:
      break;
    }
  }
#endif
  if (this->led_enabled_)
    this->update_led_(now);
}

void MatterBehaviours::update_led_(uint32_t now) {
  LedPattern wanted;
  if (this->resetting_) {
    wanted = LedPattern::STROBE;
  } else if (this->confirm_active_ &&
             static_cast<int32_t>(this->confirm_until_ - now) > 0) {
    wanted = LedPattern::CONFIRM;
  } else {
    this->confirm_active_ = false;
    if (!pairing_overlay(this->pairing_.stage(), wanted)) {
      const CommissioningState &s = MatterCommissioning::instance().state();
      wanted = status_pattern(s.valid, s.error, s.window_open, s.fabric_count,
                              this->commissioned_on_);
    }
  }
  if (wanted != this->led_pattern_) {
    this->led_pattern_ = wanted;
    this->led_pattern_since_ = now;
  }
  this->write_led_(led_level(wanted, now - this->led_pattern_since_));
}

} // namespace esphome::matter

#endif // USE_MATTER
