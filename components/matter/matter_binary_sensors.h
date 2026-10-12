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

// SmokeCoAlarm EndOfServiceEnum: ON = Expired (1), OFF = Normal (0).
inline esp_matter_attr_val_t end_of_service(bool value) {
  return esp_matter_enum8(value ? 1 : 0);
}

inline esp_matter_attr_val_t end_of_service_inverted(bool value) {
  return end_of_service(!value);
}

// SmokeCoAlarm MuteStateEnum: ON = Muted (1), OFF = NotMuted (0).
inline esp_matter_attr_val_t mute_state(bool value) {
  return esp_matter_enum8(value ? 1 : 0);
}

inline esp_matter_attr_val_t mute_state_inverted(bool value) {
  return mute_state(!value);
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

namespace detail {

inline chip::app::Clusters::SmokeCoAlarmCluster *
smoke_co_cluster(uint16_t endpoint_id, uint32_t cluster_id) {
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {endpoint_id, cluster_id});
  return static_cast<chip::app::Clusters::SmokeCoAlarmCluster *>(server);
}

} // namespace detail

inline CHIP_ERROR update_hardware_fault_attribute(uint16_t endpoint_id,
                                                  uint32_t cluster_id,
                                                  esp_matter_attr_val_t value) {
  auto *cluster = detail::smoke_co_cluster(endpoint_id, cluster_id);
  if (cluster == nullptr)
    return CHIP_ERROR_NOT_FOUND;
  cluster->SetHardwareFaultAlert(value.val.b);
  detail::update_smoke_co_expressed_state(cluster);
  return CHIP_NO_ERROR;
}

inline CHIP_ERROR update_end_of_service_attribute(uint16_t endpoint_id,
                                                  uint32_t cluster_id,
                                                  esp_matter_attr_val_t value) {
  auto *cluster = detail::smoke_co_cluster(endpoint_id, cluster_id);
  if (cluster == nullptr)
    return CHIP_ERROR_NOT_FOUND;
  cluster->SetEndOfServiceAlert(
      static_cast<chip::app::Clusters::SmokeCoAlarm::EndOfServiceEnum>(
          value.val.u8));
  detail::update_smoke_co_expressed_state(cluster);
  return CHIP_NO_ERROR;
}

// Follows the application: ON starts a self test, OFF completes it (which
// generates SelfTestComplete). A test requested from Matter has already set
// TestInProgress when the application reports it.
inline CHIP_ERROR update_test_in_progress_attribute(uint16_t endpoint_id,
                                                    uint32_t cluster_id,
                                                    esp_matter_attr_val_t value) {
  auto *cluster = detail::smoke_co_cluster(endpoint_id, cluster_id);
  if (cluster == nullptr)
    return CHIP_ERROR_NOT_FOUND;
  cluster->SetTestInProgress(value.val.b);
  detail::update_smoke_co_expressed_state(cluster);
  return CHIP_NO_ERROR;
}

// The cluster refuses to mute while an alarm is critical.
inline CHIP_ERROR update_device_muted_attribute(uint16_t endpoint_id,
                                                uint32_t cluster_id,
                                                esp_matter_attr_val_t value) {
  auto *cluster = detail::smoke_co_cluster(endpoint_id, cluster_id);
  if (cluster == nullptr)
    return CHIP_ERROR_NOT_FOUND;
  if (!cluster->SetDeviceMuted(
          static_cast<chip::app::Clusters::SmokeCoAlarm::MuteStateEnum>(
              value.val.u8)))
    return CHIP_ERROR_INCORRECT_STATE;
  return CHIP_NO_ERROR;
}

} // namespace esphome::matter

#endif // USE_MATTER
