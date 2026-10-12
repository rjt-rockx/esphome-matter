#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_MATTER_FILTER_MONITORING)

#include "esphome/core/log.h"
#include "matter_attributes.h"
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

void MatterFilterMapping::initialize() {
  auto *cluster = find_cluster(this->endpoint_id(), this->cluster_id_);
  if (cluster == nullptr) {
    ESP_LOGW(TAG, "Filter monitoring cluster 0x%04X not found on endpoint %u",
             static_cast<unsigned>(this->cluster_id_), this->endpoint_id());
    return;
  }
  // Without a delegate the ResetCondition command would dereference nothing.
  if (cluster->SetDelegate(this) != CHIP_NO_ERROR) {
    ESP_LOGW(TAG, "Failed to set the delegate of filter cluster 0x%04X",
             static_cast<unsigned>(this->cluster_id_));
    return;
  }

#ifdef USE_SENSOR
  if (this->condition_ != nullptr)
    this->condition_->add_on_state_callback(
        [this](float) { this->push_state_to_matter_(); });
#endif
#ifdef USE_BINARY_SENSOR
  if (this->replace_needed_ != nullptr)
    this->replace_needed_->add_on_state_callback(
        [this](bool) { this->push_state_to_matter_(); });
#endif
  global_matter_component->defer_to_main_loop(
      [this]() { this->push_state_to_matter_(); });
}

void MatterFilterMapping::push_state_to_matter_() {
  uint16_t eid = this->endpoint_id();
  uint32_t cid = this->cluster_id_;
  bool has_condition = false;
  uint8_t condition = 0;
  uint8_t indication = INDICATION_OK;
#ifdef USE_SENSOR
  if (this->condition_ != nullptr && this->condition_->has_state() &&
      std::isfinite(this->condition_->state)) {
    has_condition = true;
    condition = static_cast<uint8_t>(
        std::clamp(std::lround(this->condition_->state), 0L, 100L));
    indication = this->levels_.change_indication(condition);
  }
#endif
#ifdef USE_BINARY_SENSOR
  if (this->replace_needed_ != nullptr && this->replace_needed_->has_state() &&
      this->replace_needed_->state)
    indication = INDICATION_CRITICAL;
#endif

  run_on_matter_thread([eid, cid, has_condition, condition, indication]() {
    auto *cluster = find_cluster(eid, cid);
    if (cluster == nullptr)
      return;
    if (has_condition)
      cluster->UpdateCondition(condition);
    cluster->UpdateChangeIndication(
        static_cast<RM::ChangeIndicationEnum>(indication));
  });
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
