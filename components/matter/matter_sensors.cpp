#include "matter_sensors.h"

#if defined(USE_MATTER) && (defined(USE_SENSOR) || defined(USE_BINARY_SENSOR))

#include "esphome/core/log.h"
#include "matter_component.h"

#include <app/AttributePathParams.h>
#include <app/clusters/boolean-state-server/BooleanStateCluster.h>
#include <app/clusters/concentration-measurement-server/ConcentrationMeasurementCluster.h>
#include <app/clusters/electrical-power-measurement-server/ElectricalPowerMeasurementDelegate.h>
#include <data_model_provider/clusters/electrical_energy_measurement/integration.h>
#include <data_model_provider/clusters/soil_measurement/integration.h>
#include <platform/CHIPDeviceLayer.h>
#include <system/SystemClock.h>

#include <cinttypes>
#include <cstdint>
#include <map>
#include <memory>
#include <set>
#include <utility>
#include <vector>

namespace esphome::matter {

static const char *const TAG = "matter.sensor";

namespace {

struct AttributeUpdate {
  uint16_t endpoint_id;
  uint32_t cluster_id;
  uint32_t attribute_id;
  esp_matter_attr_val_t value;
  SensorAttributeUpdater updater;
};

void update_attribute_on_matter_thread(intptr_t context) {
  auto *update = reinterpret_cast<AttributeUpdate *>(context);
  if (update->updater != nullptr) {
    CHIP_ERROR err =
        update->updater(update->endpoint_id, update->cluster_id, update->value);
    if (err != CHIP_NO_ERROR) {
      ESP_LOGE(TAG,
               "Failed to update attribute 0x%08" PRIX32
               " on cluster 0x%08" PRIX32 ", endpoint %u: %s",
               update->attribute_id, update->cluster_id, update->endpoint_id,
               err.AsString());
    }
    delete update;
    return;
  }

  esp_err_t err =
      esp_matter::attribute::update(update->endpoint_id, update->cluster_id,
                                    update->attribute_id, &update->value);
  if (err != ESP_OK) {
    ESP_LOGE(TAG,
             "Failed to update attribute 0x%08" PRIX32
             " on cluster 0x%08" PRIX32 ", endpoint %u: %s",
             update->attribute_id, update->cluster_id, update->endpoint_id,
             esp_err_to_name(err));
  }
  delete update;
}

void update_attribute(uint16_t endpoint_id, uint32_t cluster_id,
                      uint32_t attribute_id,
                      esp_matter_attr_val_t attribute_value,
                      SensorAttributeUpdater updater = nullptr) {
  auto *update = new AttributeUpdate{endpoint_id, cluster_id, attribute_id,
                                     attribute_value, updater};
  CHIP_ERROR err = chip::DeviceLayer::PlatformMgr().ScheduleWork(
      update_attribute_on_matter_thread, reinterpret_cast<intptr_t>(update));
  if (err != CHIP_NO_ERROR) {
    ESP_LOGE(TAG, "Failed to schedule Matter attribute update: %s",
             err.AsString());
    delete update;
  }
}

} // namespace

#ifdef USE_SENSOR
void MatterComponent::register_sensor_attribute(
    sensor::Sensor *sensor, uint16_t endpoint_id, uint32_t cluster_id,
    uint32_t attribute_id, SensorValueConverter converter) {
  this->mappings_.push_back(new MatterSensorAttributeMapping(
      sensor, endpoint_id, cluster_id, attribute_id, converter));
}

MatterSensorAttributeMapping::MatterSensorAttributeMapping(
    sensor::Sensor *sensor, uint16_t endpoint_id, uint32_t cluster_id,
    uint32_t attribute_id, SensorValueConverter converter,
    SensorAttributeUpdater updater)
    : MatterEndpointMappingBase(endpoint_id), sensor_(sensor),
      cluster_id_(cluster_id), attribute_id_(attribute_id),
      converter_(converter), updater_(updater) {}

void MatterSensorAttributeMapping::initialize() {
  if (this->sensor_ == nullptr || this->converter_ == nullptr)
    return;
  this->sensor_->add_on_state_callback(
      [this](float value) { this->publish_(value); });
  if (this->sensor_->has_state())
    this->publish_(this->sensor_->state);
}

void MatterSensorAttributeMapping::publish_(float value) {
  esp_matter_attr_val_t converted = this->converter_(value);
  // A converter returns an invalid value when the reading cannot be reported.
  if (converted.type == ESP_MATTER_VAL_TYPE_INVALID)
    return;
  update_attribute(this->endpoint_id_, this->cluster_id_, this->attribute_id_,
                   converted, this->updater_);
}

namespace {

namespace Concentration = chip::app::Clusters::ConcentrationMeasurement;
namespace EPM = chip::app::Clusters::ElectricalPowerMeasurement;

// Concentration clusters created by MatterConcentrationSensorMapping. Only
// touched from the Matter thread (or before it handles sensor updates).
std::set<std::pair<uint16_t, uint32_t>> owned_concentration_clusters;

void ensure_concentration_cluster(uint16_t endpoint_id, uint32_t cluster_id,
                                  uint8_t measurement_unit) {
  esp_matter::lock::ScopedChipStackLock lock(portMAX_DELAY);
  auto &registry =
      esp_matter::data_model::provider::get_instance().registry();
  if (registry.Get({endpoint_id, cluster_id}) != nullptr) {
    if (owned_concentration_clusters.count({endpoint_id, cluster_id}) == 0) {
      ESP_LOGW(TAG,
               "Cluster 0x%08" PRIX32
               " on endpoint %u already has a server. The MeasurementUnit "
               "cannot be set",
               cluster_id, endpoint_id);
    }
    return;
  }

  Concentration::ConcentrationMeasurementCluster::Config config{
      .clusterId = cluster_id,
      .features = chip::BitFlags<Concentration::Feature>(
          Concentration::Feature::kNumericMeasurement),
      .medium = Concentration::MeasurementMediumEnum::kAir,
      .unit = static_cast<Concentration::MeasurementUnitEnum>(measurement_unit),
  };
  // Lives as long as the program, like the endpoint it belongs to.
  auto *server = new chip::app::RegisteredServerCluster<
      Concentration::ConcentrationMeasurementCluster>(endpoint_id, config);
  CHIP_ERROR err = registry.Register(server->Registration());
  if (err != CHIP_NO_ERROR) {
    ESP_LOGE(TAG,
             "Failed to register cluster 0x%08" PRIX32 " on endpoint %u: %s",
             cluster_id, endpoint_id, err.AsString());
    delete server;
    return;
  }
  owned_concentration_clusters.insert({endpoint_id, cluster_id});
}

CHIP_ERROR update_concentration_attribute(uint16_t endpoint_id,
                                          uint32_t cluster_id,
                                          esp_matter_attr_val_t value) {
  if (owned_concentration_clusters.count({endpoint_id, cluster_id}) == 0)
    return CHIP_ERROR_NOT_FOUND;
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {endpoint_id, cluster_id});
  if (server == nullptr)
    return CHIP_ERROR_NOT_FOUND;

