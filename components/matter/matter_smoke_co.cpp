#include "matter_smoke_co.h"

#ifdef USE_MATTER_SMOKE_CO

#include "esphome/core/log.h"
#include "matter_attributes.h"
#include "matter_binary_sensors.h"
#include "matter_component.h"

#include <clusters/SmokeCoAlarm/ClusterId.h>
#include <data_model_provider/esp_matter_data_model_provider.h>

static const char *const TAG = "matter.smoke_co";

namespace esphome::matter {

namespace {

chip::app::Clusters::SmokeCoAlarmCluster *find_cluster(uint16_t endpoint_id) {
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {endpoint_id, chip::app::Clusters::SmokeCoAlarm::Id});
  return static_cast<chip::app::Clusters::SmokeCoAlarmCluster *>(server);
}

} // namespace

void MatterSmokeCoAlarmMapping::initialize() {
  run_on_matter_thread([this]() {
    auto *cluster = find_cluster(this->endpoint_id_);
    if (cluster == nullptr) {
      ESP_LOGW(TAG, "Smoke CO Alarm cluster of endpoint %u not found",
               this->endpoint_id_);
      return;
    }
    cluster->SetDelegate(this);
  });
}

// Matter thread, called by the cluster while it handles SelfTestRequest.
void MatterSmokeCoAlarmMapping::OnSelfTestRequested() {
  global_matter_component->defer_to_main_loop([this]() {
    ESP_LOGD(TAG, "Self test requested on endpoint %u", this->endpoint_id_);
    for (auto *trigger : this->triggers_)
      trigger->trigger();
    if (this->auto_complete_ms_ > 0) {
      global_matter_component->run_on_main_loop_after(
          this->auto_complete_ms_, [this]() { this->complete_self_test_(); });
    }
  });
}

// Main loop.
void MatterSmokeCoAlarmMapping::complete_self_test_() {
  run_on_matter_thread([this]() {
    auto *cluster = find_cluster(this->endpoint_id_);
    if (cluster == nullptr)
      return;
    cluster->SetTestInProgress(false);
    detail::update_smoke_co_expressed_state(cluster);
    ESP_LOGD(TAG, "Self test completed on endpoint %u", this->endpoint_id_);
  });
}

} // namespace esphome::matter

#endif // USE_MATTER_SMOKE_CO
