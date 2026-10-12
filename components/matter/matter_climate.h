#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_CLIMATE)

#include "esphome/components/climate/climate.h"
#include "matter_endpoints.h"

#include <esp_matter.h>
#include <esp_matter_cluster.h>

#include <cstdint>

namespace esphome::matter {

// What the Matter Thermostat cluster looks like for a given ESPHome climate.
// Derived from the climate traits when the endpoint is built.
struct MatterClimateSpec {
  bool heating{false};
  bool cooling{false};
  bool auto_mode{false};
  bool two_point{false};
  bool has_action{false};
  uint8_t control_sequence{0}; // ControlSequenceOfOperationEnum
  int8_t dead_band{0};         // MinSetpointDeadBand (0.1 C), AutoMode only
  // Absolute setpoint limits (0.01 C): the climate's visual range. With
  // AutoMode the heating maximum stops a dead band below the cooling maximum
  // and the cooling minimum starts a dead band above the heating minimum, as
  // the Thermostat cluster's write validation requires.
  int16_t heat_min{1000};
  int16_t heat_max{3000};
  int16_t cool_min{1000};
  int16_t cool_max{3000};

  int16_t clamp_heating(int16_t centi) const;
  int16_t clamp_cooling(int16_t centi) const;
};

MatterClimateSpec derive_climate_spec(climate::Climate *climate,
                                      bool prefer_cooling);

// Called from the generated endpoint build callback: configure_ before the
// device type is added (fills the Thermostat cluster config including the
// feature flags), extend_ afterwards (creates the optional absolute limit and
// running state attributes).
void configure_climate_thermostat(
    esp_matter::cluster::thermostat::config_t &config,
    climate::Climate *climate, bool prefer_cooling);
bool extend_climate_thermostat(esp_matter::endpoint_t *endpoint,
                               climate::Climate *climate, bool prefer_cooling);

class MatterClimateMapping : public MatterEndpointMappingBase {
public:
  MatterClimateMapping(climate::Climate *climate, uint16_t endpoint_id,
                       bool prefer_cooling);

  void initialize() override;

  // Must be called on the ESPHome main loop.
  void push_state_to_matter();

protected:
  void apply_system_mode_(uint8_t system_mode);
  void apply_setpoint_(bool heating, int16_t centi);
  void apply_on_off_(bool on);

  climate::Climate *climate_;
  bool prefer_cooling_;
  MatterClimateSpec spec_;
  bool has_on_off_{false};
  bool has_running_state_{false};
  climate::ClimateMode last_active_mode_{climate::CLIMATE_MODE_OFF};
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_CLIMATE
