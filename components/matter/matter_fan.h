#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_FAN)

#include "esphome/components/fan/fan.h"
#include "esphome/components/fan/fan_traits.h"
#include "matter_binding.h"
#include "matter_fan_logic.h"

#include <app/clusters/fan-control-server/fan-control-delegate.h>
#include <esp_matter.h>

#include <cstdint>
#include <string>

namespace esphome::matter {

// Chooses a FanModeSequence (Matter enum value) that matches the ESPHome fan
// traits. Used by the generated endpoint build callback.
uint8_t fan_mode_sequence(const fan::FanTraits &traits, bool with_auto);

// Fan Control features that are not derived from the fan traits.
struct FanFeatureOptions {
  // Step command (needs a fan with speed control).
  bool step{false};
  // Wind feature: names of the fan preset modes that stand for natural wind and
  // sleep wind (case insensitive, nullptr = not mapped). At least one of them
  // must exist in the fan's preset modes.
  const char *wind_natural{nullptr};
  const char *wind_sleep{nullptr};
};

// Adds the MultiSpeed, Rocking and AirflowDirection features to a Fan Control
// cluster, based on the ESPHome fan traits, plus the Step and Wind features
// when requested. Returns an error (and logs why) when a requested feature
// can't be backed by the fan. Used by the generated endpoint build callback.
esp_err_t add_fan_features(esp_matter::cluster_t *cluster, fan::Fan *fan,
                           const FanFeatureOptions &options = {});

// Binds an ESPHome fan to the Fan Control cluster of an endpoint, through the
// binding framework (docs/dev/binding-framework.md).
//
// Matter -> ESPHome: controller writes to FanMode, PercentSetting, SpeedSetting,
// RockSetting and AirflowDirection (attribute watches), the Step command and
// WindSetting changes (delegate) all end in submit_inbound(): the settled
// cluster state is read on the CHIP task and the main loop performs one fan
// call. The cluster keeps its own state; the fan's own pushes are recognised as
// echoes by the PushScope.
//
// ESPHome -> Matter: the fan state is pushed on every state callback. The
// cluster settings are only written when the cluster no longer maps to the fan
// (a refused or clamped request, an ESPHome side change); the Current
// attributes, WindSetting, RockSetting and AirflowDirection always follow the
// fan. After a controller request the binding waits (settle) for the fan to
// publish, so a fan that publishes later does not see the old state pushed
// back first.
class MatterFanMapping
    : public MatterBinding<MatterFanOutbound, MatterFanInbound>,
      public chip::app::Clusters::FanControl::Delegate {
public:
  // How long to wait for the fan's own publish after applying a request.
  static constexpr uint16_t SETTLE_MS = 200;

  // wind_natural / wind_sleep: preset mode names, see FanFeatureOptions.
  MatterFanMapping(fan::Fan *fan, uint16_t endpoint_id,
                   const char *wind_natural = nullptr,
                   const char *wind_sleep = nullptr);

  // FanControl::Delegate. Called on the CHIP task.
  chip::Protocols::InteractionModel::Status
  HandleStep(chip::app::Clusters::FanControl::StepDirectionEnum direction,
             bool wrap, bool lowest_off) override;
  void OnWindSettingChanged(
      chip::BitMask<chip::app::Clusters::FanControl::WindBitmap> value) override;

protected:
  void setup_() override;
  MatterFanOutbound capture_() override;
  void write_matter_(const MatterFanOutbound &s) override;
  void merge_inbound_(MatterFanInbound &acc,
                      const MatterFanInbound &in) override;
  void apply_inbound_(const MatterFanInbound &in) override;
  void on_attribute_(uint32_t cluster, uint32_t attribute,
                     const esp_matter_attr_val_t &v) override;

  // CHIP task: the settled cluster state, and its submission as a request.
  MatterFanState read_cluster_state_() const;
  void submit_cluster_state_();
  // Main loop.
  void apply_settings_(const MatterFanState &state);
  void apply_wind_(bool natural, bool sleep);
  // Name of the active preset mode ("" = none).
  std::string active_preset_() const;

  fan::Fan *fan_;
  bool ready_{false};
  bool has_speed_{false};
  bool has_rocking_{false};
  bool has_direction_{false};
  int speed_count_{1};
  // Wind feature: the fan's own spelling of the matching preset mode names
  // (empty = not available).
  std::string wind_natural_request_;
  std::string wind_sleep_request_;
  std::string wind_natural_;
  std::string wind_sleep_;
  bool has_wind_{false};
  bool has_step_{false};
  // Main loop. The cluster was in Auto when the last request was applied, and
  // the fan state at that time: Auto survives pushes until the fan changes.
  bool auto_active_{false};
  bool auto_on_{false};
  int auto_speed_{0};
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_FAN
