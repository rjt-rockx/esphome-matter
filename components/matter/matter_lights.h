#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_LIGHT)

#include "esphome/components/light/light_color_values.h"
#include "esphome/components/light/light_state.h"
#include "esphome/core/optional.h"
#include "matter_endpoints.h"

#include <esp_matter.h>

#include <cstdint>

namespace esphome::matter {

struct MatterColorTemperatureRange {
  uint16_t min_mireds;
  uint16_t max_mireds;
};

struct MatterLightCapabilities {
  bool has_level{false};
  // ColorControl attribute groups that exist on the endpoint.
  bool has_xy{false};
  bool has_hs{false};
  bool has_enhanced_hue{false};
  bool has_color{false}; // has_xy || has_hs
  bool has_enhanced_color_mode{false};
  optional<MatterColorTemperatureRange> color_temperature_range;
};

// The colour attributes that describe the active Matter ColorMode.
struct MatterLightColorUpdate {
  enum class Kind : uint8_t {
    XY,          // first = CurrentX, second = CurrentY
    HS,          // first = CurrentHue, second = CurrentSaturation
    ENHANCED_HS, // first = EnhancedCurrentHue, second = CurrentSaturation
    CT,          // first = ColorTemperatureMireds
  };
  Kind kind{Kind::XY};
  uint16_t first{0};
  uint16_t second{0};
};

// Matter changes that arrived since the last time they were applied to the
// ESPHome light. Merged so that OnOff, level and colour become one light call.
struct MatterLightPendingUpdate {
  optional<bool> on;
  optional<uint8_t> level;
  optional<MatterLightColorUpdate> color;
};

class MatterLightMapping : public MatterEndpointMappingBase,
                           public light::LightRemoteValuesListener {
public:
  MatterLightMapping(light::LightState *light, uint16_t endpoint_id);

  void on_light_remote_values_update() override;
  void initialize() override;

  void push_state_to_matter();

protected:
  bool has_attribute_(uint32_t cluster_id, uint32_t attribute_id) const;

  // Runs on the CHIP task.
  void schedule_color_read_();
  optional<MatterLightColorUpdate> read_color_update_();

  // Run on the ESPHome main loop.
  void queue_update_(MatterLightPendingUpdate update);
  void apply_pending_();
  bool add_color_to_call_(light::LightCall &call,
                          const MatterLightColorUpdate &color);

  light::LightState *light_;
  MatterLightCapabilities capabilities_;

  // Main loop only.
  MatterLightPendingUpdate pending_;
  bool apply_scheduled_{false};
  bool synchronizing_from_matter_{false};
  // Remote values right after the last change from Matter. Used to ignore the
  // echo of that change, also when ESPHome publishes it later (transitions).
  light::LightColorValues applied_values_;
  bool applied_values_valid_{false};
  uint32_t last_apply_ms_{0};
  bool last_apply_valid_{false};
  // Last colour that was reported to Matter, to avoid needlessly rewriting
  // ColorMode (which would switch a controller's HS mode back to XY).
  bool reported_color_valid_{false};
  bool reported_color_is_ct_{false};
  float reported_red_{0.0f};
  float reported_green_{0.0f};
  float reported_blue_{0.0f};
  float reported_color_temperature_{0.0f};

  // CHIP task only.
  bool color_update_pending_{false};
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_LIGHT
