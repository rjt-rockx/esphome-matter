"""Sensor to Matter attribute mapping helpers.

This module contains everything that is specific to exposing ESPHome sensors:

* unit and device_class validation (done at config validation time),
* conversion of the sensor unit into the unit that the C++ converters expect,
* the Matter ``MeasurementUnit`` of concentration clusters,
* the endpoint level ``battery`` option,
* the code generation helpers for the electrical sensor device type.
"""

import logging
from dataclasses import dataclass, replace

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.const import CONF_DEVICE_CLASS, CONF_UNIT_OF_MEASUREMENT

from .const import CONF_ENDPOINTS
from .data_model.attributes import SENSOR_ATTRIBUTES, SensorAttribute
from .data_model.clusters import CLUSTERS_BY_NAME
from .data_model.units import (
    CONCENTRATION_UNITS,
    MEASUREMENT_UNITS,
    SENSOR_FAMILIES,
    concentration_default_unit,
    concentration_scale,
)
from .data_model import espmatter_names

_LOGGER = logging.getLogger(__name__)

# Endpoint level option that binds a battery level sensor (in %) to the
# BatPercentRemaining attribute of a Power Source cluster on that endpoint.
CONF_BATTERY = "battery"
# Device type option of the electrical_sensor.
CONF_CURRENT_TYPE = "current_type"
# Device type option to override the advertised MeasurementUnit of concentration sensors.
CONF_MEASUREMENT_UNITS = "measurement_units"

# ESPHome device classes that fit each concentration sensor attribute.
_CONCENTRATION_DEVICE_CLASSES = {
    "carbon_monoxide": ("carbon_monoxide",),
    "carbon_dioxide": ("carbon_dioxide",),
    "nitrogen_dioxide": ("nitrogen_dioxide",),
    "ozone": ("ozone",),
    "pm_1": ("pm1",),
    "pm_2_5": ("pm25",),
    "pm_10": ("pm10",),
    "total_voc": ("volatile_organic_compounds", "volatile_organic_compounds_parts"),
}

# Unit -> families that know about this unit. Used to detect a sensor that is
# clearly of another kind (e.g. a temperature sensor bound to the pressure).
_UNIT_FAMILIES: dict[str, set[str]] = {}
for _family_name, _family in SENSOR_FAMILIES.items():
    for _unit in _family.units:
        if _unit:
            _UNIT_FAMILIES.setdefault(_unit, set()).add(_family_name)
for _unit in CONCENTRATION_UNITS:
    _UNIT_FAMILIES.setdefault(_unit, set()).add("concentration")


def battery_sensor_attribute() -> SensorAttribute:
    """The SensorAttribute behind the endpoint level `battery` option."""
    cluster = CLUSTERS_BY_NAME["PowerSource"]
    return replace(
        SENSOR_ATTRIBUTES["PowerSource"]["BatPercentRemaining"],
        cluster=cluster,
        attribute=cluster.get_attribute("BatPercentRemaining"),
    )


def measurement_units_schema(sensor_attributes):
    """Schema for the `measurement_units` option, or None when not applicable."""
    concentration = [sa for sa in sensor_attributes if sa.kind == "concentration"]
    if not concentration:
        return None
    return cv.Schema(
        {
            cv.Optional(sa.conf_key): cv.one_of(*MEASUREMENT_UNITS, lower=True)
            for sa in concentration
        }
    )


def validate_battery_binding(config):
    """The battery sensor can be bound at endpoint level or in a device type, not both."""
    if CONF_BATTERY not in config:
        return config
    for conf_key, device_config in config.items():
        if isinstance(device_config, dict) and CONF_BATTERY in device_config:
            raise cv.Invalid(
                f"'{CONF_BATTERY}' is configured on the endpoint and in '{conf_key}'. "
                "Configure it only once"
            )
    return config


# --------------------------------------------------------------------------- #
# Unit planning
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SensorPlan:
    """How a sensor value is converted for a Matter attribute."""

    # canonical = value * scale + offset
    scale: float = 1.0
    offset: float = 0.0
    # Concentration: Matter MeasurementUnit name that is advertised.
    measurement_unit: str | None = None
    warnings: tuple[str, ...] = ()

    @property
    def is_identity(self) -> bool:
        return self.scale == 1.0 and self.offset == 0.0