  chip::app::DataModel::Nullable<float> converted;
  if (!value.is_null())
    converted.SetNonNull(value.val.f);
  return static_cast<Concentration::ConcentrationMeasurementCluster *>(server)
      ->SetMeasuredValue(converted);
}

// The Soil Measurement cluster is served by a code driven cluster of
// esp-matter, which does not read the esp-matter attribute store.
CHIP_ERROR update_soil_moisture_attribute(uint16_t endpoint_id, uint32_t,
                                          esp_matter_attr_val_t value) {
  chip::app::DataModel::Nullable<chip::Percent> converted;
  if (!value.is_null())
    converted.SetNonNull(value.val.u8);
  return chip::app::Clusters::SoilMeasurement::SetSoilMoistureMeasuredValue(
      endpoint_id, converted);
}

// Provides the values of the ElectricalPowerMeasurement cluster. The values
// are pushed by sensors, the cluster reads them through this delegate.
class MatterElectricalPowerDelegate : public EPM::Delegate {
public:
  using Nullable64 = chip::app::DataModel::Nullable<int64_t>;

  // Registers a measurement type for the Accuracy list. ActivePower is
  // mandatory so it is always present.
  void enable(uint32_t attribute_id) {
    EPM::MeasurementTypeEnum type = to_measurement_type_(attribute_id);
    if (type == EPM::MeasurementTypeEnum::kUnspecified)
      return;
    for (auto existing : this->types_) {
      if (existing == type)
        return;
    }
    this->types_.push_back(type);
  }

