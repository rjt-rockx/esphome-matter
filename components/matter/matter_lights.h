#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_LIGHT)

#include "esphome/components/light/light_color_values.h"
#include "esphome/components/light/light_state.h"
#include "esphome/core/optional.h"
#include "matter_light_binding.h"

#include <esp_matter.h>

#include <atomic>
#include <cstdint>

namespace chip::TLV {
class TLVReader;
}

namespace esphome::matter {

// Adds the Extended Color Light device type to an endpoint like
// esp_matter::endpoint::extended_color_light::add(), but the ColorTemperature
// feature only when the light can show a color temperature. A light that only
// does RGB would otherwise advertise a color temperature range it can't
// follow. The other ColorControl features are added by the caller.
esp_err_t add_extended_color_light(
    esp_matter::endpoint_t *endpoint,
    esp_matter::endpoint::extended_color_light::config_t *config,
    light::LightState *light);

// Binds an ESPHome light to the OnOff, LevelControl and ColorControl clusters of
// an endpoint, through the binding framework (matter_light_binding.h holds the
// state machine; this class supplies the entity hooks, the decoding of the
// Matter commands and the CHIP cluster calls).
class MatterLightMapping : public MatterLightBinding,
                           public light::LightRemoteValuesListener {
public:
  MatterLightMapping(light::LightState *light, uint16_t endpoint_id,
                     const char *color_loop_effect = nullptr);

  void on_light_remote_values_update() override {
    this->on_entity_published_();
  }

  // Called on the CHIP task, before CHIP executes the command.
  void handle_command(uint32_t cluster_id, uint32_t command_id,
                      chip::TLV::TLVReader &reader);

protected:
  bool has_attribute_(uint32_t cluster_id, uint32_t attribute_id) const;

  // Runs on the CHIP task.
  bool decode_level_command_(uint32_t command_id, chip::TLV::TLVReader &reader,
                             MatterLightCommand &command);
  bool decode_color_command_(uint32_t command_id, chip::TLV::TLVReader &reader,
                             MatterLightCommand &command);
  bool decode_on_off_command_(uint32_t command_id, MatterLightCommand &command);
  bool execute_if_off_(uint32_t cluster_id, uint32_t options_attribute_id,
                       uint8_t options_mask, uint8_t options_override);

  // MatterLightBinding hooks. Main loop unless noted.
  LightEntityView entity_view_() override;
  void perform_(const LightRequest &request) override;
  void start_color_loop_effect_() override;
  void stop_color_loop_effect_hook_() override;
  bool color_loop_effect_running_() override;
  void remember_applied_entity_() override;
  bool remote_matches_applied_() override;
  void probe_capabilities_() override;
  void listen_entity_() override;
  void register_commands_() override;
  // CHIP task.
  void chip_stop_light_transition_(uint8_t level) override;
  void chip_stop_color_transitions_() override;
  void deactivate_chip_color_loop_() override;

  light::LightState *light_;
  // Name of the ESPHome effect that runs while a Matter color loop is active.
  const char *color_loop_effect_;
  light::LightColorValues applied_values_;
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_LIGHT