def plan_sensor(
    sensor_attr: SensorAttribute,
    unit: str | None,
    device_class: str | None,
    override_unit: str | None = None,
) -> SensorPlan:
    """Determine the conversion for a sensor.

    Raises cv.Invalid if the sensor can never be a valid source for the attribute.
    Everything that is merely suspicious ends up in SensorPlan.warnings.
    """
    if sensor_attr.family is None:
        return SensorPlan()
    if sensor_attr.family == "concentration":
        return _plan_concentration(sensor_attr, unit, device_class, override_unit)
    return _plan_family(sensor_attr, unit, device_class)


def _plan_family(
    sensor_attr: SensorAttribute, unit: str | None, device_class: str | None
) -> SensorPlan:
    family = SENSOR_FAMILIES[sensor_attr.family]
    warnings: list[str] = []

    scale, offset = 1.0, 0.0
    if unit in family.units:
        scale, offset = family.units[unit]
    elif unit:
        others = sorted(_UNIT_FAMILIES.get(unit, set()) - {sensor_attr.family})
        if others:
            raise cv.Invalid(
                f"Sensor reports '{unit}' ({'/'.join(others)}), which cannot be used for "
                f"'{sensor_attr.conf_key}'. Expected a {sensor_attr.family} sensor in "
                f"'{family.canonical}'"
                + (
                    ". Use a template sensor to map a voltage to a battery level in %"
                    if sensor_attr.family == "battery"
                    else ""
                )
            )
        warnings.append(
            f"unit '{unit}' is not a known {sensor_attr.family} unit, assuming "
            f"'{family.canonical}'"
        )
    elif unit is None and family.canonical:
        warnings.append(
            f"it has no unit_of_measurement, assuming '{family.canonical}'. Set "
            "unit_of_measurement on the sensor to get the right conversion"
        )

    if device_class and device_class not in family.device_classes:
        warnings.append(
            f"device_class '{device_class}' does not look like a {sensor_attr.family} "
            f"sensor (expected {' or '.join(family.device_classes)})"
        )
    return SensorPlan(scale, offset, None, tuple(warnings))


def _plan_concentration(
    sensor_attr: SensorAttribute,
    unit: str | None,
    device_class: str | None,
    override_unit: str | None,
) -> SensorPlan:
    warnings: list[str] = []
    known = CONCENTRATION_UNITS.get(unit) if unit else None

    scale = 1.0
    if override_unit is not None:
        advertised = override_unit
        if known is not None:
            factor = concentration_scale(known[0], advertised)
            if factor is None:
                raise cv.Invalid(
                    f"Sensor reports '{unit}', which cannot be converted to the "
                    f"'{advertised}' MeasurementUnit. Remove the "
                    f"{CONF_MEASUREMENT_UNITS} override or use a different sensor"
                )
            scale = known[1] * factor
        elif unit:
            warnings.append(
                f"unit '{unit}' is not a known concentration unit, its values are "
                f"advertised as '{advertised}' without conversion"
            )
    elif known is not None:
        advertised, scale = known
    else:
        advertised = concentration_default_unit(sensor_attr.conf_key)
        if unit:
            others = sorted(_UNIT_FAMILIES.get(unit, set()) - {"concentration"})
            if others:
                raise cv.Invalid(
                    f"Sensor reports '{unit}' ({'/'.join(others)}), which cannot be used "
                    f"for '{sensor_attr.conf_key}'"
                )
            warnings.append(
                f"unit '{unit}' has no Matter MeasurementUnit equivalent, advertising "
                f"'{advertised}'. Use {CONF_MEASUREMENT_UNITS} to choose the unit"
            )
        else:
            warnings.append(
                f"it has no unit_of_measurement, advertising '{advertised}'. Set "
                f"unit_of_measurement on the sensor or {CONF_MEASUREMENT_UNITS} on the "
                "device type"
            )

    expected = _CONCENTRATION_DEVICE_CLASSES.get(sensor_attr.conf_key)
    if device_class and expected is not None and device_class not in expected:
        warnings.append(
            f"device_class '{device_class}' does not look like a "
            f"{sensor_attr.conf_key} sensor (expected {' or '.join(expected)})"
        )
    return SensorPlan(scale, 0.0, advertised, tuple(warnings))


def sensor_unit_info(full_config, sensor_id) -> tuple[str | None, str | None]:
    """Returns (unit_of_measurement, device_class) of an ESPHome sensor."""
    try:
        path = full_config.get_path_for_id(sensor_id)[:-1]
        sensor_config = full_config.get_config_for_path(path)
    except (KeyError, IndexError):
        return None, None
    if not isinstance(sensor_config, dict):
        return None, None
    return (
        sensor_config.get(CONF_UNIT_OF_MEASUREMENT),
        sensor_config.get(CONF_DEVICE_CLASS),
    )