  void set_alternating_current(bool ac) { this->ac_ = ac; }

  // Returns true if the value changed.
  bool set_value(uint32_t attribute_id, const Nullable64 &value) {
    Nullable64 *slot = this->slot_(attribute_id);
    if (slot == nullptr)
      return false;
    if (slot->IsNull() == value.IsNull() &&
        (value.IsNull() || slot->Value() == value.Value()))
      return false;
    *slot = value;
    return true;
  }

  EPM::PowerModeEnum GetPowerMode() override {
    return this->ac_ ? EPM::PowerModeEnum::kAc : EPM::PowerModeEnum::kDc;
  }
  uint8_t GetNumberOfMeasurementTypes() override {
    return static_cast<uint8_t>(this->types_.size());
  }

  CHIP_ERROR StartAccuracyRead() override { return CHIP_NO_ERROR; }
  CHIP_ERROR
  GetAccuracyByIndex(uint8_t index,
                     EPM::Structs::MeasurementAccuracyStruct::Type &accuracy)
      override {
    if (index >= this->types_.size())
      return CHIP_ERROR_PROVIDER_LIST_EXHAUSTED;
    // Nominal values. The real accuracy depends on the measuring hardware.
    this->range_.rangeMin = -kLimit;
    this->range_.rangeMax = kLimit;
    this->range_.percentMax =
        chip::MakeOptional(static_cast<chip::Percent100ths>(1000));
    accuracy.measurementType = this->types_[index];
    accuracy.measured = true;
    accuracy.minMeasuredValue = -kLimit;
    accuracy.maxMeasuredValue = kLimit;
    accuracy.accuracyRanges = chip::app::DataModel::List<
        const EPM::Structs::MeasurementAccuracyRangeStruct::Type>(&this->range_,
                                                                  1);
    return CHIP_NO_ERROR;
  }
  CHIP_ERROR EndAccuracyRead() override { return CHIP_NO_ERROR; }

  CHIP_ERROR StartRangesRead() override { return CHIP_NO_ERROR; }
  CHIP_ERROR GetRangeByIndex(
      uint8_t, EPM::Structs::MeasurementRangeStruct::Type &) override {
    return CHIP_ERROR_PROVIDER_LIST_EXHAUSTED;
  }
  CHIP_ERROR EndRangesRead() override { return CHIP_NO_ERROR; }

  CHIP_ERROR StartHarmonicCurrentsRead() override { return CHIP_NO_ERROR; }
  CHIP_ERROR GetHarmonicCurrentsByIndex(
      uint8_t, EPM::Structs::HarmonicMeasurementStruct::Type &) override {
    return CHIP_ERROR_PROVIDER_LIST_EXHAUSTED;
  }
  CHIP_ERROR EndHarmonicCurrentsRead() override { return CHIP_NO_ERROR; }

