#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER_BOOLEAN_CONFIG

#include "esphome/core/automation.h"
#include "matter_endpoints.h"

#ifdef USE_BINARY_SENSOR
#include "esphome/components/binary_sensor/binary_sensor.h"
#endif
#ifdef USE_NUMBER
#include "esphome/components/number/number.h"
#endif

#include <app/clusters/boolean-state-configuration-server/BooleanStateConfigurationCluster.h>
#include <data_model_provider/esp_matter_data_model_provider.h>

#include <atomic>
#include <cstdint>
#include <vector>

namespace esphome::matter {

// on_active / on_suppressed: AlarmsActive / AlarmsSuppressed of the Boolean
// State Configuration cluster changed (visual, audible).
class MatterAlarmsActiveTrigger : public Trigger<bool, bool> {};
class MatterAlarmsSuppressedTrigger : public Trigger<bool, bool> {};

// Delegate and ESPHome bindings of the Boolean State Configuration cluster of
// an endpoint:
//
//  * the alarm source: while the binary_sensor is ON every enabled alarm is
//    active (AlarmsActive), OFF clears all alarms. The state of the source is
//    remembered, so alarms that a controller enables while the source is
//    already ON become active right away,
//  * on_active / on_suppressed automations (also for changes caused by the
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
  void add_alarms_suppressed_trigger(MatterAlarmsSuppressedTrigger *trigger) {
    this->suppressed_triggers_.push_back(trigger);
  }
#ifdef USE_BINARY_SENSOR
  void set_alarm_source(binary_sensor::BinarySensor *source) {
    this->source_ = source;
  }
#endif
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
      override;
  bool OnCurrentSensitivityLevelChanged(uint8_t new_value) override;
  bool OnAlarmsActiveChanged(
      chip::BitMask<chip::app::Clusters::BooleanStateConfiguration::AlarmModeBitmap>
          new_value) override;
  bool OnAlarmsSuppressedChanged(
      chip::BitMask<chip::app::Clusters::BooleanStateConfiguration::AlarmModeBitmap>
          new_value) override;

protected:
  // Matter thread: ON makes the enabled alarms active, OFF clears them.
  void apply_source_(bool on);
#ifdef USE_NUMBER
  void on_number_(float value);
  void publish_to_number_(uint8_t level);
#endif

  std::vector<MatterAlarmsActiveTrigger *> triggers_;
  std::vector<MatterAlarmsSuppressedTrigger *> suppressed_triggers_;
  std::atomic<bool> source_on_{false};
#ifdef USE_BINARY_SENSOR
  binary_sensor::BinarySensor *source_{nullptr};
#endif
#ifdef USE_NUMBER
  number::Number *number_{nullptr};
#endif
};

} // namespace esphome::matter

#endif // USE_MATTER_BOOLEAN_CONFIG
