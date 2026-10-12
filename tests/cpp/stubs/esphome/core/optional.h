#pragma once
// Host test stub: ESPHome's optional is std::optional here (same API for what the component uses).
#include <optional>

namespace esphome {
template<typename T> using optional = std::optional<T>;
inline constexpr std::nullopt_t nullopt = std::nullopt;
}  // namespace esphome
