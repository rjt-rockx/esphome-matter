#pragma once

// Converter and Matter-thread updater of the Air Quality cluster's AirQuality attribute, for a sensor bound with
// `air_quality:`. The level is either the sensor value itself (a 0-6 index) or computed from thresholds by a lambda
// that the code generation emits. Header only, so nothing is compiled or linked unless the generated code uses it.

#include "esphome/core/defines.h"
#ifdef USE_MATTER_AIR_QUALITY

#include "esphome/core/log.h"
#include "matter_attributes.h"
#include "matter_endpoints.h"
#include "matter_push_bindings.h"
#ifdef USE_SENSOR
#include "esphome/components/sensor/sensor.h"
#endif

#include <app/clusters/air-quality-server/AirQualityCluster.h>
#include <clusters/AirQuality/Enums.h>
#include <data_model_provider/esp_matter_data_model_provider.h>
#include <esp_matter.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <vector>

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

// A level of the derived air quality and the sensor value above which it applies.
struct MatterAirQualityThreshold {
  uint8_t level;
  float above;
};

// AirQualityEnum level for a value. `thresholds` ascend, `index` is the number
// of thresholds the previous value was above (0 = Good), kept by the caller. A
// level is entered strictly above its threshold and left again once the value
// is at or below threshold - |threshold| * hysteresis, so a reading that
// hovers around a threshold does not flip the level. NaN is Unknown (0).
inline uint8_t air_quality_level(float value,
                                 const std::vector<MatterAirQualityThreshold> &thresholds,
                                 float hysteresis, size_t &index) {
  if (std::isnan(value)) {
    index = 0;
    return 0;
  }
  while (index < thresholds.size() && value > thresholds[index].above)
    index++;
  while (index > 0 &&
         value <= thresholds[index - 1].above -
                      std::fabs(thresholds[index - 1].above) * hysteresis)
    index--;
  return index == 0 ? 1 : thresholds[index - 1].level;
}

#ifdef USE_SENSOR
// Air Quality level derived from a measurement with thresholds. An ESPHome ->
// Matter attribute push of the binding framework (MatterAttributePushBinding):
// the newest level wins, the cluster is written on the CHIP task inside a
// PushScope.
class MatterAirQualityMapping : public MatterAttributePushBinding {
public:
  MatterAirQualityMapping(sensor::Sensor *sensor, uint16_t endpoint_id,
                          uint32_t cluster_id, uint32_t attribute_id,
                          std::vector<MatterAirQualityThreshold> thresholds,
                          float hysteresis)
      : MatterAttributePushBinding(endpoint_id, cluster_id, attribute_id),
        sensor_(sensor), thresholds_(std::move(thresholds)),
        hysteresis_(hysteresis) {}

protected:
  void setup_() override {
    if (this->sensor_ == nullptr)
      return;
    this->sensor_->add_on_state_callback(
        [this](float value) { this->publish_(value); });
    if (this->sensor_->has_state())
      this->publish_(this->sensor_->state);
  }

  // Main loop.
  ValueState capture_() override {
    ValueState s;
    if (this->published_ >= 0) {
      s.valid = true;
      s.value = esp_matter_enum8(static_cast<uint8_t>(this->published_));
    }
    return s;
  }

  // CHIP task, inside the PushScope.
  void write_attribute_(const ValueState &s) override {
    CHIP_ERROR err = update_air_quality_attribute(this->endpoint_id_,
                                                  this->cluster_id_, s.value);
    if (err != CHIP_NO_ERROR)
      ESP_LOGW("matter.air_quality",
               "Cannot set the air quality level %u on endpoint %u: %s",
               s.value.val.u8, this->endpoint_id_, err.AsString());
  }

  // Main loop.
  void publish_(float value) {
    uint8_t level =
        air_quality_level(value, this->thresholds_, this->hysteresis_, this->index_);
    if (level == this->published_)
      return;
    this->published_ = level;
    this->request_push();
  }

  sensor::Sensor *sensor_;
  std::vector<MatterAirQualityThreshold> thresholds_;
  float hysteresis_;
  size_t index_{0};
  int16_t published_{-1};
};
#endif // USE_SENSOR

} // namespace esphome::matter

#endif // USE_MATTER_AIR_QUALITY