  CHIP_ERROR StartHarmonicPhasesRead() override { return CHIP_NO_ERROR; }
  CHIP_ERROR GetHarmonicPhasesByIndex(
      uint8_t, EPM::Structs::HarmonicMeasurementStruct::Type &) override {
    return CHIP_ERROR_PROVIDER_LIST_EXHAUSTED;
  }
  CHIP_ERROR EndHarmonicPhasesRead() override { return CHIP_NO_ERROR; }

  Nullable64 GetVoltage() override { return this->voltage_; }
  Nullable64 GetActiveCurrent() override { return this->active_current_; }
  Nullable64 GetReactiveCurrent() override { return this->reactive_current_; }
  Nullable64 GetApparentCurrent() override { return this->apparent_current_; }
  Nullable64 GetActivePower() override { return this->active_power_; }
  Nullable64 GetReactivePower() override { return this->reactive_power_; }
  Nullable64 GetApparentPower() override { return this->apparent_power_; }
  Nullable64 GetRMSVoltage() override { return this->rms_voltage_; }
  Nullable64 GetRMSCurrent() override { return this->rms_current_; }
  Nullable64 GetRMSPower() override { return this->rms_power_; }
  Nullable64 GetFrequency() override { return this->frequency_; }
  Nullable64 GetPowerFactor() override { return this->power_factor_; }
  Nullable64 GetNeutralCurrent() override { return this->neutral_current_; }

protected:
  static constexpr int64_t kLimit = 1000000000000LL;

  static EPM::MeasurementTypeEnum to_measurement_type_(uint32_t attribute_id) {
    switch (attribute_id) {
    case EPM::Attributes::Voltage::Id:
      return EPM::MeasurementTypeEnum::kVoltage;
    case EPM::Attributes::ActiveCurrent::Id:
      return EPM::MeasurementTypeEnum::kActiveCurrent;
    case EPM::Attributes::ReactiveCurrent::Id:
      return EPM::MeasurementTypeEnum::kReactiveCurrent;
    case EPM::Attributes::ApparentCurrent::Id:
      return EPM::MeasurementTypeEnum::kApparentCurrent;
    case EPM::Attributes::ActivePower::Id:
      return EPM::MeasurementTypeEnum::kActivePower;
    case EPM::Attributes::ReactivePower::Id:
      return EPM::MeasurementTypeEnum::kReactivePower;
    case EPM::Attributes::ApparentPower::Id:
      return EPM::MeasurementTypeEnum::kApparentPower;
    case EPM::Attributes::RMSVoltage::Id:
      return EPM::MeasurementTypeEnum::kRMSVoltage;
    case EPM::Attributes::RMSCurrent::Id:
      return EPM::MeasurementTypeEnum::kRMSCurrent;
    case EPM::Attributes::RMSPower::Id:
      return EPM::MeasurementTypeEnum::kRMSPower;
    case EPM::Attributes::Frequency::Id:
      return EPM::MeasurementTypeEnum::kFrequency;
    case EPM::Attributes::PowerFactor::Id:
      return EPM::MeasurementTypeEnum::kPowerFactor;
    case EPM::Attributes::NeutralCurrent::Id:
      return EPM::MeasurementTypeEnum::kNeutralCurrent;
    default:
      return EPM::MeasurementTypeEnum::kUnspecified;
    }
  }

  Nullable64 *slot_(uint32_t attribute_id) {
    switch (attribute_id) {
    case EPM::Attributes::Voltage::Id:
      return &this->voltage_;
    case EPM::Attributes::ActiveCurrent::Id:
      return &this->active_current_;
    case EPM::Attributes::ReactiveCurrent::Id:
      return &this->reactive_current_;
    case EPM::Attributes::ApparentCurrent::Id:
      return &this->apparent_current_;
    case EPM::Attributes::ActivePower::Id:
      return &this->active_power_;
    case EPM::Attributes::ReactivePower::Id:
      return &this->reactive_power_;
    case EPM::Attributes::ApparentPower::Id:
      return &this->apparent_power_;
    case EPM::Attributes::RMSVoltage::Id:
      return &this->rms_voltage_;
    case EPM::Attributes::RMSCurrent::Id:
      return &this->rms_current_;
    case EPM::Attributes::RMSPower::Id:
      return &this->rms_power_;
    case EPM::Attributes::Frequency::Id:
      return &this->frequency_;
    case EPM::Attributes::PowerFactor::Id:
      return &this->power_factor_;
    case EPM::Attributes::NeutralCurrent::Id:
      return &this->neutral_current_;
    default:
      return nullptr;
    }
  }

