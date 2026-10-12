#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "matter_conversions.h"
#include "matter_endpoints.h"

#ifdef USE_BINARY_SENSOR
#include "esphome/components/binary_sensor/binary_sensor.h"
#endif // USE_BINARY_SENSOR
#ifdef USE_SENSOR
#include "esphome/components/sensor/sensor.h"
#endif // USE_SENSOR

#include <data_model_provider/esp_matter_data_model_provider.h>
#include <type_traits>

namespace esphome::matter {

using SensorValueConverter = esp_matter_attr_val_t (*)(float);
using BinarySensorValueConverter = esp_matter_attr_val_t (*)(bool);
using SensorAttributeUpdater = CHIP_ERROR (*)(uint16_t, uint32_t,
                                              esp_matter_attr_val_t);

#ifdef USE_SENSOR
template <
    typename ClusterT, typename ValueT,
    CHIP_ERROR (ClusterT::*Setter)(chip::app::DataModel::Nullable<ValueT>)>
CHIP_ERROR update_code_driven_sensor_attribute(uint16_t endpoint_id,
                                               uint32_t cluster_id,
                                               esp_matter_attr_val_t value) {
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {endpoint_id, cluster_id});
  if (server == nullptr)
    return CHIP_ERROR_NOT_FOUND;

  chip::app::DataModel::Nullable<ValueT> converted;
  if (!value.is_null()) {
    if constexpr (std::is_same_v<ValueT, int16_t>)
      converted.SetNonNull(value.val.i16);
    else if constexpr (std::is_same_v<ValueT, uint16_t>)
      converted.SetNonNull(value.val.u16);
  }
  return (static_cast<ClusterT *>(server)->*Setter)(converted);
}

class MatterSensorAttributeMapping : public MatterEndpointMappingBase {
public:
  MatterSensorAttributeMapping(sensor::Sensor *sensor, uint16_t endpoint_id,
                               uint32_t cluster_id, uint32_t attribute_id,
                               SensorValueConverter converter,
                               SensorAttributeUpdater updater = nullptr);

  void initialize() override;

protected:
  void publish_(float value);

  sensor::Sensor *sensor_;
  uint32_t cluster_id_;
  uint32_t attribute_id_;
  SensorValueConverter converter_;
  SensorAttributeUpdater updater_;
};

// esp-matter doesn't serve the Concentration Measurement clusters. The cluster
// is created and registered here, which also fixes its MeasurementUnit.
class MatterConcentrationSensorMapping : public MatterSensorAttributeMapping {
public:
  MatterConcentrationSensorMapping(sensor::Sensor *sensor, uint16_t endpoint_id,
                                   uint32_t cluster_id, uint32_t attribute_id,
                                   SensorValueConverter converter,
                                   uint8_t measurement_unit);

  void initialize() override;

protected:
  uint8_t measurement_unit_;
};

// Values of the ElectricalPowerMeasurement and ElectricalEnergyMeasurement
// clusters. The sensor value is converted into a nullable int64 (mW, mV, mA,
// mHz, 1/100 % or mWh).
class MatterElectricalSensorMapping : public MatterEndpointMappingBase {
public:
  enum class Kind : uint8_t { POWER, ENERGY_IMPORTED, ENERGY_EXPORTED };

  MatterElectricalSensorMapping(sensor::Sensor *sensor, uint16_t endpoint_id,
                                Kind kind, uint32_t attribute_id,
                                SensorValueConverter converter);

  void initialize() override;

protected:
  void publish_(float value);

  sensor::Sensor *sensor_;
  Kind kind_;
  uint32_t attribute_id_;
  SensorValueConverter converter_;
};

// Delegate of the ElectricalPowerMeasurement cluster of an endpoint, used in the
// esp-matter cluster config. Called while building an endpoint.
void *electrical_power_delegate(uint16_t endpoint_id, bool alternating_current);
#endif // USE_SENSOR

#ifdef USE_BINARY_SENSOR
CHIP_ERROR update_boolean_state_attribute(uint16_t endpoint_id,
                                          uint32_t cluster_id,
                                          esp_matter_attr_val_t value);

class MatterBinarySensorAttributeMapping : public MatterEndpointMappingBase {
public:
  MatterBinarySensorAttributeMapping(binary_sensor::BinarySensor *sensor,
                                     uint16_t endpoint_id, uint32_t cluster_id,
                                     uint32_t attribute_id,
                                     BinarySensorValueConverter converter,
                                     SensorAttributeUpdater updater = nullptr);

  void initialize() override;

protected:
  void publish_(bool value);

  binary_sensor::BinarySensor *sensor_;
  uint32_t cluster_id_;
  uint32_t attribute_id_;
  BinarySensorValueConverter converter_;
  SensorAttributeUpdater updater_;
};
#endif // USE_BINARY_SENSOR

} // namespace esphome::matter

#endif // USE_MATTER
