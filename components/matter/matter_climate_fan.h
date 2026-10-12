#pragma once

#include "esphome/core/defines.h"
// Compiled only when a Fan Control cluster is bound to a climate (the generated
// code defines USE_MATTER_CLIMATE_FAN), so other builds don't need the cluster.
#if defined(USE_MATTER) && defined(USE_CLIMATE) && defined(USE_MATTER_CLIMATE_FAN)

#include "esphome/components/climate/climate.h"
#include "matter_endpoints.h"

#include <app/clusters/fan-control-server/fan-control-delegate.h>
#include <esp_matter.h>

#include <cstdint>

namespace esphome::matter {

// Fan Control cluster of a room air conditioner bound to the fan and swing
// modes of an ESPHome climate (`clusters: fan_control: {climate_id: ...}`).
//
//   climate fan mode   Matter FanMode
//   OFF                Off
//   LOW, QUIET         Low
//   MEDIUM, MIDDLE     Medium
//   HIGH, ON           High
//   AUTO               Auto (Auto feature)
//
// FOCUS, DIFFUSE and custom fan modes have no Matter equivalent and are not
// exposed. The climate swing modes drive RockSetting (VERTICAL = UpDown,
// HORIZONTAL = LeftRight, BOTH = both bits).

// FanModeSequence (Matter enum value) for the fan modes the climate supports.
uint8_t climate_fan_mode_sequence(climate::Climate *climate);

// Adds the Auto and Rocking features that follow from the climate traits, plus
// the Step command when requested. Used by the generated endpoint build
// callback.
esp_err_t add_climate_fan_features(esp_matter::cluster_t *cluster,
                                   climate::Climate *climate, bool step);

class MatterClimateFanMapping
    : public MatterEndpointMappingBase,
      public chip::app::Clusters::FanControl::Delegate {
public:
  MatterClimateFanMapping(climate::Climate *climate, uint16_t endpoint_id);

  void initialize() override;

  // Must be called on the ESPHome main loop.
  void push_state_to_matter();

  // FanControl::Delegate. Called on the CHIP task.
  chip::Protocols::InteractionModel::Status
  HandleStep(chip::app::Clusters::FanControl::StepDirectionEnum direction,
             bool wrap, bool lowest_off) override;
  void OnFanDriveStateChanged(
      const chip::app::Clusters::FanControl::FanDriveState &state) override;
  void OnRockSettingChanged(
      chip::BitMask<chip::app::Clusters::FanControl::RockBitmap> value)
      override;

protected:
  void apply_fan_mode_(uint8_t fan_mode);
  void apply_rock_(uint8_t rock);

  climate::Climate *climate_;
  bool has_rocking_{false};
  bool has_step_{false};
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_CLIMATE && USE_MATTER_CLIMATE_FAN