  bool ac_{true};
  // ActivePower is mandatory.
  std::vector<EPM::MeasurementTypeEnum> types_{
      EPM::MeasurementTypeEnum::kActivePower};
  EPM::Structs::MeasurementAccuracyRangeStruct::Type range_{};

  Nullable64 voltage_;
  Nullable64 active_current_;
  Nullable64 reactive_current_;
  Nullable64 apparent_current_;
  Nullable64 active_power_;
  Nullable64 reactive_power_;
  Nullable64 apparent_power_;
  Nullable64 rms_voltage_;
  Nullable64 rms_current_;
  Nullable64 rms_power_;
  Nullable64 frequency_;
  Nullable64 power_factor_;
  Nullable64 neutral_current_;
};

// One delegate per endpoint, created while the sensors are registered. The map
// is not modified after that.
std::map<uint16_t, MatterElectricalPowerDelegate *> electrical_power_delegates;

MatterElectricalPowerDelegate *get_electrical_power_delegate(uint16_t endpoint_id) {
  auto it = electrical_power_delegates.find(endpoint_id);
  if (it != electrical_power_delegates.end())
    return it->second;
  auto *delegate = new MatterElectricalPowerDelegate();
  electrical_power_delegates[endpoint_id] = delegate;
  return delegate;
}

struct ElectricalUpdate {
  uint16_t endpoint_id;
  MatterElectricalSensorMapping::Kind kind;
  uint32_t attribute_id;
  bool is_null;
  int64_t value;
};

void apply_electrical_update(intptr_t context) {
  std::unique_ptr<ElectricalUpdate> update(
      reinterpret_cast<ElectricalUpdate *>(context));
  using Kind = MatterElectricalSensorMapping::Kind;

  if (update->kind == Kind::POWER) {
    auto it = electrical_power_delegates.find(update->endpoint_id);
    if (it == electrical_power_delegates.end())
      return;
    MatterElectricalPowerDelegate::Nullable64 value;
    if (!update->is_null)
      value.SetNonNull(update->value);
    if (!it->second->set_value(update->attribute_id, value))
      return;
    esp_matter::data_model::provider::get_instance().ReportAttributeChanged(
        chip::app::AttributePathParams(update->endpoint_id, EPM::Id,
                                       update->attribute_id));
    return;
  }

  // Cumulative energy. A null value (unknown) can't undo a counter.
  if (update->is_null)
    return;
  namespace EEM = chip::app::Clusters::ElectricalEnergyMeasurement;
  EEM::Structs::EnergyMeasurementStruct::Type energy;
  energy.energy = update->value;
  energy.endSystime = chip::MakeOptional(static_cast<uint64_t>(
      chip::System::SystemClock().GetMonotonicTimestamp().count()));
  auto measured = chip::app::DataModel::MakeNullable(energy);
  chip::app::DataModel::Nullable<EEM::Structs::EnergyMeasurementStruct::Type>
      none;
  bool ok = update->kind == Kind::ENERGY_IMPORTED
                ? EEM::NotifyCumulativeEnergyMeasured(update->endpoint_id,
                                                      measured, none)
                : EEM::NotifyCumulativeEnergyMeasured(update->endpoint_id, none,
                                                      measured);
  if (!ok) {
    ESP_LOGE(TAG,
             "Failed to update the cumulative energy of endpoint %u. Is the "
             "ElectricalEnergyMeasurement cluster created with the matching "
             "features?",
             update->endpoint_id);
  }
}

} // namespace