def _bindings(endpoint_config: dict):
    """Yields (config path, SensorAttribute, sensor id, measurement units override)."""
    from .data_model.device_types import DEVICE_TYPES_BY_CONF_KEY

    for conf_key, device_config in endpoint_config.items():
        if conf_key == CONF_BATTERY and device_config is not None:
            yield [CONF_BATTERY], battery_sensor_attribute(), device_config, {}
            continue
        device_type = DEVICE_TYPES_BY_CONF_KEY.get(conf_key)
        if device_type is None or not isinstance(device_config, dict):
            continue
        overrides = device_config.get(CONF_MEASUREMENT_UNITS, {})
        for sensor_attr in device_type.sensor_attributes:
            sensor_id = device_config.get(sensor_attr.conf_key)
            if sensor_id is not None:
                yield (
                    [conf_key, sensor_attr.conf_key],
                    sensor_attr,
                    sensor_id,
                    overrides,
                )


def validate_sensor_bindings(matter_config: dict, full_config) -> None:
    """Warn or fail when a bound sensor does not fit the Matter attribute."""
    for endpoint_id, endpoint_config in matter_config.get(CONF_ENDPOINTS, {}).items():
        for path, sensor_attr, sensor_id, overrides in _bindings(endpoint_config):
            unit, device_class = sensor_unit_info(full_config, sensor_id)
            try:
                plan = plan_sensor(
                    sensor_attr, unit, device_class, overrides.get(sensor_attr.conf_key)
                )
            except cv.Invalid as err:
                raise cv.Invalid(
                    err.msg, path=[CONF_ENDPOINTS, endpoint_id, *path]
                ) from err
            for warning in plan.warnings:
                _LOGGER.warning(
                    "Matter endpoint %s: sensor '%s' mapped to '%s': %s",
                    endpoint_id,
                    sensor_id,
                    ".".join(path),
                    warning,
                )


def _float(value: float) -> str:
    return f"{value!r}f"


def converter_expression(sensor_attr: SensorAttribute, plan: SensorPlan):
    """C++ expression for the converter of a sensor.

    Plain converter functions are used when the sensor already is in the unit
    the converter expects. Otherwise a captureless lambda that converts the unit
    first is generated, which still is a plain function pointer.
    """
    base = f"esphome::matter::conversion::{sensor_attr.converter}"
    if plan.is_identity:
        return cg.RawExpression(base)
    value = f"v * {_float(plan.scale)}"
    if plan.offset != 0.0:
        value += f" + {_float(plan.offset)}"
    return cg.RawExpression(f"[](float v) {{ return {base}({value}); }}")


# --------------------------------------------------------------------------- #
# Code generation helpers
# --------------------------------------------------------------------------- #

POWER_SOURCE_STATUS_ACTIVE = 1


def optional_attribute_lines(
    created: list[tuple], initial_values: dict[tuple[int, int], list[str]] | None = None
) -> list[str]:
    """Lines that create optional attributes and set initial values.

    ``created`` holds (Cluster, Attribute) pairs of attributes that esp-matter
    does not create by itself. ``initial_values`` maps (cluster id, attribute id) to
    extra lines that run right after the attribute is created and can use the
    ``created_attribute`` variable.
    """
    lines: list[str] = []
    seen = set()
    for cluster, attribute in created:
        if (cluster.id, attribute.id) in seen:
            continue
        seen.add((cluster.id, attribute.id))
        cluster_var = f"{cluster.espm_namespace}_cluster"
        create_name = espmatter_names.attribute_create_function(
            cluster.name, attribute.name
        )
        if create_name is None:
            raise cv.Invalid(
                f"esp-matter cannot create the {attribute.name} attribute of the "
                f"{cluster.name} cluster"
            )
        create_fn = (
            f"esp_matter::cluster::{cluster.espm_namespace}::attribute::{create_name}"
        )
        lines.extend(
            (
                "{",
                f"  auto *{cluster_var} = esp_matter::cluster::get(endpoint, {cluster.id});",
                f"  if ({cluster_var} == nullptr || {create_fn}({cluster_var}, {{}}) == nullptr)",
                "    return false;",
            )
        )
        if cluster.name == "PowerSource":
            # Status is "Unspecified" by default.
            lines.extend(
                (
                    f"  auto *status_attr = esp_matter::attribute::get({cluster_var}, 0x0000);",
                    f"  esp_matter_attr_val_t status_val = esp_matter_enum8({POWER_SOURCE_STATUS_ACTIVE});",
                    "  if (status_attr != nullptr)",
                    "    esp_matter::attribute::set_val(status_attr, &status_val, false);",
                )
            )
        if extra := (initial_values or {}).get((cluster.id, attribute.id)):
            lines.append(
                f"  auto *created_attribute = esp_matter::attribute::get({cluster_var}, {attribute.id});"
            )
            lines.extend(extra)
        lines.append("}")
    return lines


