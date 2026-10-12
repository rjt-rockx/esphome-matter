#pragma once

// Pure logic of the Fan Control Step command; no ESPHome or Matter dependencies
// so that it can be compiled and tested on the host (tests/unit/test_fan_step.py).

namespace esphome::matter {

// Target level after a Step command.
//
// Levels are 0 (off) to max_level. `lowest_off` selects whether stepping down
// can reach 0 (Off); when it is false the lowest level is 1. `wrap` makes the
// step continue from the highest level to the lowest one and the other way
// around; without it the level stays at the end.
//
// A fan that is off and stepped down with lowest_off == false stays off unless
// wrap is set: Off is not part of the stepped range then, so there is no lower
// level to go to, and the command must not turn the fan on.
inline int fan_step_target(int current, int max_level, bool increase, bool wrap,
                           bool lowest_off) {
  if (max_level < 1)
    return current;
  if (current < 0)
    current = 0;
  if (current > max_level)
    current = max_level;
  const int lowest = lowest_off ? 0 : 1;
  if (increase) {
    if (current >= max_level)
      return wrap ? lowest : max_level;
    return current + 1;
  }
  if (current <= lowest) {
    if (current == 0 && !lowest_off)
      return wrap ? max_level : 0;
    return wrap ? max_level : lowest;
  }
  return current - 1;
}

} // namespace esphome::matter
