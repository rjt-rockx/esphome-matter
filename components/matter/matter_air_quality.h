#pragma once

// Converter and Matter-thread updater of the Air Quality cluster's AirQuality attribute, for a sensor bound with
// `air_quality:`. The level is either the sensor value itself (a 0-6 index) or computed from thresholds by a lambda
// that the code generation emits. Header only, so nothing is compiled or linked unless the generated code uses it.

#include "esphome/core/defines.h"
#ifdef USE_MATTER_AIR_QUALITY

#include <app/clusters/air-quality-server/AirQualityCluster.h>
#include <clusters/AirQuality/Enums.h>
#include <data_model_provider/esp_matter_data_model_provider.h>
#include <esp_matter.h>

#include <algorithm>
#include <cmath>
#include <cstdint>

namespace esphome::matter {

namespace conversion {

// AirQualityEnum: 0 Unknown, 1 Good, 2 Fair, 3 Moderate, 4 Poor, 5 VeryPoor, 6 ExtremelyPoor. Anything else
// (including NaN) is reported as Unknown.
inline esp_matter_attr_val_t air_quality_index(float value) {
  if (std::isnan(value) || value < 0.0f || value > 6.4f)
    return esp_matter_enum8(0);
  return esp_matter_enum8(static_cast<uint8_t>(std::lround(value)));
}

} // namespace conversion

inline CHIP_ERROR update_air_quality_attribute(uint16_t endpoint_id,
                                               uint32_t cluster_id,
                                               esp_matter_attr_val_t value) {
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {endpoint_id, cluster_id});
  if (server == nullptr)
    return CHIP_ERROR_NOT_FOUND;
  auto status =
      static_cast<chip::app::Clusters::AirQualityCluster *>(server)
          ->SetAirQuality(
              static_cast<chip::app::Clusters::AirQuality::AirQualityEnum>(
                  value.val.u8));
  // Fails for a level whose optional feature (Fair, Moderate, VeryPoor, ExtremelyPoor) is not enabled.
  return status == chip::Protocols::InteractionModel::Status::Success
             ? CHIP_NO_ERROR
             : CHIP_ERROR_INVALID_ARGUMENT;
}

} // namespace esphome::matter

#endif // USE_MATTER_AIR_QUALITY
