#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_LIGHT)

#include "esphome/components/light/light_color_values.h"
#include "esphome/components/light/light_state.h"
#include "esphome/core/optional.h"
#include "matter_endpoints.h"

#include <esp_matter.h>

#include <atomic>
#include <cstdint>

namespace chip::TLV {
class TLVReader;
}

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
  // The values are authoritative (end of a commanded transition, restore after
  // a Stop command): apply them instantly, also when they look unchanged
  // because the light is still fading towards a different target.
  bool settle{false};
};

// A Matter command that moves the light to a target, decoded on the CHIP task
// before CHIP executes it. Applied as one ESPHome light call with the real
// transition length instead of following CHIP's intermediate steps.
struct MatterLightCommand {
  optional<bool> on;
  optional<uint8_t> level;
  // Apply `level` only when this command turns a light that is off on (On
  // command with OnLevel).
  bool level_if_turning_on{false};
  optional<MatterLightColorUpdate> color;
  uint32_t transition_ms{0};
  // How long CHIP itself takes to reach the target. That can be longer than
  // transition_ms (On and Off use OnTransitionTime and OffTransitionTime for
  // the light, CHIP only knows OnOffTransitionTime), and CHIP's steps are
  // ignored until it is done.
  uint32_t chip_transition_ms{0};
  // ColorLoopSet starts or stops the color loop effect of the light.
  enum class ColorLoop : uint8_t { NONE, START, STOP };
  ColorLoop color_loop{ColorLoop::NONE};
  // Attribute groups whose CHIP-driven intermediate writes are ignored while
  // the transition runs.
  bool light_group{false}; // OnOff and CurrentLevel
  bool color_group{false}; // ColorControl attributes
  // Stop commands: CHIP stops where it is, so the light does as well.
  bool stop_light{false};
  bool stop_color{false};
};

// Adds the Extended Color Light device type to an endpoint like
// esp_matter::endpoint::extended_color_light::add(), but the ColorTemperature
// feature only when the light can show a color temperature. A light that only
// does RGB would otherwise advertise a color temperature range it can't
// follow. The other ColorControl features are added by the caller.
esp_err_t add_extended_color_light(
    esp_matter::endpoint_t *endpoint,
    esp_matter::endpoint::extended_color_light::config_t *config,
    light::LightState *light);

class MatterLightMapping : public MatterEndpointMappingBase,
                           public light::LightRemoteValuesListener {
public:
  MatterLightMapping(light::LightState *light, uint16_t endpoint_id,
                     const char *color_loop_effect = nullptr);

  void on_light_remote_values_update() override;
  void initialize() override;

  void push_state_to_matter();

  // Called on the CHIP task, before CHIP executes the command.
  void handle_command(uint32_t cluster_id, uint32_t command_id,
                      chip::TLV::TLVReader &reader);

protected:
  bool has_attribute_(uint32_t cluster_id, uint32_t attribute_id) const;

  // Runs on the CHIP task.
  void schedule_color_read_();
  optional<MatterLightColorUpdate> read_color_update_();

  void schedule_full_read_(bool settle, bool light, bool color);
  MatterLightPendingUpdate read_state_();
  bool decode_level_command_(uint32_t command_id, chip::TLV::TLVReader &reader,
                             MatterLightCommand &command);
  bool decode_color_command_(uint32_t command_id, chip::TLV::TLVReader &reader,
                             MatterLightCommand &command);
  bool decode_on_off_command_(uint32_t command_id, MatterLightCommand &command);
  bool execute_if_off_(uint32_t cluster_id, uint32_t options_attribute_id,
                       uint8_t options_mask, uint8_t options_override);
  void register_commands_();

  // Color loop (see docs/lights.md).
  bool color_loop_effect_running_() const;
  void stop_color_loop_effect_();
  void watch_color_loop_();
  void check_color_loop_();
  // Runs on the CHIP task.
  void deactivate_chip_color_loop_();
  // A change that came from ESPHome ends the windows of Matter commands.
  void end_windows_for_local_change_();
  void settle_when_idle_(bool light, bool color);

  // Run on the ESPHome main loop.
  void queue_update_(MatterLightPendingUpdate update);
  void apply_pending_();
  void apply_command_(const MatterLightCommand &command);
  void finish_command_windows_();
  bool light_group_suppressed_(uint32_t now) const;
  bool color_group_suppressed_(uint32_t now) const;
  void remember_applied_values_();
  bool add_color_to_call_(light::LightCall &call,
                          const MatterLightColorUpdate &color, bool force);

  light::LightState *light_;
  MatterLightCapabilities capabilities_;
  // Name of the ESPHome effect that runs while a Matter color loop is active.
  const char *color_loop_effect_;
  // The color loop effect is running on the light: CHIP walks the hue
  // attributes in the background, those steps are not applied to the light
  // and the color of the light is not reported. Main loop only (set only when
  // the effect really started).
  bool color_loop_active_{false};
  // A ColorLoopSet that activates the loop was accepted and not ended since.
  // CHIP's loop runs (ColorLoopActive) whether or not the effect started.
  // Written on the CHIP task when the command is decoded.
  std::atomic<bool> color_loop_requested_{false};
  uint32_t settle_retries_{0};

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
  // While a transition that came from a Matter command runs, CHIP's
  // intermediate attribute writes for that group are ignored. Matter still
  // reports them. The end of the window is set by the command's transition
  // time plus a margin, after which the light is synchronized with the final
  // attribute values.
  bool light_window_active_{false};
  uint32_t light_window_until_ms_{0};
  bool color_window_active_{false};
  uint32_t color_window_until_ms_{0};
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
