from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import esphome.codegen as cg
from esphome.components.binary_sensor import BinarySensor
from esphome.components.sensor import Sensor
from esphome.cpp_generator import MockObjClass

if TYPE_CHECKING:
    from .clusters import Cluster


@dataclass(frozen=True, slots=True)
class Attribute:
    id: int
    name: str | None  # CamelCase
    type: str
    # max: int | None = None
    # is_nullable: bool = False
    writable: bool = False
    optional: bool = False

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            id=data["id"],
            name=data.get("name"),
            type=data["type"],
            writable=data["writable"],
            optional=data["optional"],
        )


def attribute_value_type(attribute: Attribute):
    type_name = attribute.type
    if type_name == "boolean":
        return cg.bool_
    if type_name == "single":
        return cg.float_
    if type_name in ("char_string", "long_char_string"):
        return cg.std_string
    if type_name in (
        "int8s",
        "int16s",
        "int32s",
        "int64s",
        "temperature",
        "amperage_ma",
        "energy_mwh",
        "power_mva",
        "power_mvar",
        "power_mw",
        "voltage_mv",
    ):
        return cg.int64
    if (
        type_name
        in (
            "int8u",
            "int16u",
            "int24u",
            "int32u",
            "int64u",
            "enum8",
            "enum16",
            "bitmap16",
            "elapsed_s",
            "epoch_s",
            "epoch_us",
            "fabric_idx",
            "node_id",
            "percent",
            "percent100ths",
            "vendor_id",
        )
        or type_name.endswith("Enum")
        or type_name.endswith("Bitmap")
    ):
        return cg.uint64
    return None


@dataclass(frozen=True, slots=True)
class SensorAttribute:
    conf_key: str
    converter: str
    sensor_type: MockObjClass = field(default_factory=lambda: Sensor)
    features: tuple[str, ...] = ()
    code_driven: bool = False
    # Key in units.SENSOR_FAMILIES (or "concentration"). Drives the unit and
    # device_class validation and the conversion of the sensor value into the
    # canonical unit expected by the C++ converter.
    family: str | None = None
    # Optional attributes are not created by esp-matter. When True, the build
    # callback calls esp_matter::cluster::<cluster>::attribute::create_<name>().
    create_attribute: bool = False
    # How the sensor is wired up at runtime:
    #   attribute         - esp_matter attribute store (or code driven cluster)
    #   concentration     - ConcentrationMeasurementCluster owned by this component
    #   electrical_power  - ElectricalPowerMeasurement delegate
    #   electrical_energy - ElectricalEnergyMeasurement snapshots
    kind: str = "attribute"
    # Attribute only exists with the AlternatingCurrent feature.
    ac_only: bool = False
    # Set by DeviceType:
    cluster: "Cluster | None" = None
    attribute: Attribute | None = None


def _power(
    conf_key: str,
    converter: str,
    family: str,
    ac: bool = False,
    create: bool = True,
) -> SensorAttribute:
    return SensorAttribute(
        conf_key,
        converter,
        family=family,
        create_attribute=create,
        kind="electrical_power",
        ac_only=ac,
    )


def _concentration(conf_key: str) -> SensorAttribute:
    return SensorAttribute(
        conf_key,
        "concentration",
        features=("NumericMeasurement",),
        family="concentration",
        kind="concentration",
    )


