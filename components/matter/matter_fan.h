#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_FAN)

#include "esphome/components/fan/fan.h"
#include "esphome/components/fan/fan_traits.h"
#include "matter_endpoints.h"

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

// Snapshot of the Fan Control cluster state, taken on the CHIP task.
struct MatterFanState {
  // False when the cluster is in Auto, which ESPHome can't represent.
  bool has_speed_info{false};
  bool off{true};
  uint8_t percent{0};
  uint8_t speed{0};
  bool has_rock{false};
  bool rocking{false};
  bool has_direction{false};
  bool reverse{false};
};

class MatterFanMapping : public MatterEndpointMappingBase,
                        public chip::app::Clusters::FanControl::Delegate {
public:
  // wind_natural / wind_sleep: preset mode names, see FanFeatureOptions.
  MatterFanMapping(fan::Fan *fan, uint16_t endpoint_id,
                   const char *wind_natural = nullptr,
                   const char *wind_sleep = nullptr);

  void initialize() override;

  // update_settings is false for changes that originate from Matter. The
  // settings are already correct then, but the Current attributes are driven by
  // the application and always need to be updated.
  void push_state_to_matter(bool update_settings = true);

  // FanControl::Delegate. Called on the CHIP task.
  chip::Protocols::InteractionModel::Status
  HandleStep(chip::app::Clusters::FanControl::StepDirectionEnum direction,
             bool wrap, bool lowest_off) override;
  void OnWindSettingChanged(
      chip::BitMask<chip::app::Clusters::FanControl::WindBitmap> value) override;

protected:
  // Reads the cluster state (CHIP task) and applies it to the fan (main loop).
  void sync_from_cluster_();
  void apply_(const MatterFanState &state);
  // Main loop: selects the preset mode for a WindSetting value.
  void apply_wind_(bool natural, bool sleep);
  // Name of the active preset mode ("" = none).
  std::string active_preset_() const;

  // The ESPHome side state last seen or applied (main loop), to push only real
  // ESPHome-side changes: a state callback that repeats the old state would
  // otherwise overwrite a controller write that is still on its way to apply_().
  struct FanSnapshot {
    bool on{false};
    int speed{0};
    bool oscillating{false};
    bool reverse{false};
    std::string preset;
    bool operator==(const FanSnapshot &o) const {
      return on == o.on && speed == o.speed && oscillating == o.oscillating &&
             reverse == o.reverse && preset == o.preset;
    }
  };
  FanSnapshot snapshot_() const;
  FanSnapshot last_;

  fan::Fan *fan_;
  bool has_speed_{false};
  bool has_rocking_{false};
  bool has_direction_{false};
  int speed_count_{1};
  bool synchronizing_from_matter_{false};
  // Wind feature: the fan's own spelling of the matching preset mode names
  // (empty = not available).
  std::string wind_natural_request_;
  std::string wind_sleep_request_;
  std::string wind_natural_;
  std::string wind_sleep_;
  bool has_wind_{false};
  // True while push_state_to_matter() writes the cluster (CHIP task only): the
  // delegate callbacks run synchronously inside those writes and are echoes.
  bool pushing_{false};
  bool has_step_{false};
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_FAN
