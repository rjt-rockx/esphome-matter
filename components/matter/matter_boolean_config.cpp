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
  const bool number_has_state =
      this->number_ != nullptr && this->number_->has_state();
#endif
  // The bindings exist before the CHIP task can look at them; they are
  // initialized (which queues their first push) after the delegate work item.
#ifdef USE_BINARY_SENSOR
  if (this->source_ != nullptr)
    this->source_binding_ =
        new MatterBscAlarmSource(this->endpoint_id_, this->source_);
#endif
#ifdef USE_NUMBER
  if (this->number_ != nullptr)
    this->sensitivity_binding_ =
        new MatterBscSensitivity(this->endpoint_id_, this->number_);
#endif

  run_on_matter_thread([this
#ifdef USE_NUMBER
                        ,
                        number_has_state
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
    // Without a (restored) number state Matter's level is shown in the number;
    // otherwise the number wins and is pushed by its binding.
    if (this->sensitivity_binding_ != nullptr && !number_has_state)
      this->sensitivity_binding_->on_matter_level(
          cluster->GetCurrentSensitivityLevel());
#endif
  });

#ifdef USE_BINARY_SENSOR
  if (this->source_binding_ != nullptr)
    this->source_binding_->initialize();
#endif
#ifdef USE_NUMBER
  if (this->sensitivity_binding_ != nullptr)
    this->sensitivity_binding_->initialize();
#endif
}

#ifdef USE_BINARY_SENSOR
// ------------------------------------------------------- MatterBscAlarmSource

void MatterBscAlarmSource::setup_() {
  this->source_->add_on_state_callback([this](bool) { this->request_push(); });
}

// Main loop.
bool MatterBscAlarmSource::source_state_(bool &on) {
  if (!this->source_->has_state())
    return false;
  on = this->source_->state;
  return true;
}

// Matter thread, inside the PushScope: ON makes the enabled alarms active, OFF
// clears them.
void MatterBscAlarmSource::apply_source_(bool on) {
  auto *cluster = find_cluster(this->endpoint_id_);
  if (cluster == nullptr)
    return;
  if (on)
    cluster->SetAllEnabledAlarmsActive();
  else
    cluster->ClearAllAlarms();
}
#endif // USE_BINARY_SENSOR

#ifdef USE_NUMBER
// ------------------------------------------------------ MatterBscSensitivity

void MatterBscSensitivity::setup_() {
  this->number_->add_on_state_callback([this](float) { this->request_push(); });
}

// Main loop.
bool MatterBscSensitivity::entity_value_(int32_t &out) {
  if (!this->number_->has_state() || std::isnan(this->number_->state))
    return false;
  out = std::clamp<long>(std::lround(this->number_->state), 0, 9);
  return true;
}

// Main loop. Shows the level that Matter holds.
void MatterBscSensitivity::command_value_(int32_t level) {
  if (this->number_->has_state() &&
      std::lround(this->number_->state) == static_cast<long>(level))
    return;
  auto call = this->number_->make_call();
  call.set_value(static_cast<float>(level));
  call.perform();
}

// Matter thread, inside the PushScope.
void MatterBscSensitivity::write_value_(int32_t level) {
  auto *cluster = find_cluster(this->endpoint_id_);
  if (cluster == nullptr)
    return;
  // No-op when Matter already holds the level.
  CHIP_ERROR err =
      cluster->SetCurrentSensitivityLevel(static_cast<uint8_t>(level));
  if (err != CHIP_NO_ERROR)
    ESP_LOGW(TAG, "Cannot set sensitivity level %d on endpoint %u: %s",
             static_cast<int>(level), this->endpoint_id_, err.AsString());
}
#endif // USE_NUMBER

// Matter thread, inside the EnableDisableAlarm command (AlarmsEnabled is
// already updated). Alarms that were enabled while the source is ON become
// active once the command is done.
CHIP_ERROR MatterBooleanStateConfigMapping::HandleEnableDisableAlarms(
    chip::BitMask<chip::app::Clusters::BooleanStateConfiguration::AlarmModeBitmap>) {
#ifdef USE_BINARY_SENSOR
  if (this->source_binding_ != nullptr)
    this->source_binding_->on_alarms_enabled();
#endif
  return CHIP_NO_ERROR;
}

// Matter thread. Returning false would reject the change.
bool MatterBooleanStateConfigMapping::OnCurrentSensitivityLevelChanged(
    uint8_t new_value) {
#ifdef USE_NUMBER
  // The updates caused by our own SetCurrentSensitivityLevel() are not controller writes.
  if (this->sensitivity_binding_ != nullptr &&
      !MatterBindingBase::is_echo(this->endpoint_id_))
    this->sensitivity_binding_->on_matter_level(new_value);
#endif
  return true;
}

// Matter thread. The cluster passes the alarms that were suppressed by the
// command (all suppressed alarms are cleared with an empty mask).
bool MatterBooleanStateConfigMapping::OnAlarmsSuppressedChanged(
    chip::BitMask<chip::app::Clusters::BooleanStateConfiguration::AlarmModeBitmap>
        new_value) {
  using chip::app::Clusters::BooleanStateConfiguration::AlarmModeBitmap;
  bool visual = new_value.Has(AlarmModeBitmap::kVisual);
  bool audible = new_value.Has(AlarmModeBitmap::kAudible);
  if (!this->suppressed_triggers_.empty()) {
    global_matter_component->defer_to_main_loop([this, visual, audible]() {
      for (auto *trigger : this->suppressed_triggers_)
        trigger->trigger(visual, audible);
    });
  }
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

} // namespace esphome::matter

#endif // USE_MATTER_BOOLEAN_CONFIG