# Arranged by Cluster, Attribute
SENSOR_ATTRIBUTES = {
    "BooleanState": {  # 0x0045
        "StateValue": SensorAttribute(
            "boolean_state", "boolean_state", BinarySensor, code_driven=True
        )
    },
    "OccupancySensing": {  # 0x0406
        "Occupancy": SensorAttribute("occupancy", "occupancy", BinarySensor)
    },
    "IlluminanceMeasurement": {  # 0x0400
        "MeasuredValue": SensorAttribute(
            "illuminance",
            "illuminance",
            code_driven=True,
            family="illuminance",
        )
    },
    "TemperatureMeasurement": {  # 0x0402
        "MeasuredValue": SensorAttribute(
            "temperature",
            "temperature",
            code_driven=True,
            family="temperature",
        )
    },
    "PressureMeasurement": {  # 0x0403
        "MeasuredValue": SensorAttribute(
            "pressure",
            "pressure",
            code_driven=True,
            family="pressure",
        )
    },
    "FlowMeasurement": {  # 0x0404
        "MeasuredValue": SensorAttribute(
            "flow",
            "flow",
            code_driven=True,
            family="flow",
        )
    },
    "RelativeHumidityMeasurement": {  # 0x0405
        "MeasuredValue": SensorAttribute(
            "relative_humidity",
            "percentage",
            code_driven=True,
            family="humidity",
        )
    },
    "ElectricalPowerMeasurement": {  # 0x0090
        # Values are exposed through a delegate (kind="electrical_power"). All
        # attributes except ActivePower are optional and must be created.
        "Voltage": _power("voltage", "volts", "voltage"),
        "ActiveCurrent": _power("active_current", "ampere", "current"),
        "ReactiveCurrent": _power("reactive_current", "ampere", "current", ac=True),
        "ApparentCurrent": _power("apparent_current", "ampere", "current", ac=True),
        "ActivePower": _power("active_power", "watts", "power", create=False),
        "ReactivePower": _power("reactive_power", "watts", "reactive_power", ac=True),
        "ApparentPower": _power("apparent_power", "watts", "apparent_power", ac=True),
        "RMSVoltage": _power("rms_voltage", "volts", "voltage", ac=True),
        "RMSCurrent": _power("rms_current", "ampere", "current", ac=True),
        "RMSPower": _power("rms_power", "watts", "power", ac=True),
        "Frequency": _power("frequency", "frequency", "frequency", ac=True),
        "PowerFactor": _power("power_factor", "power_factor", "power_factor", ac=True),
        "NeutralCurrent": _power("neutral_current", "ampere", "current", ac=True),
    },
    "ElectricalEnergyMeasurement": {  # 0x0091
        # kWh sensors, exposed as EnergyMeasurementStruct in mWh.
        "CumulativeEnergyImported": SensorAttribute(
            "energy_imported",
            "kilowatt_hours",
            features=("ImportedEnergy", "CumulativeEnergy"),
            family="energy",
            kind="electrical_energy",
        ),
        "CumulativeEnergyExported": SensorAttribute(
            "energy_exported",
            "kilowatt_hours",
            features=("ExportedEnergy", "CumulativeEnergy"),
            family="energy",
            kind="electrical_energy",
        ),
    },
    "PowerSource": {  # 0x002F
        # BatPercentRemaining is in half percent and requires the Battery
        # feature (a choice with Wired).
        "BatPercentRemaining": SensorAttribute(
            "battery",
            "battery_percent",
            features=("Battery",),
            family="battery",
            create_attribute=True,
        ),
    },
    "CarbonMonoxideConcentrationMeasurement": {  # 0x040C
        "MeasuredValue": _concentration("carbon_monoxide")
    },
    "CarbonDioxideConcentrationMeasurement": {  # 0x040D
        "MeasuredValue": _concentration("carbon_dioxide")
    },
    "NitrogenDioxideConcentrationMeasurement": {  # 0x0413
        "MeasuredValue": _concentration("nitrogen_dioxide")
    },
    "OzoneConcentrationMeasurement": {  # 0x0415
        "MeasuredValue": _concentration("ozone")
    },
    "PM25ConcentrationMeasurement": {  # 0x042A
        "MeasuredValue": _concentration("pm_2_5")
    },
    "FormaldehydeConcentrationMeasurement": {  # 0x042B
        "MeasuredValue": _concentration("formaldehyde")
    },
    "PM1ConcentrationMeasurement": {  # 0x042C
        "MeasuredValue": _concentration("pm_1")
    },
    "PM10ConcentrationMeasurement": {  # 0x042D
        "MeasuredValue": _concentration("pm_10")
    },
    "TotalVolatileOrganicCompoundsConcentrationMeasurement": {  # 0x042E
        "MeasuredValue": _concentration("total_voc")
    },
    "RadonConcentrationMeasurement": {  # 0x042F
        "MeasuredValue": _concentration("radon")
    },
    "SoilMeasurement": {  # 0x0430
        "SoilMoistureMeasuredValue": SensorAttribute("soil_moisture", "percentage")
    },
    # Not used by any device type?
    "SmokeConcentrationMeasurement": {  # 0x0434
        "MeasuredValue": _concentration("smoke_concentration")
    },
}
