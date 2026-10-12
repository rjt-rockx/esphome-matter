#pragma once

// Pure logic of the commissioning behaviours (status LED patterns and the
// pairing button hold detection). No ESPHome or CHIP dependencies so that it
// can be unit tested on the host (tests/unit/test_behaviours_logic.py).

#include <cstdint>

namespace esphome::matter {

enum class LedPattern : uint8_t {
  OFF,
  ON,
  SLOW_BLINK,   // 1 Hz: uncommissioned, window closed
  FAST_BLINK,   // 4 Hz: commissioning window open
  DOUBLE_BLINK, // two short flashes, pause: error
  MEDIUM_BLINK, // 2 Hz: pairing button held long enough to open the window
  STROBE,       // 10 Hz: pairing button held long enough to factory reset
  CONFIRM,      // three quick flashes: the window open request was accepted
};

// Level of `pattern` `t_ms` milliseconds after it started.
inline bool led_level(LedPattern pattern, uint32_t t_ms) {
  switch (pattern) {
  case LedPattern::OFF:
    return false;
  case LedPattern::ON:
    return true;
  case LedPattern::SLOW_BLINK:
    return (t_ms % 1000) < 500;
  case LedPattern::FAST_BLINK:
    return (t_ms % 250) < 125;
  case LedPattern::MEDIUM_BLINK:
    return (t_ms % 500) < 250;
  case LedPattern::STROBE:
    return (t_ms % 100) < 50;
  case LedPattern::DOUBLE_BLINK: {
    const uint32_t p = t_ms % 1400;
    return p < 150 || (p >= 300 && p < 450);
  }
  case LedPattern::CONFIRM:
    return (t_ms % 200) < 100;
  }
  return false;
}

// Length of the CONFIRM overlay (three flashes).
constexpr uint32_t LED_CONFIRM_MS = 600;

// What the LED shows for a commissioning state. `commissioned_on` selects
// whether a commissioned device keeps the LED on or off.
inline LedPattern status_pattern(bool valid, bool error, bool window_open,
                                 uint8_t fabric_count, bool commissioned_on) {
  if (!valid)
    return LedPattern::OFF; // the first snapshot has not arrived yet
  if (error)
    return LedPattern::DOUBLE_BLINK;
  if (window_open)
    return LedPattern::FAST_BLINK;
  if (fabric_count == 0)
    return LedPattern::SLOW_BLINK;
  return commissioned_on ? LedPattern::ON : LedPattern::OFF;
}

// Detects "hold the pairing button for N seconds". The action happens on
// RELEASE and the LED shows which stage is armed meanwhile:
//   released before open_hold:             nothing happens
//   released between open_hold and reset:  open the commissioning window
//   released after reset_hold:             factory reset
// A button held for CANCEL_AFTER_MS beyond the last threshold is treated as
// stuck: nothing happens on release.
class PairingHold {
public:
  enum class Stage : uint8_t { IDLE, PRESSED, OPEN_ARMED, RESET_ARMED };
  enum class Event : uint8_t { NONE, OPEN_WINDOW, FACTORY_RESET };

  static constexpr uint32_t CANCEL_AFTER_MS = 15000;

  // reset_hold_ms == 0 disables the factory reset stage.
  PairingHold(uint32_t open_hold_ms, uint32_t reset_hold_ms)
      : open_hold_ms_(open_hold_ms), reset_hold_ms_(reset_hold_ms) {}

  // Call from the main loop with the current button state. A button that is
  // already down on the first call (held during boot, stuck, a floating pin)
  // is ignored until it was released once.
  Event update(uint32_t now_ms, bool pressed) {
    if (!this->initialised_) {
      this->initialised_ = true;
      this->blocked_ = pressed;
      return Event::NONE;
    }
    if (this->blocked_) {
      if (!pressed)
        this->blocked_ = false;
      return Event::NONE;
    }
    if (pressed) {
      if (!this->down_) {
        this->down_ = true;
        this->down_since_ = now_ms;
      }
      const uint32_t held = now_ms - this->down_since_;
      const uint32_t last =
          this->reset_hold_ms_ != 0 ? this->reset_hold_ms_ : this->open_hold_ms_;
      if (held >= last + CANCEL_AFTER_MS) {
        this->down_ = false;
        this->blocked_ = true;
        this->stage_ = Stage::IDLE;
      } else if (this->reset_hold_ms_ != 0 && held >= this->reset_hold_ms_) {
        this->stage_ = Stage::RESET_ARMED;
      } else if (held >= this->open_hold_ms_) {
        this->stage_ = Stage::OPEN_ARMED;
      } else {
        this->stage_ = Stage::PRESSED;
      }
      return Event::NONE;
    }
    if (!this->down_)
      return Event::NONE;
    const Stage was = this->stage_;
    this->down_ = false;
    this->stage_ = Stage::IDLE;
    if (was == Stage::RESET_ARMED)
      return Event::FACTORY_RESET;
    if (was == Stage::OPEN_ARMED)
      return Event::OPEN_WINDOW;
    return Event::NONE;
  }

  Stage stage() const { return this->stage_; }

private:
  uint32_t open_hold_ms_;
  uint32_t reset_hold_ms_;
  uint32_t down_since_{0};
  bool initialised_{false};
  bool blocked_{false};
  bool down_{false};
  Stage stage_{Stage::IDLE};
};

// LED overlay for the pairing stage. Returns false when there is none (show
// the status pattern).
inline bool pairing_overlay(PairingHold::Stage stage, LedPattern &out) {
  switch (stage) {
  case PairingHold::Stage::OPEN_ARMED:
    out = LedPattern::MEDIUM_BLINK;
    return true;
  case PairingHold::Stage::RESET_ARMED:
    out = LedPattern::STROBE;
    return true;
  default:
    return false;
  }
}

} // namespace esphome::matter
