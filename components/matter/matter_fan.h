#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_FAN)

#include "esphome/components/fan/fan.h"
#include "esphome/components/fan/fan_traits.h"
#include "matter_endpoints.h"

#include <esp_matter.h>

#include <cstdint>

namespace esphome::matter {

// Chooses a FanModeSequence (Matter enum value) that matches the ESPHome fan
// traits. Used by the generated endpoint build callback.
uint8_t fan_mode_sequence(const fan::FanTraits &traits, bool with_auto);

// Adds the MultiSpeed, Rocking and AirflowDirection features to a Fan Control
// cluster, based on the ESPHome fan traits. Used by the generated endpoint
// build callback.
esp_err_t add_fan_features(esp_matter::cluster_t *cluster, fan::Fan *fan);

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

class MatterFanMapping : public MatterEndpointMappingBase {
public:
  MatterFanMapping(fan::Fan *fan, uint16_t endpoint_id);

  void initialize() override;

  // update_settings is false for changes that originate from Matter. The
  // settings are already correct then, but the Current attributes are driven by
  // the application and always need to be updated.
  void push_state_to_matter(bool update_settings = true);

protected:
  // Reads the cluster state (CHIP task) and applies it to the fan (main loop).
  void sync_from_cluster_();
  void apply_(const MatterFanState &state);

  fan::Fan *fan_;
  bool has_speed_{false};
  bool has_rocking_{false};
  bool has_direction_{false};
  int speed_count_{1};
  bool synchronizing_from_matter_{false};
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_FAN
