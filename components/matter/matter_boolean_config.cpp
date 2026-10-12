#include "matter_boolean_config.h"

#ifdef USE_MATTER_BOOLEAN_CONFIG

#include "esphome/core/log.h"
#include "matter_attributes.h"
#include "matter_component.h"

#include <clusters/BooleanStateConfiguration/ClusterId.h>
#include <clusters/BooleanStateConfiguration/Enums.h>

#include <algorithm>
#include <cmath>

static const char *const TAG = "matter.boolean_config";

namespace esphome::matter {

namespace {

using chip::app::Clusters::BooleanStateConfigurationCluster;

BooleanStateConfigurationCluster *find_cluster(uint16_t endpoint_id) {
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {endpoint_id,
           chip::app::Clusters::BooleanStateConfiguration::Id});
  return static_cast<BooleanStateConfigurationCluster *>(server);
}

} // namespace

void MatterBooleanStateConfigMapping::initialize() {
#ifdef USE_NUMBER
  bool number_has_state = false;
  uint8_t number_level = 0;
  if (this->number_ != nullptr) {
    number_has_state = this->number_->has_state();
    if (number_has_state)
      number_level = static_cast<uint8_t>(std::clamp<long>(
          std::lround(this->number_->state), 0, 9));
    this->number_->add_on_state_callback(
        [this](float value) { this->on_number_(value); });
  }
#endif

  run_on_matter_thread([this
#ifdef USE_NUMBER
                        ,
                        number_has_state, number_level
#endif
  ]() {
    auto *cluster = find_cluster(this->endpoint_id_);
    if (cluster == nullptr) {
      ESP_LOGW(TAG, "Boolean State Configuration cluster of endpoint %u not found",
               this->endpoint_id_);
      return;
    }
    cluster->SetDelegate(this);
#ifdef USE_NUMBER
    if (this->number_ == nullptr)
      return;
    if (number_has_state) {
      // The number (typically restored by ESPHome) is the source of truth.
      if (cluster->GetCurrentSensitivityLevel() != number_level) {
        CHIP_ERROR err = cluster->SetCurrentSensitivityLevel(number_level);
        if (err != CHIP_NO_ERROR)
          ESP_LOGW(TAG, "Cannot set sensitivity level %u on endpoint %u: %s",
                   number_level, this->endpoint_id_, err.AsString());
      }
    } else {
      uint8_t level = cluster->GetCurrentSensitivityLevel();
      global_matter_component->defer_to_main_loop(
          [this, level]() { this->publish_to_number_(level); });
    }
#endif
  });
}

// Matter thread. Returning false would reject the change.
bool MatterBooleanStateConfigMapping::OnCurrentSensitivityLevelChanged(
    uint8_t new_value) {
#ifdef USE_NUMBER
  if (this->number_ != nullptr)
    global_matter_component->defer_to_main_loop(
        [this, new_value]() { this->publish_to_number_(new_value); });
#endif
  return true;
}

// Matter thread.
bool MatterBooleanStateConfigMapping::OnAlarmsActiveChanged(
    chip::BitMask<chip::app::Clusters::BooleanStateConfiguration::AlarmModeBitmap>
        new_value) {
  using chip::app::Clusters::BooleanStateConfiguration::AlarmModeBitmap;
  bool visual = new_value.Has(AlarmModeBitmap::kVisual);
  bool audible = new_value.Has(AlarmModeBitmap::kAudible);
  if (!this->triggers_.empty()) {
    global_matter_component->defer_to_main_loop([this, visual, audible]() {
      for (auto *trigger : this->triggers_)
        trigger->trigger(visual, audible);
    });
  }
  return true;
}

#ifdef USE_NUMBER
// Main loop.
void MatterBooleanStateConfigMapping::on_number_(float value) {
  if (std::isnan(value))
    return;
  uint8_t level =
      static_cast<uint8_t>(std::clamp<long>(std::lround(value), 0, 9));
  run_on_matter_thread([this, level]() {
    auto *cluster = find_cluster(this->endpoint_id_);
    if (cluster == nullptr)
      return;
    // No-op when the number only shows a value that a controller wrote.
    CHIP_ERROR err = cluster->SetCurrentSensitivityLevel(level);
    if (err != CHIP_NO_ERROR)
      ESP_LOGW(TAG, "Cannot set sensitivity level %u on endpoint %u: %s", level,
               this->endpoint_id_, err.AsString());
  });
}

// Main loop.
void MatterBooleanStateConfigMapping::publish_to_number_(uint8_t level) {
  if (this->number_->has_state() &&
      std::lround(this->number_->state) == static_cast<long>(level))
    return;
  auto call = this->number_->make_call();
  call.set_value(static_cast<float>(level));
  call.perform();
}
#endif // USE_NUMBER

} // namespace esphome::matter

#endif // USE_MATTER_BOOLEAN_CONFIG
