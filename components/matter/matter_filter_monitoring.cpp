#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_MATTER_FILTER_MONITORING)

#include "esphome/core/log.h"
#include "matter_component.h"
#include "matter_filter_monitoring.h"

#include <algorithm>
#include <cmath>
#include <data_model_provider/esp_matter_data_model_provider.h>

static const char *const TAG = "matter";

namespace esphome::matter {

namespace {

namespace RM = chip::app::Clusters::ResourceMonitoring;

RM::ResourceMonitoringCluster *find_cluster(uint16_t endpoint_id,
                                            uint32_t cluster_id) {
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {endpoint_id, cluster_id});
  return static_cast<RM::ResourceMonitoringCluster *>(server);
}

constexpr uint8_t INDICATION_OK = 0;
constexpr uint8_t INDICATION_WARNING = 1;
constexpr uint8_t INDICATION_CRITICAL = 2;

} // namespace

uint8_t FilterLevels::change_indication(uint8_t condition) const {
  auto reached = [&](int level) {
    return level >= 0 &&
           (this->direction == 0 ? condition <= level : condition >= level);
  };
  if (reached(this->critical))
    return INDICATION_CRITICAL;
  if (reached(this->warning))
    return INDICATION_WARNING;
  return INDICATION_OK;
}

void MatterFilterMapping::setup_() {
  auto *cluster = find_cluster(this->endpoint_id(), this->cluster_id_);
  if (cluster == nullptr) {
    ESP_LOGW(TAG, "Filter monitoring cluster 0x%04X not found on endpoint %u",
             static_cast<unsigned>(this->cluster_id_), this->endpoint_id());
    return;
  }
  // The delegate is installed by esp-matter when the cluster is initialized (the
  // generated endpoint build callback passes this object in the cluster config),
  // before Matter serves requests. ResourceMonitoringCluster dereferences it
  // unchecked for ResetCondition, so make the problem visible if it is missing.
  if (this->GetInstance() == nullptr) {
    ESP_LOGE(TAG,
             "Filter cluster 0x%04X on endpoint %u has no delegate installed; "
             "ResetCondition would not work",
             static_cast<unsigned>(this->cluster_id_), this->endpoint_id());
    return;
  }

#ifdef USE_SENSOR
  if (this->condition_ != nullptr)
    this->condition_->add_on_state_callback(
        [this](float) { this->request_push(); });
#endif
#ifdef USE_BINARY_SENSOR
  if (this->replace_needed_ != nullptr)
    this->replace_needed_->add_on_state_callback(
        [this](bool) { this->request_push(); });
#endif
}

// Main loop.
MatterFilterState MatterFilterMapping::capture_() {
  MatterFilterState s;
  s.indication = INDICATION_OK;
#ifdef USE_SENSOR
  if (this->condition_ != nullptr && this->condition_->has_state() &&
      std::isfinite(this->condition_->state)) {
    s.has_condition = true;
    s.condition = static_cast<uint8_t>(
        std::clamp(std::lround(this->condition_->state), 0L, 100L));
    s.indication = this->levels_.change_indication(s.condition);
  }
#endif
#ifdef USE_BINARY_SENSOR
  if (this->replace_needed_ != nullptr && this->replace_needed_->has_state() &&
      this->replace_needed_->state)
    s.indication = INDICATION_CRITICAL;
#endif
  return s;
}

// Runs on the CHIP task, inside a PushScope.
void MatterFilterMapping::write_matter_(const MatterFilterState &s) {
  auto *cluster = find_cluster(this->endpoint_id(), this->cluster_id_);
  if (cluster == nullptr)
    return;
  if (s.has_condition)
    cluster->UpdateCondition(s.condition);
  cluster->UpdateChangeIndication(
      static_cast<RM::ChangeIndicationEnum>(s.indication));
}

// Runs on the CHIP task, after the cluster reset Condition and ChangeIndication.
chip::Protocols::InteractionModel::Status MatterFilterMapping::PostResetCondition() {
  global_matter_component->defer_to_main_loop([this]() {
#ifdef USE_BUTTON
    if (this->reset_button_ != nullptr)
      this->reset_button_->press();
#endif
    for (auto *trigger : this->reset_triggers_)
      trigger->fire();
    // No push of the bound sensors: the cluster just restored Condition, and the
    // sensor reports its own (reset) value on its next update.
  });
  return chip::Protocols::InteractionModel::Status::Success;
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_MATTER_FILTER_MONITORING