void MatterComponent::register_concentration_sensor(
    sensor::Sensor *sensor, uint16_t endpoint_id, uint32_t cluster_id,
    uint32_t attribute_id, SensorValueConverter converter,
    uint8_t measurement_unit) {
  this->mappings_.push_back(new MatterConcentrationSensorMapping(
      sensor, endpoint_id, cluster_id, attribute_id, converter,
      measurement_unit));
}

void MatterComponent::register_soil_moisture_sensor(
    sensor::Sensor *sensor, uint16_t endpoint_id, uint32_t cluster_id,
    uint32_t attribute_id, SensorValueConverter converter) {
  this->mappings_.push_back(new MatterSensorAttributeMapping(
      sensor, endpoint_id, cluster_id, attribute_id, converter,
      update_soil_moisture_attribute));
}

void MatterComponent::register_electrical_power_sensor(
    sensor::Sensor *sensor, uint16_t endpoint_id, uint32_t attribute_id,
    SensorValueConverter converter) {
  this->mappings_.push_back(new MatterElectricalSensorMapping(
      sensor, endpoint_id, MatterElectricalSensorMapping::Kind::POWER,
      attribute_id, converter));
}

void MatterComponent::register_electrical_energy_sensor(
    sensor::Sensor *sensor, uint16_t endpoint_id, uint32_t attribute_id,
    SensorValueConverter converter) {
  namespace EEM = chip::app::Clusters::ElectricalEnergyMeasurement;
  auto kind = attribute_id == EEM::Attributes::CumulativeEnergyExported::Id
                  ? MatterElectricalSensorMapping::Kind::ENERGY_EXPORTED
                  : MatterElectricalSensorMapping::Kind::ENERGY_IMPORTED;
  this->mappings_.push_back(new MatterElectricalSensorMapping(
      sensor, endpoint_id, kind, attribute_id, converter));
}

MatterConcentrationSensorMapping::MatterConcentrationSensorMapping(
    sensor::Sensor *sensor, uint16_t endpoint_id, uint32_t cluster_id,
    uint32_t attribute_id, SensorValueConverter converter,
    uint8_t measurement_unit)
    : MatterSensorAttributeMapping(sensor, endpoint_id, cluster_id,
                                   attribute_id, converter,
                                   update_concentration_attribute),
      measurement_unit_(measurement_unit) {}

void MatterConcentrationSensorMapping::initialize() {
  ensure_concentration_cluster(this->endpoint_id_, this->cluster_id_,
                               this->measurement_unit_);
  MatterSensorAttributeMapping::initialize();
}

MatterElectricalSensorMapping::MatterElectricalSensorMapping(
    sensor::Sensor *sensor, uint16_t endpoint_id, Kind kind,
    uint32_t attribute_id, SensorValueConverter converter)
    : MatterEndpointMappingBase(endpoint_id), sensor_(sensor), kind_(kind),
      attribute_id_(attribute_id), converter_(converter) {
  if (kind == Kind::POWER)
    get_electrical_power_delegate(endpoint_id)->enable(attribute_id);
}

void MatterElectricalSensorMapping::initialize() {
  if (this->sensor_ == nullptr || this->converter_ == nullptr)
    return;
  this->sensor_->add_on_state_callback(
      [this](float value) { this->publish_(value); });
  if (this->sensor_->has_state())
    this->publish_(this->sensor_->state);
}

