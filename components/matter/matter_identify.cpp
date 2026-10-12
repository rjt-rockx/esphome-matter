#include "matter_identify.h"
#ifdef USE_MATTER

#include "esphome/core/log.h"
#include "matter_attributes.h"
#include "matter_component.h"

#include <app/clusters/identify-server/IdentifyCluster.h>
#include <data_model_provider/esp_matter_data_model_provider.h>

#include <vector>

namespace esphome::matter {

static const char *const TAG = "matter.identify";

// The Identify cluster is code driven in esp-matter 1.6: IdentifyTime lives in
// the cluster object and the esp-matter attribute store is never updated, so it
// has to be read from the cluster. Runs on the CHIP task.
static uint16_t read_identify_time(uint16_t endpoint_id) {
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {endpoint_id, chip::app::Clusters::Identify::Id});
  if (server == nullptr)
    return 0;
  return static_cast<chip::app::Clusters::IdentifyCluster *>(server)
      ->GetIdentifyTime();
}

// Filled during setup() on the main loop, before esp_matter::start(); read only afterwards.
static std::vector<IdentifyTrigger *> &identify_triggers() {
  static std::vector<IdentifyTrigger *> triggers;
  return triggers;
}

void register_identify_trigger(IdentifyTrigger *trigger) {
  identify_triggers().push_back(trigger);
}

void IdentifyTrigger::dispatch(uint16_t endpoint_id, bool active,
                               uint16_t identify_time, uint8_t effect) {
  if (this->endpoint_id_ != ANY_ENDPOINT && this->endpoint_id_ != endpoint_id)
    return;
  defer_to_main_loop(this->parent_,
                     [this, endpoint_id, active, identify_time, effect]() {
                       this->trigger(endpoint_id, active, identify_time,
                                     effect);
                     });
}

esp_err_t matter_identification_cb(
    esp_matter::identification::callback_type_t type, uint16_t endpoint_id,
    uint8_t effect_id, uint8_t effect_variant, void * /*priv_data*/) {
  bool active = true;
  uint16_t identify_time = 0;
  uint8_t effect = IdentifyTrigger::NO_EFFECT;

  switch (type) {
  case esp_matter::identification::START: {
    identify_time = read_identify_time(endpoint_id);
    break;
  }
  case esp_matter::identification::STOP:
    active = false;
    break;
  case esp_matter::identification::EFFECT:
    effect = effect_id;
    break;
  default:
    return ESP_OK;
  }
  ESP_LOGD(TAG, "Identify endpoint=%u active=%d time=%u effect=%u variant=%u",
           endpoint_id, active, identify_time, effect, effect_variant);
  for (auto *trigger : identify_triggers())
    trigger->dispatch(endpoint_id, active, identify_time, effect);
  return ESP_OK;
}

} // namespace esphome::matter

#endif // USE_MATTER
