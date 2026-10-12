#pragma once

// Pure logic of the climate bindings (matter_climate.cpp, matter_climate_fan.cpp):
// the request types that cross from the CHIP task to the main loop, how bursts
// of controller writes merge, the setpoint limits and the RockSetting <-> swing
// mode mapping. No ESPHome or Matter dependencies, so it is compiled by the host
// tests (tests/cpp/test_hvac_bindings.cpp).

#include <algorithm>
#include <cstdint>

namespace esphome::matter {

// ---------------------------------------------------------------------------
// Thermostat
// ---------------------------------------------------------------------------

// Absolute setpoint limits (0.01 C). With AutoMode the heating maximum stops a
// dead band below the cooling maximum and the cooling minimum starts a dead band
// above the heating minimum, as the Thermostat cluster's write validation
// requires.
struct SetpointLimits {
  int16_t heat_min{1000};
  int16_t heat_max{3000};
  int16_t cool_min{1000};
  int16_t cool_max{3000};

  int16_t clamp_heating(int16_t centi) const {
    return std::clamp(centi, this->heat_min, this->heat_max);
  }
  int16_t clamp_cooling(int16_t centi) const {
    return std::clamp(centi, this->cool_min, this->cool_max);
  }
};

// Controller requests of the Thermostat endpoint. SystemMode, the two
// setpoints and OnOff arrive as separate attribute writes; a burst (a Matter
// client sets mode and setpoint back to back) must not lose one, so every field
// is kept on its own and the newest value of a field wins.
struct MatterClimateInbound {
  bool has_mode{false};
  uint8_t system_mode{0};
  bool has_heating{false};
  int16_t heating_centi{0};
  bool has_cooling{false};
  int16_t cooling_centi{0};
  bool has_on_off{false};
  bool on{false};
};

inline void climate_merge_inbound(MatterClimateInbound &acc,
                                  const MatterClimateInbound &in) {
  if (in.has_mode) {
    acc.has_mode = true;
    acc.system_mode = in.system_mode;
  }
  if (in.has_heating) {
    acc.has_heating = true;
    acc.heating_centi = in.heating_centi;
  }
  if (in.has_cooling) {
    acc.has_cooling = true;
    acc.cooling_centi = in.cooling_centi;
  }
  if (in.has_on_off) {
    acc.has_on_off = true;
    acc.on = in.on;
  }
}

// Which target a single point climate's setpoint belongs to in its current mode.
enum class SetpointTarget : uint8_t {
  Heat,    // heat mode: the heating setpoint is the target
  Cool,    // cool mode: the cooling setpoint is the target
  None,    // heat/cool and auto: the target isn't adjustable
  Neutral, // any other mode: whichever the climate supports alone
};

// Whether a single point climate takes a controller write of the heating
// (`heating`) or cooling setpoint now. A refused write is not applied; the
// binding reconciles the attribute back to the climate's own state.
inline bool climate_accepts_setpoint(bool heating, SetpointTarget target,
                                     bool spec_heating, bool spec_cooling) {
  switch (target) {
  case SetpointTarget::Heat:
    return heating;
  case SetpointTarget::Cool:
    return !heating;
  case SetpointTarget::None:
    return false;
  case SetpointTarget::Neutral:
  default:
    return heating ? !spec_cooling : !spec_heating;
  }
}

// ---------------------------------------------------------------------------
// Fan Control of a climate: RockSetting <-> swing mode
// ---------------------------------------------------------------------------

constexpr uint8_t ROCK_BIT_LEFT_RIGHT = 0x1;
constexpr uint8_t ROCK_BIT_UP_DOWN = 0x2;

enum class SwingKind : uint8_t { Off, Vertical, Horizontal, Both };

// Which swing modes the climate supports (bit per SwingKind, Off is implicit).
constexpr uint8_t swing_bit(SwingKind kind) {
  return static_cast<uint8_t>(1u << static_cast<uint8_t>(kind));
}

// RockSupport of the cluster for the climate's swing modes.
inline uint8_t rock_support_of(uint8_t swing_support) {
  bool both = (swing_support & swing_bit(SwingKind::Both)) != 0;
  uint8_t support = 0;
  if (both || (swing_support & swing_bit(SwingKind::Horizontal)))
    support |= ROCK_BIT_LEFT_RIGHT;
  if (both || (swing_support & swing_bit(SwingKind::Vertical)))
    support |= ROCK_BIT_UP_DOWN;
  return support;
}

inline uint8_t swing_to_rock(SwingKind kind) {
  switch (kind) {
  case SwingKind::Both:
    return ROCK_BIT_LEFT_RIGHT | ROCK_BIT_UP_DOWN;
  case SwingKind::Vertical:
    return ROCK_BIT_UP_DOWN;
  case SwingKind::Horizontal:
    return ROCK_BIT_LEFT_RIGHT;
  default:
    return 0;
  }
}

// The swing mode for a RockSetting; false if the climate can't do it.
inline bool rock_to_swing(uint8_t rock, uint8_t swing_support, SwingKind &out) {
  if (rock == 0) {
    out = SwingKind::Off;
    return true;
  }
  SwingKind exact = rock == (ROCK_BIT_LEFT_RIGHT | ROCK_BIT_UP_DOWN)
                        ? SwingKind::Both
                        : (rock == ROCK_BIT_UP_DOWN ? SwingKind::Vertical
                                                    : SwingKind::Horizontal);
  if (swing_support & swing_bit(exact)) {
    out = exact;
    return true;
  }
  // A climate that only swings both ways takes any rock request.
  if (swing_support & swing_bit(SwingKind::Both)) {
    out = SwingKind::Both;
    return true;
  }
  return false;
}

// Controller requests of the climate's Fan Control cluster: FanMode (also from
// Step) and RockSetting arrive separately and are kept per field.
struct MatterClimateFanInbound {
  bool has_mode{false};
  uint8_t mode{0};
  bool has_rock{false};
  uint8_t rock{0};
};

inline void climate_fan_merge_inbound(MatterClimateFanInbound &acc,
                                      const MatterClimateFanInbound &in) {
  if (in.has_mode) {
    acc.has_mode = true;
    acc.mode = in.mode;
  }
  if (in.has_rock) {
    acc.has_rock = true;
    acc.rock = in.rock;
  }
}

} // namespace esphome::matter
