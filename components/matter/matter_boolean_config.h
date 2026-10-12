#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER_BOOLEAN_CONFIG

#include "esphome/core/automation.h"
#include "matter_endpoints.h"

#ifdef USE_NUMBER
#include "esphome/components/number/number.h"
#endif

#include <app/clusters/boolean-state-configuration-server/BooleanStateConfigurationCluster.h>
#include <data_model_provider/esp_matter_data_model_provider.h>

#include <cstdint>
#include <vector>

namespace esphome::matter {

// on_active: AlarmsActive of the Boolean State Configuration cluster changed.
class MatterAlarmsActiveTrigger : public Trigger<bool, bool> {};

// Updater (Matter thread) of the AlarmsActive attribute for a binary_sensor:
// ON makes every enabled alarm active, OFF clears all alarms. AlarmsEnabled is
// controlled by the controllers through the EnableDisableAlarm command and
// starts out empty, so nothing becomes active until an alarm was enabled.
inline CHIP_ERROR update_alarms_active_attribute(uint16_t endpoint_id,
                                                 uint32_t cluster_id,
                                                 esp_matter_attr_val_t value) {
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {endpoint_id, cluster_id});
  if (server == nullptr)
    return CHIP_ERROR_NOT_FOUND;
  auto *cluster =
      static_cast<chip::app::Clusters::BooleanStateConfigurationCluster *>(
          server);
  if (value.val.b)
    cluster->SetAllEnabledAlarmsActive();
  else
    cluster->ClearAllAlarms();
  return CHIP_NO_ERROR;
}

// Delegate and ESPHome bindings of the Boolean State Configuration cluster of
// an endpoint:
//
//  * on_active automations for AlarmsActive changes (also changes caused by
//    EnableDisableAlarm and SuppressAlarm commands), and
//  * a number that mirrors CurrentSensitivityLevel in both directions.
class MatterBooleanStateConfigMapping
    : public MatterEndpointMappingBase,
      public chip::app::Clusters::BooleanStateConfiguration::Delegate {
public:
  explicit MatterBooleanStateConfigMapping(uint16_t endpoint_id)
      : MatterEndpointMappingBase(endpoint_id) {}

  void add_alarms_active_trigger(MatterAlarmsActiveTrigger *trigger) {
    this->triggers_.push_back(trigger);
  }
#ifdef USE_NUMBER
  void set_sensitivity_number(number::Number *number) {
    this->number_ = number;
  }
#endif

  void initialize() override;

  // Delegate, Matter thread.
  CHIP_ERROR HandleSuppressAlarm(
      chip::app::Clusters::BooleanStateConfiguration::AlarmModeBitmap) override {
    return CHIP_NO_ERROR;
  }
  CHIP_ERROR HandleEnableDisableAlarms(
      chip::BitMask<chip::app::Clusters::BooleanStateConfiguration::AlarmModeBitmap>)
      override {
    return CHIP_NO_ERROR;
  }
  bool OnCurrentSensitivityLevelChanged(uint8_t new_value) override;
  bool OnAlarmsActiveChanged(
      chip::BitMask<chip::app::Clusters::BooleanStateConfiguration::AlarmModeBitmap>
          new_value) override;

protected:
#ifdef USE_NUMBER
  void on_number_(float value);
  void publish_to_number_(uint8_t level);
#endif

  std::vector<MatterAlarmsActiveTrigger *> triggers_;
#ifdef USE_NUMBER
  number::Number *number_{nullptr};
#endif
};

} // namespace esphome::matter

#endif // USE_MATTER_BOOLEAN_CONFIG
