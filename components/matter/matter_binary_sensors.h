#pragma once

// Converters and Matter-thread updaters for binary_sensors bound to the Occupancy Sensing and Smoke CO Alarm
// clusters, plus inverted variants of the Boolean State and Occupancy converters. See docs/binary-sensors.md.
//
// Everything here is inline so nothing is emitted (or linked) unless the generated endpoint code uses it. This is
// required because the cluster implementations are only compiled when the cluster is enabled in sdkconfig.

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include <app/clusters/occupancy-sensor-server/OccupancySensingCluster.h>
#include <app/clusters/smoke-co-alarm-server/SmokeCoAlarmCluster.h>
#include <clusters/SmokeCoAlarm/Enums.h>
#include <data_model_provider/esp_matter_data_model_provider.h>
#include <esp_matter.h>

#include <array>
#include <cstdint>

namespace esphome::matter {

namespace conversion {

// Boolean State: TRUE/FALSE is defined by the device type. Contact sensor: TRUE = closed.
inline esp_matter_attr_val_t boolean_state_inverted(bool value) {
  return esp_matter_bool(!value);
}

inline esp_matter_attr_val_t occupancy_inverted(bool value) {
  return esp_matter_bitmap8(value ? 0 : 1);
}

// SmokeCoAlarm AlarmStateEnum: ON = Critical (2), OFF = Normal (0). Warning (1) is never used.
inline esp_matter_attr_val_t alarm_state(bool value) {
  return esp_matter_enum8(value ? 2 : 0);
}

inline esp_matter_attr_val_t alarm_state_inverted(bool value) {
  return alarm_state(!value);
}

} // namespace conversion

// Updaters have the SensorAttributeUpdater signature and run on the Matter thread.

inline CHIP_ERROR update_occupancy_attribute(uint16_t endpoint_id,
                                             uint32_t cluster_id,
                                             esp_matter_attr_val_t value) {
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {endpoint_id, cluster_id});
  if (server == nullptr)
    return CHIP_ERROR_NOT_FOUND;

  static_cast<chip::app::Clusters::OccupancySensingCluster *>(server)
      ->SetOccupancy((value.val.u8 & 1) != 0);
  return CHIP_NO_ERROR;
}

namespace detail {

// Priority order from the Matter specification (highest first).
inline void update_smoke_co_expressed_state(
    chip::app::Clusters::SmokeCoAlarmCluster *cluster) {
  using chip::app::Clusters::SmokeCoAlarm::ExpressedStateEnum;
  static const std::array<ExpressedStateEnum,
                          chip::app::Clusters::SmokeCoAlarmCluster::
                              kPriorityOrderLength>
      kPriorityOrder = {
          ExpressedStateEnum::kInoperative,   ExpressedStateEnum::kSmokeAlarm,
          ExpressedStateEnum::kInterconnectSmoke, ExpressedStateEnum::kCOAlarm,
          ExpressedStateEnum::kInterconnectCO, ExpressedStateEnum::kHardwareFault,
          ExpressedStateEnum::kTesting,       ExpressedStateEnum::kEndOfService,
          ExpressedStateEnum::kBatteryAlert};
  cluster->SetExpressedStateByPriority(kPriorityOrder);
}

} // namespace detail

inline CHIP_ERROR update_smoke_state_attribute(uint16_t endpoint_id,
                                               uint32_t cluster_id,
                                               esp_matter_attr_val_t value) {
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {endpoint_id, cluster_id});
  if (server == nullptr)
    return CHIP_ERROR_NOT_FOUND;

  auto *cluster = static_cast<chip::app::Clusters::SmokeCoAlarmCluster *>(server);
  if (!cluster->SetSmokeState(
          static_cast<chip::app::Clusters::SmokeCoAlarm::AlarmStateEnum>(
              value.val.u8)))
    return CHIP_ERROR_INCORRECT_STATE;
  detail::update_smoke_co_expressed_state(cluster);
  return CHIP_NO_ERROR;
}

inline CHIP_ERROR update_co_state_attribute(uint16_t endpoint_id,
                                            uint32_t cluster_id,
                                            esp_matter_attr_val_t value) {
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {endpoint_id, cluster_id});
  if (server == nullptr)
    return CHIP_ERROR_NOT_FOUND;

  auto *cluster = static_cast<chip::app::Clusters::SmokeCoAlarmCluster *>(server);
  if (!cluster->SetCOState(
          static_cast<chip::app::Clusters::SmokeCoAlarm::AlarmStateEnum>(
              value.val.u8)))
    return CHIP_ERROR_INCORRECT_STATE;
  detail::update_smoke_co_expressed_state(cluster);
  return CHIP_NO_ERROR;
}

} // namespace esphome::matter

#endif // USE_MATTER
