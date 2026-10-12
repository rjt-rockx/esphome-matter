#pragma once

#include "esphome/core/defines.h"
// Compiled only when a Fan Control cluster is bound to a climate (the generated
// code defines USE_MATTER_CLIMATE_FAN), so other builds don't need the cluster.
#if defined(USE_MATTER) && defined(USE_CLIMATE) && defined(USE_MATTER_CLIMATE_FAN)

#include "esphome/components/climate/climate.h"
#include "matter_binding.h"
#include "matter_climate_logic.h"

#include <app/clusters/fan-control-server/fan-control-delegate.h>
#include <esp_matter.h>

#include <cstdint>
#include <vector>

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

// What is mirrored into the cluster: FanMode (and the PercentCurrent that goes
// with it) and RockSetting.
struct MatterClimateFanState {
  bool has_mode{false};
  uint8_t mode{0};
  bool has_rock{false};
  uint8_t rock{0};
};

// Binding framework implementation (docs/dev/binding-framework.md).
//
// Matter -> ESPHome: the delegate callbacks (FanMode changes, which includes
// the Step command, and RockSetting changes) run on the CHIP task and submit
// the request; the main loop applies it to the climate. A request the climate
// can't take is dropped, and the reconcile after the apply puts the settled
// state back. The pushes of this binding are echoes (PushScope).
//
// ESPHome -> Matter: every climate state callback requests a push (coalesced);
// after a controller request the binding waits for the climate's own publish.
class MatterClimateFanMapping
    : public MatterBinding<MatterClimateFanState, MatterClimateFanInbound>,
      public chip::app::Clusters::FanControl::Delegate {
public:
  // How long to wait for the climate's own publish after applying a request.
  static constexpr uint16_t SETTLE_MS = 300;

  MatterClimateFanMapping(climate::Climate *climate, uint16_t endpoint_id);

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
  void setup_() override;
  MatterClimateFanState capture_() override;
  void write_matter_(const MatterClimateFanState &s) override;
  void merge_inbound_(MatterClimateFanInbound &acc,
                      const MatterClimateFanInbound &in) override;
  void apply_inbound_(const MatterClimateFanInbound &in) override;

  void apply_fan_mode_(uint8_t fan_mode);
  void apply_rock_(uint8_t rock);

  climate::Climate *climate_;
  bool has_rocking_{false};
  bool has_step_{false};
  // The Step levels (Off, then the sequence's modes, lowest first), built at
  // setup so the CHIP task doesn't read the climate.
  std::vector<uint8_t> step_levels_;
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_CLIMATE && USE_MATTER_CLIMATE_FAN
