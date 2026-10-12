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
// The climate fan modes are ranked low (LOW, QUIET), medium (MEDIUM, MIDDLE)
// and high (HIGH, ON); the ones the climate supports become the Matter Low /
// Medium / High modes of the FanModeSequence, so the sequence only contains
// modes the climate can do:
//   3 or more ranks: Off, Low, Medium, High   2 ranks: Off, Low, High
//   1 rank:          Off, High                (+ Auto when the climate has AUTO)
// A climate fan mode that is not one of those (QUIET next to LOW, ...) is
// reported as the nearest supported one. FOCUS, DIFFUSE and custom fan modes
// are not exposed.
//
// Off needs a real action: with a FAN_OFF fan mode that is used, otherwise
// Off turns the climate itself off (mode OFF), and a climate in mode OFF with
// no FAN_OFF fan mode reports FanMode Off.
//
// The climate swing modes drive RockSetting (VERTICAL = UpDown,
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
  // force: also report an unchanged state (to revert a rejected request).
  void push_state_(bool force);

  climate::Climate *climate_;
  bool has_rocking_{false};
  bool has_step_{false};
  // True while push_state_ writes the cluster (CHIP task only). The delegate
  // callbacks run synchronously inside those writes, so callbacks seen while
  // it is set are echoes of our own report, not controller requests.
  bool pushing_{false};
  // Last state reported (main loop), to skip pushes when nothing changed.
  bool pushed_{false};
  bool last_has_mode_{false};
  uint8_t last_mode_{0};
  uint8_t last_rock_{0};
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_CLIMATE && USE_MATTER_CLIMATE_FAN