void MatterElectricalSensorMapping::publish_(float value) {
  esp_matter_attr_val_t converted = this->converter_(value);
  auto *update = new ElectricalUpdate{
      this->endpoint_id_, this->kind_, this->attribute_id_,
      converted.is_null(), converted.is_null() ? 0 : converted.val.i64};
  CHIP_ERROR err = chip::DeviceLayer::PlatformMgr().ScheduleWork(
      apply_electrical_update, reinterpret_cast<intptr_t>(update));
  if (err != CHIP_NO_ERROR) {
    ESP_LOGE(TAG, "Failed to schedule Matter attribute update: %s",
             err.AsString());
    delete update;
  }
}

void set_soil_moisture_limits(uint16_t endpoint_id) {
  namespace Globals = chip::app::Clusters::Globals;
  // Nominal accuracy. The real accuracy depends on the probe.
  static const Globals::Structs::MeasurementAccuracyRangeStruct::Type range{
      .rangeMin = 0,
      .rangeMax = 100,
      .percentMax = chip::MakeOptional<chip::Percent100ths>(1000),
  };
  chip::app::Clusters::SoilMeasurement::Attributes::
      SoilMoistureMeasurementLimits::TypeInfo::Type limits;
  limits.measurementType = Globals::MeasurementTypeEnum::kSoilMoisture;
  limits.measured = true;
  limits.minMeasuredValue = 0;
  limits.maxMeasuredValue = 100;
  limits.accuracyRanges =
      chip::app::DataModel::List<const Globals::Structs::
                                     MeasurementAccuracyRangeStruct::Type>(
          &range, 1);
  chip::app::Clusters::SoilMeasurement::SetSoilMoistureLimits(endpoint_id,
                                                              limits);
}

void *electrical_power_delegate(uint16_t endpoint_id,
                                bool alternating_current) {
  auto *delegate = get_electrical_power_delegate(endpoint_id);
  delegate->set_alternating_current(alternating_current);
  return static_cast<EPM::Delegate *>(delegate);
}
#endif // USE_SENSOR

#ifdef USE_BINARY_SENSOR
CHIP_ERROR update_boolean_state_attribute(uint16_t endpoint_id,
                                          uint32_t cluster_id,
                                          esp_matter_attr_val_t value) {
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {endpoint_id, cluster_id});
  if (server == nullptr)
    return CHIP_ERROR_NOT_FOUND;

  static_cast<chip::app::Clusters::BooleanStateCluster *>(server)
      ->SetStateValue(value.val.b);
  return CHIP_NO_ERROR;
}

void MatterComponent::register_binary_sensor_attribute(
    binary_sensor::BinarySensor *sensor, uint16_t endpoint_id,
    uint32_t cluster_id, uint32_t attribute_id,
    BinarySensorValueConverter converter, SensorAttributeUpdater updater) {
  this->mappings_.push_back(new MatterBinarySensorAttributeMapping(
      sensor, endpoint_id, cluster_id, attribute_id, converter, updater));
}

MatterBinarySensorAttributeMapping::MatterBinarySensorAttributeMapping(
    binary_sensor::BinarySensor *sensor, uint16_t endpoint_id,
    uint32_t cluster_id, uint32_t attribute_id,
    BinarySensorValueConverter converter, SensorAttributeUpdater updater)
    : MatterEndpointMappingBase(endpoint_id), sensor_(sensor),
      cluster_id_(cluster_id), attribute_id_(attribute_id),
      converter_(converter), updater_(updater) {}

void MatterBinarySensorAttributeMapping::initialize() {
  if (this->sensor_ == nullptr || this->converter_ == nullptr)
    return;
  this->sensor_->add_on_state_callback(
      [this](bool value) { this->publish_(value); });
  if (this->sensor_->has_state())
    this->publish_(this->sensor_->state);
}

void MatterBinarySensorAttributeMapping::publish_(bool value) {
  update_attribute(this->endpoint_id_, this->cluster_id_, this->attribute_id_,
                   this->converter_(value), this->updater_);
}
#endif // USE_BINARY_SENSOR

} // namespace esphome::matter

#endif // USE_MATTER && (USE_SENSOR || USE_BINARY_SENSOR)
