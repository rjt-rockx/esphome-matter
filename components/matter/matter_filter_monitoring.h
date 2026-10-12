#pragma once

#include "esphome/core/defines.h"
// Compiled only when a filter monitoring cluster is bound (the generated code
// defines USE_MATTER_FILTER_MONITORING), so other builds don't need the cluster.
#if defined(USE_MATTER) && defined(USE_MATTER_FILTER_MONITORING)

#include "esphome/core/automation.h"
#include "matter_endpoints.h"

#ifdef USE_BINARY_SENSOR
#include "esphome/components/binary_sensor/binary_sensor.h"
#endif
#ifdef USE_BUTTON
#include "esphome/components/button/button.h"
#endif
#ifdef USE_SENSOR
#include "esphome/components/sensor/sensor.h"
#endif

#include <app/clusters/resource-monitoring-server/ResourceMonitoringCluster.h>
#include <app/clusters/resource-monitoring-server/ResourceMonitoringDelegate.h>

#include <cstdint>
#include <vector>

namespace esphome::matter {

// Thresholds that derive ChangeIndication from the Condition (percent).
// -1 = unused. `direction` is the DegradationDirection: 0 = down (Condition
// falls from 100 as the filter wears), 1 = up. Warning / Critical apply at or
// beyond the level in the direction of degradation.
struct FilterLevels {
  int8_t direction{0};
  int16_t warning{-1};
  int16_t critical{-1};

  // 0 = Ok, 1 = Warning, 2 = Critical (ResourceMonitoring::ChangeIndicationEnum)
  uint8_t change_indication(uint8_t condition) const;
};

// on_reset: the ResetCondition command arrived.
class FilterResetTrigger : public Trigger<> {
public:
  void fire() { this->trigger(); }
};

// HEPA / Activated Carbon Filter Monitoring cluster of an air purifier or
// extractor hood, fed by ESPHome entities:
//
//   condition sensor      -> Condition (percent) and, with the levels, ChangeIndication
//   replace_needed sensor -> ChangeIndication Critical while on
//   ResetCondition        -> button press and / or on_reset automations
//
// The ResourceMonitoringCluster object resets Condition and ChangeIndication
// by itself; the bound sensors take over again with their next update.
class MatterFilterMapping : public MatterEndpointMappingBase,
                            public chip::app::Clusters::ResourceMonitoring::Delegate {
public:
  MatterFilterMapping(uint16_t endpoint_id, uint32_t cluster_id,
                      FilterLevels levels)
      : MatterEndpointMappingBase(endpoint_id), cluster_id_(cluster_id),
        levels_(levels) {}

#ifdef USE_SENSOR
  void set_condition_sensor(sensor::Sensor *sensor) { this->condition_ = sensor; }
#endif
#ifdef USE_BINARY_SENSOR
  void set_replace_needed_sensor(binary_sensor::BinarySensor *sensor) {
    this->replace_needed_ = sensor;
  }
#endif
#ifdef USE_BUTTON
  void set_reset_button(button::Button *button) { this->reset_button_ = button; }
#endif
  void add_reset_trigger(FilterResetTrigger *trigger) {
    this->reset_triggers_.push_back(trigger);
  }

  void initialize() override;

  // ResourceMonitoring::Delegate. Called on the CHIP task.
  CHIP_ERROR Init() override { return CHIP_NO_ERROR; }
  chip::Protocols::InteractionModel::Status PostResetCondition() override;

protected:
  // Main loop: reports the state of the bound entities to the cluster.
  void push_state_to_matter_();

  uint32_t cluster_id_;
  FilterLevels levels_;
#ifdef USE_SENSOR
  sensor::Sensor *condition_{nullptr};
#endif
#ifdef USE_BINARY_SENSOR
  binary_sensor::BinarySensor *replace_needed_{nullptr};
#endif
#ifdef USE_BUTTON
  button::Button *reset_button_{nullptr};
#endif
  std::vector<FilterResetTrigger *> reset_triggers_;
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_MATTER_FILTER_MONITORING
