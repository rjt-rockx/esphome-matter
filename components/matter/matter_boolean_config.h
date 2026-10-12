#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER_BOOLEAN_CONFIG

#include "esphome/core/automation.h"
#include "matter_endpoints.h"
#include "matter_sync_bindings.h"

#ifdef USE_BINARY_SENSOR
#include "esphome/components/binary_sensor/binary_sensor.h"
#endif
#ifdef USE_NUMBER
#include "esphome/components/number/number.h"
#endif

#include <app/clusters/boolean-state-configuration-server/BooleanStateConfigurationCluster.h>
#include <data_model_provider/esp_matter_data_model_provider.h>

#include <cstdint>
#include <vector>

namespace esphome::matter {

// on_active / on_suppressed: AlarmsActive / AlarmsSuppressed of the Boolean
// State Configuration cluster changed (visual, audible).
class MatterAlarmsActiveTrigger : public Trigger<bool, bool> {};
class MatterAlarmsSuppressedTrigger : public Trigger<bool, bool> {};

class MatterBooleanStateConfigMapping;

#ifdef USE_BINARY_SENSOR
// The alarm source as a binding (MatterAlarmSourceBinding): the latest source
// state is applied to the cluster on the CHIP task, and re-applied after an
// EnableDisableAlarm command while the source is ON.
class MatterBscAlarmSource : public MatterAlarmSourceBinding {
public:
  MatterBscAlarmSource(uint16_t endpoint_id, binary_sensor::BinarySensor *source)
      : MatterAlarmSourceBinding(endpoint_id), source_(source) {}

  // CHIP task: the enabled alarms changed.
  void on_alarms_enabled() { this->matter_state_changed_(); }

protected:
  void setup_() override;
  bool source_state_(bool &on) override;
  void apply_source_(bool on) override;

  binary_sensor::BinarySensor *source_;
};
#endif

#ifdef USE_NUMBER
// CurrentSensitivityLevel <-> number, through the binding framework
// (MatterSettingBinding).
class MatterBscSensitivity : public MatterSettingBinding {
public:
  MatterBscSensitivity(uint16_t endpoint_id, number::Number *number)
      : MatterSettingBinding(endpoint_id), number_(number) {}

  // CHIP task: Matter holds a sensitivity level the controller (or the boot
  // read) wants the number to show.
  void on_matter_level(uint8_t level) { this->matter_changed_(level); }

protected:
  void setup_() override;
  bool entity_value_(int32_t &out) override;
  void command_value_(int32_t value) override;
  void write_value_(int32_t value) override;

  number::Number *number_;
};
#endif

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
  std::vector<MatterAlarmsActiveTrigger *> triggers_;
  std::vector<MatterAlarmsSuppressedTrigger *> suppressed_triggers_;
#ifdef USE_BINARY_SENSOR
  binary_sensor::BinarySensor *source_{nullptr};
  MatterBscAlarmSource *source_binding_{nullptr};
#endif
#ifdef USE_NUMBER
  number::Number *number_{nullptr};
  MatterBscSensitivity *sensitivity_binding_{nullptr};
#endif
};

} // namespace esphome::matter

#endif // USE_MATTER_BOOLEAN_CONFIG
