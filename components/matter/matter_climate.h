#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_CLIMATE)

#include "esphome/components/climate/climate.h"
#include "matter_binding.h"
#include "matter_climate_logic.h"

#include <esp_matter.h>
#include <esp_matter_cluster.h>

#include <cstdint>

namespace esphome::matter {

// What the Matter Thermostat cluster looks like for a given ESPHome climate.
// Derived from the climate traits when the endpoint is built.
// The absolute setpoint limits (SetpointLimits, the climate's visual range) are
// the base class.
struct MatterClimateSpec : SetpointLimits {
  bool heating{false};
  bool cooling{false};
  bool auto_mode{false};
  bool two_point{false};
  bool has_action{false};
  uint8_t control_sequence{0}; // ControlSequenceOfOperationEnum
  int8_t dead_band{0};         // MinSetpointDeadBand (0.1 C), AutoMode only
};

// The Thermostat attributes mirrored from the climate (main loop capture).
struct MatterClimateState {
  uint8_t system_mode{0};
  esp_matter_attr_val_t local_temperature{};
  // Setpoints to write, already clamped to the absolute limits.
  bool write_heating{false};
  bool write_cooling{false};
  int16_t heating{0};
  int16_t cooling{0};
  bool has_running_state{false};
  uint16_t running_state{0};
  bool has_on_off{false};
  bool on{false};
};

// heating_only: the device type can only heat (Water Heater). Cooling modes
// of the climate are then not exposed.
MatterClimateSpec derive_climate_spec(climate::Climate *climate,
                                      bool prefer_cooling,
                                      bool heating_only = false);

// Called from the generated endpoint build callback: configure_ before the
// device type is added (fills the Thermostat cluster config including the
// feature flags), extend_ afterwards (creates the optional absolute limit and
// running state attributes).
void configure_climate_thermostat(
    esp_matter::cluster::thermostat::config_t &config,
    climate::Climate *climate, bool prefer_cooling,
    bool heating_only = false);
bool extend_climate_thermostat(esp_matter::endpoint_t *endpoint,
                               climate::Climate *climate, bool prefer_cooling,
                               bool heating_only = false);

// Binds an ESPHome climate to the Thermostat (and OnOff) cluster of an
// endpoint, through the binding framework (docs/dev/binding-framework.md).
//
// Matter -> ESPHome: SystemMode, the two occupied setpoints and OnOff are
// watched; a write is merged per field with the others of the same burst and
// applied as climate calls in the order mode, on/off, heating, cooling. A
// request the climate refuses (unsupported mode, a setpoint that isn't the
// active target) or clamps ends with the attribute reconciled back to the
// climate's own state; the binding waits (settle) for the climate to publish
// first, because a climate may take a moment to confirm.
//
// ESPHome -> Matter: every climate state callback requests a push; the writes
// are inside a PushScope, so the cluster's own follow-up changes (a shifted
// setpoint keeping the dead band) are not requests.
class MatterClimateMapping
    : public MatterBinding<MatterClimateState, MatterClimateInbound> {
public:
  // How long to wait for the climate's own publish after applying a request.
  static constexpr uint16_t SETTLE_MS = 500;

  MatterClimateMapping(climate::Climate *climate, uint16_t endpoint_id,
                       bool prefer_cooling, bool heating_only = false);

protected:
  void setup_() override;
  MatterClimateState capture_() override;
  void write_matter_(const MatterClimateState &s) override;
  void merge_inbound_(MatterClimateInbound &acc,
                      const MatterClimateInbound &in) override;
  void apply_inbound_(const MatterClimateInbound &in) override;
  void on_attribute_(uint32_t cluster, uint32_t attribute,
                     const esp_matter_attr_val_t &v) override;

  void apply_system_mode_(uint8_t system_mode);
  void apply_setpoint_(bool heating, int16_t centi);
  void apply_on_off_(bool on);

  climate::Climate *climate_;
  bool prefer_cooling_;
  bool heating_only_;
  MatterClimateSpec spec_;
  bool has_on_off_{false};
  bool has_running_state_{false};
  climate::ClimateMode last_active_mode_{climate::CLIMATE_MODE_OFF};
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_CLIMATE
