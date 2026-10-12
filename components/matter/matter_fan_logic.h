#pragma once

// Pure logic of the Fan Control binding (matter_fan.cpp): the state types that
// cross the main loop / CHIP task boundary, the merge of controller writes, the
// decision which settings to write back and the Wind preset mapping. No ESPHome
// or Matter dependencies, so it is compiled by the host tests
// (tests/cpp/test_hvac_bindings.cpp).

#include "matter_fan_step.h"

#include <algorithm>
#include <cstdint>
#include <string>

namespace esphome::matter {

// Snapshot of the Fan Control cluster state, taken on the CHIP task. Every
// controller write (FanMode, PercentSetting, SpeedSetting, RockSetting,
// AirflowDirection, Step) changes the same cluster state and cascades into the
// other settings, so the binding reads the settled state instead of using the
// written value.
struct MatterFanState {
  // False when the cluster is in Auto, which ESPHome can't represent.
  bool has_speed_info{false};
  bool off{true};
  uint8_t percent{0};
  uint8_t speed{0};
  bool has_rock{false};
  bool rocking{false};
  bool has_direction{false};
  bool reverse{false};
};

// A pending controller request: the settled cluster state and / or a
// WindSetting change. Merged on the CHIP task, applied on the main loop.
struct MatterFanInbound {
  bool has_settings{false};
  MatterFanState settings;
  bool has_wind{false};
  bool wind_natural{false};
  bool wind_sleep{false};
};

// A newer settings snapshot replaces the older one (it includes the cascade of
// every write before it); a WindSetting change is kept next to it.
inline void fan_merge_inbound(MatterFanInbound &acc, const MatterFanInbound &in) {
  if (in.has_settings) {
    acc.has_settings = true;
    acc.settings = in.settings;
  }
  if (in.has_wind) {
    acc.has_wind = true;
    acc.wind_natural = in.wind_natural;
    acc.wind_sleep = in.wind_sleep;
  }
}

// The ESPHome fan as mirrored into the cluster (main loop capture).
struct MatterFanOutbound {
  bool on{false};
  int speed{0};
  bool oscillating{false};
  bool reverse{false};
  // WindBitmap bits of the active preset mode.
  uint8_t wind{0};
  // The cluster was in Auto when the last controller request was applied and
  // the fan has not changed since: leave Auto alone (ESPHome has no Auto).
  bool keep_auto{false};
};

// The speed and percent CurrentX attributes for the fan.
struct MatterFanCurrent {
  uint8_t speed{0};
  uint8_t percent{0};
};

inline MatterFanCurrent fan_current(const MatterFanOutbound &fan, bool has_speed,
                                    int speed_count) {
  MatterFanCurrent c;
  if (fan.on && has_speed) {
    c.speed = static_cast<uint8_t>(std::clamp(fan.speed, 1, speed_count));
    c.percent = static_cast<uint8_t>(c.speed * 100 / speed_count);
  } else if (fan.on) {
    c.percent = 100;
  }
  return c;
}

// True when the cluster settings (FanMode / PercentSetting / SpeedSetting) have
// to be written to match the fan. When the cluster already maps to the fan state
// they are left alone: a percent-only fan keeps the 50 % a controller wrote, and
// a state echo does not renormalise the setting. A cluster in Auto only stays
// there while `keep_auto` holds.
inline bool fan_settings_need_write(const MatterFanState &cluster,
                                    const MatterFanOutbound &fan, bool has_speed,
                                    int speed_count) {
  if (!cluster.has_speed_info)
    return !fan.keep_auto;
  if (cluster.off == fan.on)
    return true;
  if (has_speed && fan.on &&
      cluster.speed != static_cast<uint8_t>(std::clamp(fan.speed, 1, speed_count)))
    return true;
  return false;
}

// What a settings request changes on the fan (main loop).
struct MatterFanCallPlan {
  bool changed{false};
  bool set_state{false};
  bool state{false};
  bool set_speed{false};
  int speed{0};
  bool set_oscillating{false};
  bool oscillating{false};
  bool set_direction{false};
  bool reverse{false};
};

inline MatterFanCallPlan fan_plan_settings(const MatterFanState &cluster,
                                           bool fan_on, int fan_speed,
                                           bool fan_oscillating, bool fan_reverse,
                                           bool has_speed) {
  MatterFanCallPlan plan;
  if (cluster.has_speed_info) {
    if (cluster.off) {
      // Never use set_speed(0), ESPHome clamps this to 1.
      if (fan_on) {
        plan.set_state = true;
        plan.state = false;
        plan.changed = true;
      }
    } else if (!fan_on || (has_speed && fan_speed != cluster.speed)) {
      plan.set_state = true;
      plan.state = true;
      // Always pass the speed explicitly, otherwise ESPHome turns on the fan at
      // the maximum speed.
      if (has_speed) {
        plan.set_speed = true;
        plan.speed = cluster.speed;
      }
      plan.changed = true;
    }
  }
  if (cluster.has_rock && fan_oscillating != cluster.rocking) {
    plan.set_oscillating = true;
    plan.oscillating = cluster.rocking;
    plan.changed = true;
  }
  if (cluster.has_direction && fan_reverse != cluster.reverse) {
    plan.set_direction = true;
    plan.reverse = cluster.reverse;
    plan.changed = true;
  }
  return plan;
}

// What a WindSetting request does to the preset mode (main loop). ESPHome
// presets are exclusive: with both bits set the one that is not active yet wins.
// Clearing the setting resets the preset the way ESPHome does for a manual speed
// change. Every outcome except Preset and ClearBySpeed leaves the fan alone; the
// binding then reconciles WindSetting back from the fan.
enum class MatterWindAction : uint8_t {
  None,         // nothing to do
  Preset,       // set_preset_mode(preset)
  ClearBySpeed, // set_speed(max(speed, 1)) clears the preset
  NoMatch,      // a requested bit has no preset mode
  FanOff,       // presets are only set on a running fan
  NoSpeed,      // can't clear: the fan has no speed control
};

struct MatterWindPlan {
  MatterWindAction action{MatterWindAction::None};
  std::string preset;
};

inline MatterWindPlan fan_plan_wind(bool natural, bool sleep,
                                    const std::string &current,
                                    const std::string &wind_natural,
                                    const std::string &wind_sleep, bool fan_on,
                                    bool fan_has_speed) {
  MatterWindPlan plan;
  std::string wanted;
  if (natural && sleep)
    wanted = (current == wind_natural) ? wind_sleep : wind_natural;
  else if (natural)
    wanted = wind_natural;
  else if (sleep)
    wanted = wind_sleep;
  if ((natural && wind_natural.empty()) || (sleep && wind_sleep.empty()) ||
      (natural && sleep && wanted.empty())) {
    plan.action = MatterWindAction::NoMatch;
    return plan;
  }
  if (wanted.empty() && current.empty())
    return plan;
  if (!wanted.empty()) {
    if (!fan_on) {
      plan.action = MatterWindAction::FanOff;
      return plan;
    }
    plan.action = MatterWindAction::Preset;
    plan.preset = wanted;
    return plan;
  }
  plan.action = fan_has_speed ? MatterWindAction::ClearBySpeed
                              : MatterWindAction::NoSpeed;
  return plan;
}

// WindBitmap bits (NaturalWind 1, SleepWind 2) of the active preset mode.
inline uint8_t fan_wind_bits(const std::string &preset,
                             const std::string &wind_natural,
                             const std::string &wind_sleep) {
  if (!preset.empty() && preset == wind_natural)
    return 0x1;
  if (!preset.empty() && preset == wind_sleep)
    return 0x2;
  return 0;
}

} // namespace esphome::matter
