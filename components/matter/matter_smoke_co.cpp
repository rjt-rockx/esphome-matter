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

void MatterSmokeCoAlarmMapping::setup_() {
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
  this->submit_inbound(1);
}

// Main loop; one run per accepted request.
void MatterSmokeCoAlarmMapping::apply_inbound_(const uint32_t &requests) {
  for (uint32_t i = 0; i < requests; i++) {
    ESP_LOGD(TAG, "Self test requested on endpoint %u", this->endpoint_id_);
    for (auto *trigger : this->triggers_)
      trigger->trigger();
    if (this->auto_complete_ms_ > 0) {
      binding_platform()->main_after(this->auto_complete_ms_,
                                     [this]() { this->complete_self_test_(); });
    }
  }
}

// Main loop.
void MatterSmokeCoAlarmMapping::complete_self_test_() {
  this->completions_++;
  this->request_push();
}

// Main loop.
MatterSmokeCoSelfTestState MatterSmokeCoAlarmMapping::capture_() {
  MatterSmokeCoSelfTestState state;
  state.completions = this->completions_;
  return state;
}

// Matter thread, inside the PushScope. Ending the test is idempotent: the
// cluster emits SelfTestComplete when TestInProgress turns false.
void MatterSmokeCoAlarmMapping::write_matter_(
    const MatterSmokeCoSelfTestState &s) {
  if (s.completions == this->written_completions_)
    return;
  this->written_completions_ = s.completions;
  auto *cluster = find_cluster(this->endpoint_id_);
  if (cluster == nullptr)
    return;
  cluster->SetTestInProgress(false);
  detail::update_smoke_co_expressed_state(cluster);
  ESP_LOGD(TAG, "Self test completed on endpoint %u", this->endpoint_id_);
}

} // namespace esphome::matter

#endif // USE_MATTER_SMOKE_CO