def configure_electrical_sensor(
    cluster_configs, device_type, device_config
) -> list[str]:
    """Prepare cluster configs of an electrical_sensor.

    The electrical_sensor device type creates its measurement clusters itself,
    based on the with_*() builders of its config. Returns the extra feature
    flags that are not tracked in the cluster configs.
    """
    power_attrs = [
        sa
        for sa in device_type.sensor_attributes
        if sa.kind == "electrical_power" and device_config.get(sa.conf_key) is not None
    ]
    energy_attrs = [
        sa
        for sa in device_type.sensor_attributes
        if sa.kind == "electrical_energy" and device_config.get(sa.conf_key) is not None
    ]

    # The device type requires at least one of the two measurement clusters.
    with_power = bool(power_attrs) or not energy_attrs
    with_energy = bool(energy_attrs)

    # Required by the Power Topology cluster, the only topology that fits a single node.
    cluster_configs["PowerTopology"].enabled_features["NodeTopology"] = True

    extra_power_features: list[str] = []
    if with_power:
        config = cluster_configs["ElectricalPowerMeasurement"]
        config.created = True
        if device_config.get(CONF_CURRENT_TYPE, "ac") == "dc":
            config.enabled_features["DirectCurrent"] = True
        else:
            config.enabled_features["AlternatingCurrent"] = True
        if any(sa.conf_key == "neutral_current" for sa in power_attrs):
            extra_power_features.append("PolyphasePower")
    if with_energy:
        cluster_configs["ElectricalEnergyMeasurement"].created = True
    return extra_power_features


def electrical_config_lines(
    config_var: str, endpoint_id: int, cluster_configs, extra_power_features
) -> list[str]:
    """Lines that enable the optional clusters in the electrical_sensor config_t."""
    lines: list[str] = []
    for cluster_name, builder in (
        ("ElectricalPowerMeasurement", "with_electrical_power_measurement"),
        ("ElectricalEnergyMeasurement", "with_electrical_energy_measurement"),
    ):
        cluster = CLUSTERS_BY_NAME[cluster_name]
        # Don't index the defaultdict, that would register the cluster as an extra cluster.
        cluster_config = cluster_configs.get(cluster_name)
        if cluster_config is None or not cluster_config.created:
            continue
        features = [
            feature.namespace
            for feature in cluster.features
            if cluster_config.enabled_features.get(feature.name)
        ]
        if cluster_name == "ElectricalPowerMeasurement":
            features.extend(
                espmatter_names.feature_namespace(cluster_name, name)
                for name in extra_power_features
            )
        flags = " | ".join(
            f"esp_matter::cluster::{cluster.espm_namespace}::feature::{name}::get_id()"
            for name in features
        )
        target = f"{config_var}.{builder}()"
        if flags:
            lines.append(f"{target}.feature_flags = {flags};")
        else:
            lines.append(f"{target};")
        if cluster_name == "ElectricalPowerMeasurement":
            alternating = str(
                cluster_config.enabled_features.get("AlternatingCurrent", False)
            ).lower()
            lines.append(
                f"{target}.delegate = esphome::matter::electrical_power_delegate({endpoint_id}, {alternating});"
            )
    return lines


def validate_electrical_current_type(device_type, config: dict) -> dict:
    """DC power measurements only have voltage, current and active power."""
    if config.get(CONF_CURRENT_TYPE, "ac") != "dc":
        return config
    for sa in device_type.sensor_attributes:
        if sa.ac_only and config.get(sa.conf_key) is not None:
            raise cv.Invalid(
                f"'{sa.conf_key}' requires {CONF_CURRENT_TYPE}: ac. DC power "
                "measurements only support voltage, active_current and active_power"
            )
    return config
