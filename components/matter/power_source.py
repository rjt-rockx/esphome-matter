"""Power Source cluster support: the endpoint level `battery:` / `wired:` options.

A Power Source cluster can live on the root node (endpoint 0, with the Power Source
device type and an EndpointList that names the endpoints it powers) or on an
application endpoint. The cluster is created by the component itself; esp-matter
only creates the mandatory attributes of the chosen features, so every optional
attribute that is bound to an ESPHome entity is created explicitly (see
``sensor_mapping.optional_attribute_lines``).

YAML (see docs/power.md)::

    battery: battery_percent_sensor          # shorthand: BatPercentRemaining
    battery:
      percent: battery_percent_sensor        # BatPercentRemaining
      voltage: battery_voltage_sensor        # BatVoltage
      low: battery_low                       # BatChargeLevel Warning
      replacement_needed: change_battery     # BatReplacementNeeded
      charging: battery_charging             # RECHG: BatChargeState
      replaceability: user_replaceable       # REPLC
      replacement_description: CR2032
    wired:
      current_type: ac
      present: mains_present

One Power Source cluster per endpoint; a second source needs another endpoint.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components.binary_sensor import BinarySensor
from esphome.components.sensor import Sensor
from esphome.core import ID

from .const import CONF_CLUSTERS, CONF_ENDPOINTS, CONF_WITH_FEATURES
from .data_model.attributes import SENSOR_ATTRIBUTES, SensorAttribute
from .data_model.clusters import CLUSTERS_BY_NAME

_LOGGER = logging.getLogger(__name__)

CONF_BATTERY = "battery"
CONF_WIRED = "wired"

# battery:
CONF_PERCENT = "percent"
CONF_VOLTAGE = "voltage"
CONF_LOW = "low"
CONF_CRITICAL = "critical"
CONF_WARNING_BELOW = "warning_below"
CONF_CRITICAL_BELOW = "critical_below"
CONF_REPLACEMENT_NEEDED = "replacement_needed"
CONF_CHARGING = "charging"
CONF_CHARGED = "charged"
CONF_RECHARGEABLE = "rechargeable"
CONF_FUNCTIONAL_WHILE_CHARGING = "functional_while_charging"
CONF_REPLACEABILITY = "replaceability"
CONF_REPLACEMENT_DESCRIPTION = "replacement_description"
CONF_QUANTITY = "quantity"
# wired:
CONF_CURRENT_TYPE = "current_type"
CONF_PRESENT = "present"
CONF_FREQUENCY = "frequency"
CONF_CURRENT = "current"
# both:
CONF_ORDER = "order"
CONF_DESCRIPTION = "description"
CONF_POWERS = "powers"

POWER_SOURCE_CLUSTER = "PowerSource"
POWER_SOURCE_DEVICE_TYPE_ID = 0x0011
ROOT_ENDPOINT = 0
# Descriptions are limited to 60 bytes by the spec.
DESCRIPTION_MAX = 60
# PowerSourceStatusEnum: 1 = Active
STATUS_ACTIVE = 1
STATUS_STANDBY = 2
STATUS_UNAVAILABLE = 3
# (The Power Source XML has no maxCount for EndpointList, so `powers` is not capped.)

# BatReplaceabilityEnum
REPLACEABILITY = {
    "unspecified": 0,
    "not_replaceable": 1,
    "user_replaceable": 2,
    "factory_replaceable": 3,
}
# BatChargeLevelEnum
CHARGE_LEVEL_WARNING = 1
CHARGE_LEVEL_CRITICAL = 2
# BatChargeStateEnum
CHARGE_STATE_UNKNOWN = 0
CHARGE_STATE_CHARGING = 1
CHARGE_STATE_FULL = 2
CHARGE_STATE_NOT_CHARGING = 3
# WiredCurrentTypeEnum
CURRENT_TYPES = {"ac": 0, "dc": 1}

# Device type configs that can carry a Power Source cluster through their own
# `battery:` sensor option (see DeviceType.sensor_attributes).
_DEVICE_TYPE_BATTERY_KEY = "battery"


def _description_text(value):
    value = cv.string_strict(value)
    if not value or len(value.encode()) > DESCRIPTION_MAX:
        raise cv.Invalid(f"Must be 1 to {DESCRIPTION_MAX} bytes long, got '{value}'")
    return value


def _endpoint_no(value):
    return cv.int_range(min=0, max=65534)(value)


_ORDER = cv.int_range(min=0, max=254)
_POWERS = cv.All(
    cv.ensure_list(_endpoint_no),
    cv.Length(min=1),
)


def _unique(values, what):
    if len(set(values)) != len(values):
        raise cv.Invalid(f"{what} must not contain duplicates")
    return values


def _battery_schema() -> cv.Schema:
    return cv.Schema(
        {
            cv.Optional(CONF_PERCENT): cv.use_id(Sensor),
            cv.Optional(CONF_VOLTAGE): cv.use_id(Sensor),
            cv.Optional(CONF_LOW): cv.use_id(BinarySensor),
            cv.Optional(CONF_CRITICAL): cv.use_id(BinarySensor),
            cv.Optional(CONF_WARNING_BELOW): cv.float_range(min=0, max=100),
            cv.Optional(CONF_CRITICAL_BELOW): cv.float_range(min=0, max=100),
            cv.Optional(CONF_REPLACEMENT_NEEDED): cv.use_id(BinarySensor),
            cv.Optional(CONF_RECHARGEABLE): cv.boolean,
            cv.Optional(CONF_CHARGING): cv.use_id(BinarySensor),
            cv.Optional(CONF_CHARGED): cv.use_id(BinarySensor),
            cv.Optional(CONF_FUNCTIONAL_WHILE_CHARGING): cv.boolean,
            cv.Optional(CONF_REPLACEABILITY): cv.enum(REPLACEABILITY, lower=True),
            cv.Optional(CONF_REPLACEMENT_DESCRIPTION): _description_text,
            cv.Optional(CONF_QUANTITY): cv.int_range(min=1, max=255),
            cv.Optional(CONF_DESCRIPTION, default="Battery"): _description_text,
            cv.Optional(CONF_ORDER, default=0): _ORDER,
            cv.Optional(CONF_POWERS): _POWERS,
        }
    )


def _wired_schema() -> cv.Schema:
    return cv.Schema(
        {
            cv.Optional(CONF_CURRENT_TYPE, default="ac"): cv.one_of(
                *CURRENT_TYPES, lower=True
            ),
            cv.Optional(CONF_PRESENT): cv.use_id(BinarySensor),
            cv.Optional(CONF_VOLTAGE): cv.use_id(Sensor),
            cv.Optional(CONF_FREQUENCY): cv.use_id(Sensor),
            cv.Optional(CONF_CURRENT): cv.use_id(Sensor),
            cv.Optional(CONF_DESCRIPTION, default="Mains"): _description_text,
            cv.Optional(CONF_ORDER, default=0): _ORDER,
            cv.Optional(CONF_POWERS): _POWERS,
        }
    )


_BATTERY_SCHEMA = _battery_schema()
_WIRED_SCHEMA = _wired_schema()


def _validate_battery(value):
    if not isinstance(value, dict):
        # Shorthand: `battery: sensor_id` binds BatPercentRemaining.
        value = {CONF_PERCENT: cv.use_id(Sensor)(value)}
        return _BATTERY_SCHEMA(value)
    value = _BATTERY_SCHEMA(value)

    has_flags = CONF_LOW in value or CONF_CRITICAL in value
    has_thresholds = CONF_WARNING_BELOW in value or CONF_CRITICAL_BELOW in value
    if has_flags and CONF_PERCENT in value:
        raise cv.Invalid(
            f"'{CONF_LOW}' / '{CONF_CRITICAL}' cannot be combined with '{CONF_PERCENT}'. "
            f"With a percent sensor the charge level follows '{CONF_WARNING_BELOW}' and "
            f"'{CONF_CRITICAL_BELOW}'"
        )
    if has_thresholds and CONF_PERCENT not in value:
        raise cv.Invalid(
            f"'{CONF_WARNING_BELOW}' / '{CONF_CRITICAL_BELOW}' need a '{CONF_PERCENT}' sensor"
        )
    if has_thresholds:
        warning = value.get(CONF_WARNING_BELOW, DEFAULT_WARNING_BELOW)
        critical = value.get(CONF_CRITICAL_BELOW, DEFAULT_CRITICAL_BELOW)
        if critical > warning:
            raise cv.Invalid(
                f"'{CONF_CRITICAL_BELOW}' ({critical}"
                f"{'' if CONF_CRITICAL_BELOW in value else ', default'}) must not be above "
                f"'{CONF_WARNING_BELOW}' ({warning}"
                f"{'' if CONF_WARNING_BELOW in value else ', default'}). Set both thresholds"
            )

    rechargeable = bool(
        value.get(CONF_RECHARGEABLE) or CONF_CHARGING in value or CONF_CHARGED in value
    )
    if value.get(CONF_RECHARGEABLE) is False and (
        CONF_CHARGING in value or CONF_CHARGED in value
    ):
        raise cv.Invalid(
            f"'{CONF_CHARGING}' / '{CONF_CHARGED}' need a rechargeable battery, "
            f"but '{CONF_RECHARGEABLE}: false' is set"
        )
    if not rechargeable and CONF_FUNCTIONAL_WHILE_CHARGING in value:
        raise cv.Invalid(
            f"'{CONF_FUNCTIONAL_WHILE_CHARGING}' only applies to a rechargeable battery "
            f"('{CONF_RECHARGEABLE}', '{CONF_CHARGING}' or '{CONF_CHARGED}')"
        )

    replaceability = value.get(CONF_REPLACEABILITY)
    has_description = bool(value.get(CONF_REPLACEMENT_DESCRIPTION))
    if replaceability is None and has_description:
        replaceability = "user_replaceable"
        value = {**value, CONF_REPLACEABILITY: replaceability}
    if replaceability == "user_replaceable" and not has_description:
        raise cv.Invalid(
            f"'{CONF_REPLACEABILITY}: user_replaceable' needs '{CONF_REPLACEMENT_DESCRIPTION}', "
            "for example 'CR2032' (the Replaceable feature makes the description mandatory)"
        )
    if replaceability != "user_replaceable" and (
        has_description or CONF_QUANTITY in value
    ):
        raise cv.Invalid(
            f"'{CONF_REPLACEMENT_DESCRIPTION}' and '{CONF_QUANTITY}' only apply to "
            f"'{CONF_REPLACEABILITY}: user_replaceable'"
        )
    if CONF_POWERS in value:
        _unique(value[CONF_POWERS], CONF_POWERS)
    return value


def _validate_wired(value):
    value = _WIRED_SCHEMA(value if isinstance(value, dict) else {})
    if CONF_POWERS in value:
        _unique(value[CONF_POWERS], CONF_POWERS)
    return value


# Added to ENDPOINT_SCHEMA in endpoints.py.
SCHEMA_OPTIONS = {
    cv.Optional(CONF_BATTERY): _validate_battery,
    cv.Optional(CONF_WIRED): _validate_wired,
}


def validate_endpoint_power_source(config):
    """Endpoint level checks: one source per endpoint, no clash with other ways to get the cluster."""
    if CONF_BATTERY in config and CONF_WIRED in config:
        raise cv.Invalid(
            f"'{CONF_BATTERY}' and '{CONF_WIRED}' cannot be on the same endpoint: an endpoint "
            "has one Power Source cluster. Put the second source on another endpoint"
        )
    key = CONF_BATTERY if CONF_BATTERY in config else CONF_WIRED
    if key not in config:
        return config
    for conf_key, device_config in config.items():
        if conf_key in (CONF_BATTERY, CONF_WIRED):
            continue
        if (
            isinstance(device_config, dict)
            and _DEVICE_TYPE_BATTERY_KEY in device_config
        ):
            raise cv.Invalid(
                f"'{key}' is configured on the endpoint and '{_DEVICE_TYPE_BATTERY_KEY}' in "
                f"'{conf_key}'. Configure the Power Source only once"
            )
        if conf_key == "power_source":
            raise cv.Invalid(
                f"'{key}' cannot be combined with the power_source device type on the same endpoint"
            )
    if "power_source" in (config.get(CONF_CLUSTERS) or {}):
        raise cv.Invalid(
            f"'{key}' cannot be combined with the power_source cluster in 'clusters:'"
        )
    return config


# --------------------------------------------------------------------------- #
# Bindings
# --------------------------------------------------------------------------- #


def _attribute(name: str) -> tuple:
    cluster = CLUSTERS_BY_NAME[POWER_SOURCE_CLUSTER]
    return cluster, cluster.get_attribute(name)


def _sensor_attribute(
    conf_key: str,
    attribute_name: str,
    converter: str,
    family: str | None,
    *,
    features: tuple[str, ...],
    sensor_type=Sensor,
    create: bool = True,
) -> SensorAttribute:
    cluster, attribute = _attribute(attribute_name)
    return SensorAttribute(
        conf_key,
        converter,
        sensor_type,
        features=features,
        family=family,
        create_attribute=create,
        cluster=cluster,
        attribute=attribute,
    )


@dataclass(frozen=True)
class PowerBinding:
    """One ESPHome entity bound to an attribute of the Power Source cluster."""

    path: tuple[str, ...]
    sensor_attribute: SensorAttribute
    sensor_id: ID
    # Extra arguments for the conversion lambda of the charge level.
    thresholds: tuple[float, float] | None = None


def battery_percent_attribute() -> SensorAttribute:
    """The SensorAttribute behind `battery:` / `battery: {percent: ...}`."""
    cluster, attribute = _attribute("BatPercentRemaining")
    return replace(
        SENSOR_ATTRIBUTES[POWER_SOURCE_CLUSTER]["BatPercentRemaining"],
        cluster=cluster,
        attribute=attribute,
    )


def bindings(config: dict) -> list[PowerBinding]:
    """Sensor (float) and binary sensor bindings of the battery / wired option of an endpoint."""
    out: list[PowerBinding] = []
    battery = config.get(CONF_BATTERY)
    wired = config.get(CONF_WIRED)
    if battery:
        if CONF_PERCENT in battery:
            out.append(
                PowerBinding(
                    (CONF_BATTERY, CONF_PERCENT),
                    battery_percent_attribute(),
                    battery[CONF_PERCENT],
                )
            )
            out.append(
                PowerBinding(
                    (CONF_BATTERY, CONF_PERCENT),
                    _sensor_attribute(
                        "charge_level",
                        "BatChargeLevel",
                        "battery_charge_level",
                        # The unit was validated for BatPercentRemaining already.
                        None,
                        features=("Battery",),
                        create=False,
                    ),
                    battery[CONF_PERCENT],
                    thresholds=(
                        battery.get(CONF_WARNING_BELOW, DEFAULT_WARNING_BELOW),
                        battery.get(CONF_CRITICAL_BELOW, DEFAULT_CRITICAL_BELOW),
                    ),
                )
            )
        if CONF_VOLTAGE in battery:
            out.append(
                PowerBinding(
                    (CONF_BATTERY, CONF_VOLTAGE),
                    _sensor_attribute(
                        CONF_VOLTAGE,
                        "BatVoltage",
                        "millivolts",
                        "voltage",
                        features=("Battery",),
                    ),
                    battery[CONF_VOLTAGE],
                )
            )
        if CONF_REPLACEMENT_NEEDED in battery:
            out.append(
                PowerBinding(
                    (CONF_BATTERY, CONF_REPLACEMENT_NEEDED),
                    _sensor_attribute(
                        CONF_REPLACEMENT_NEEDED,
                        "BatReplacementNeeded",
                        "boolean_state",
                        None,
                        features=("Battery",),
                        sensor_type=BinarySensor,
                        create=False,
                    ),
                    battery[CONF_REPLACEMENT_NEEDED],
                )
            )
    if wired:
        for conf_key, attribute_name, converter, family in (
            (CONF_VOLTAGE, "WiredAssessedInputVoltage", "millivolts", "voltage"),
            (CONF_FREQUENCY, "WiredAssessedInputFrequency", "hertz", "frequency"),
            (CONF_CURRENT, "WiredAssessedCurrent", "milliamps", "current"),
        ):
            if conf_key in wired:
                out.append(
                    PowerBinding(
                        (CONF_WIRED, conf_key),
                        _sensor_attribute(
                            conf_key,
                            attribute_name,
                            converter,
                            family,
                            features=("Wired",),
                        ),
                        wired[conf_key],
                    )
                )
        if CONF_PRESENT in wired:
            out.append(
                PowerBinding(
                    (CONF_WIRED, CONF_PRESENT),
                    _sensor_attribute(
                        CONF_PRESENT,
                        "WiredPresent",
                        "boolean_state",
                        None,
                        features=("Wired",),
                        sensor_type=BinarySensor,
                    ),
                    wired[CONF_PRESENT],
                )
            )
    return out


DEFAULT_WARNING_BELOW = 20.0
DEFAULT_CRITICAL_BELOW = 10.0


# --------------------------------------------------------------------------- #
# Cluster structure
# --------------------------------------------------------------------------- #


def kind(config: dict) -> str | None:
    """ "battery", "wired" or None for the Power Source option of an endpoint."""
    if config.get(CONF_BATTERY):
        return CONF_BATTERY
    if config.get(CONF_WIRED) is not None:
        return CONF_WIRED
    return None


def features(config: dict) -> list[str]:
    """Power Source feature names the option selects."""
    which = kind(config)
    if which == CONF_WIRED:
        return ["Wired"]
    if which != CONF_BATTERY:
        return []
    battery = config[CONF_BATTERY]
    result = ["Battery"]
    if (
        battery.get(CONF_RECHARGEABLE)
        or CONF_CHARGING in battery
        or CONF_CHARGED in battery
    ):
        result.append("Rechargeable")
    if battery.get(CONF_REPLACEABILITY) == "user_replaceable":
        result.append("Replaceable")
    return result


def _c_string(text: str) -> str:
    out = []
    for char in text:
        if char in '\\"':
            out.append("\\" + char)
        elif char == "\n":
            out.append("\\n")
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\x{ord(char):02x}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def config_lines(target: str, config: dict) -> list[str]:
    """Initial values of the Power Source config_t (status, order, description, features)."""
    which = kind(config)
    if which is None:
        return []
    options = config[which]
    lines = [
        f"{target}.status = {STATUS_ACTIVE};",
        f"{target}.order = {options[CONF_ORDER]};",
        (
            f'snprintf({target}.description, sizeof({target}.description), "%s", '
            f"{_c_string(options[CONF_DESCRIPTION])});"
        ),
    ]
    if which == CONF_WIRED:
        lines.append(
            f"{target}.features.wired.wired_current_type = "
            f"{CURRENT_TYPES[options[CONF_CURRENT_TYPE]]};"
        )
        return lines
    replaceability = options.get(CONF_REPLACEABILITY)
    if replaceability is not None:
        lines.append(
            f"{target}.features.battery.bat_replaceability = {REPLACEABILITY[replaceability]};"
        )
    if "Rechargeable" in features(config):
        lines.append(
            f"{target}.features.rechargeable.bat_functional_while_charging = "
            f"{str(options.get(CONF_FUNCTIONAL_WHILE_CHARGING, True)).lower()};"
        )
        # Unknown until a charging / charged sensor has a state.
        lines.append(
            f"{target}.features.rechargeable.bat_charge_state = {CHARGE_STATE_UNKNOWN};"
        )
    if "Replaceable" in features(config):
        lines.append(
            f"snprintf({target}.features.replaceable.bat_replacement_description, "
            f'sizeof({target}.features.replaceable.bat_replacement_description), "%s", '
            f"{_c_string(options[CONF_REPLACEMENT_DESCRIPTION])});"
        )
        lines.append(
            f"{target}.features.replaceable.bat_quantity = {options.get(CONF_QUANTITY, 1)};"
        )
    return lines


def device_type_lines(target: str, device_config: dict) -> list[str]:
    """Defaults for the Power Source cluster created by the power_source device type.

    ``target`` is the cluster config member, e.g. ``device_config_0.power_source``.
    """
    wired = "Wired" in (device_config.get(CONF_WITH_FEATURES) or ())
    default = "Mains" if wired else "Battery"
    return [
        f"{target}.status = {STATUS_ACTIVE};",
        f"{target}.order = {device_config.get(CONF_ORDER, 0)};",
        (
            f'snprintf({target}.description, sizeof({target}.description), "%s", '
            f"{_c_string(device_config.get(CONF_DESCRIPTION, default))});"
        ),
    ]


def device_type_schema_options() -> dict:
    """`order` and `description` of the power_source device type."""
    return {
        cv.Optional(CONF_ORDER): _ORDER,
        cv.Optional(CONF_DESCRIPTION): _description_text,
    }


def root_device_type_lines() -> list[str]:
    """Adds the Power Source device type to the root node endpoint."""
    return [
        (
            "if (esp_matter::endpoint::add_device_type(endpoint, "
            "esp_matter::endpoint::power_source::get_device_type_id(), "
            "esp_matter::endpoint::power_source::get_device_type_version()) != ESP_OK)"
        ),
        "  return false;",
    ]


def endpoint_list(endpoint_id: int, config: dict, all_endpoints) -> list[int]:
    """Endpoints powered by the source: `powers:` or, on the root node, every other endpoint."""
    which = kind(config)
    if which is None:
        return []
    explicit = config[which].get(CONF_POWERS)
    if explicit is not None:
        return list(explicit)
    if endpoint_id == ROOT_ENDPOINT:
        return sorted(ep for ep in all_endpoints if ep != ROOT_ENDPOINT)
    return []


# --------------------------------------------------------------------------- #
# Code generation
# --------------------------------------------------------------------------- #

MATTER_POWER_INCLUDE = '#include "esphome/components/matter/matter_power.h"'


async def register(endpoint, var) -> None:
    """Registers the Power Source option of an `Endpoint` (endpoints.Endpoint)."""
    from esphome.core import CORE

    from .sensor_mapping import converter_expression, plan_sensor, sensor_unit_info

    config = endpoint.config
    which = kind(config)
    if which is None:
        return

    cluster = CLUSTERS_BY_NAME[POWER_SOURCE_CLUSTER]
    endpoint.enabled_sdkconfig_options.add(cluster.sdkconfig_option)
    cluster_config = endpoint.cluster_configs[cluster.name]
    for feature in features(config):
        cluster_config.enabled_features[feature] = True
    endpoint.power_source_config = config

    options = config[which]
    endpoint_id = endpoint.endpoint_id
    includes = endpoint.global_includes

    for binding in bindings(config):
        sensor_attribute = binding.sensor_attribute
        attribute = sensor_attribute.attribute
        if sensor_attribute.create_attribute:
            endpoint.optional_attributes.append((cluster, attribute))
        sensor = await cg.get_variable(binding.sensor_id)
        includes.add('#include "esphome/components/matter/matter_sensors.h"')
        if sensor_attribute.sensor_type is BinarySensor:
            cg.add(
                var.register_binary_sensor_attribute(
                    sensor,
                    endpoint_id,
                    cluster.id,
                    attribute.id,
                    cg.RawExpression("esphome::matter::conversion::boolean_state"),
                )
            )
            continue
        unit, device_class = sensor_unit_info(CORE.config, binding.sensor_id)
        plan = plan_sensor(sensor_attribute, unit, device_class)
        if binding.thresholds is not None:
            warning, critical = binding.thresholds
            converter = cg.RawExpression(
                "[](float v) { return esphome::matter::conversion::battery_charge_level("
                f"v, {warning!r}f, {critical!r}f); }}"
            )
        else:
            converter = converter_expression(sensor_attribute, plan)
        cg.add(
            var.register_sensor_attribute(
                sensor, endpoint_id, cluster.id, attribute.id, converter
            )
        )

    includes.add('#include "esphome/components/matter/matter_conversions.h"')
    if which == CONF_BATTERY:
        await _register_flag_enums(endpoint, var, options, cluster)
    await _register_status(endpoint, var, cluster, config)

    endpoints = endpoint_list(endpoint_id, config, endpoint.all_endpoint_ids)
    if endpoints:
        includes.add(MATTER_POWER_INCLUDE)
        values = ", ".join(str(ep) for ep in endpoints)
        cg.add(
            var.register_mapping(
                cg.RawExpression(
                    f"new esphome::matter::MatterPowerSourceEndpointList({endpoint_id}, {{{values}}})"
                )
            )
        )


async def _register_flag_enums(endpoint, var, options: dict, cluster) -> None:
    """BatChargeLevel (low / critical) and BatChargeState (charging / charged) from binary sensors."""
    # (attribute, value when no flag is ON, value while no sensor has a state or -1 to keep the
    # initial value, [(option, value)])
    groups = (
        (
            "BatChargeLevel",
            0,
            -1,  # the enum has no "unknown": keep OK until a sensor reports
            ((CONF_CRITICAL, CHARGE_LEVEL_CRITICAL), (CONF_LOW, CHARGE_LEVEL_WARNING)),
        ),
        (
            "BatChargeState",
            CHARGE_STATE_NOT_CHARGING,
            CHARGE_STATE_UNKNOWN,
            ((CONF_CHARGING, CHARGE_STATE_CHARGING), (CONF_CHARGED, CHARGE_STATE_FULL)),
        ),
    )
    for attribute_name, default, unknown, flags in groups:
        selected = [(key, value) for key, value in flags if key in options]
        if not selected:
            continue
        sensors = [(options[key], value) for key, value in selected]
        await _register_flag_mapping(
            endpoint, var, cluster, attribute_name, default, unknown, sensors
        )


async def _register_flag_mapping(
    endpoint, var, cluster, attribute_name, default, unknown, sensors
) -> None:
    attribute = cluster.get_attribute(attribute_name)
    entries = []
    for sensor_id, value in sensors:
        sensor = await cg.get_variable(sensor_id)
        entries.append(f"{{{sensor}, {value}}}")
    endpoint.global_includes.add(MATTER_POWER_INCLUDE)
    endpoint.global_includes.add(
        '#include "esphome/components/binary_sensor/binary_sensor.h"'
    )
    cg.add(
        var.register_mapping(
            cg.RawExpression(
                "new esphome::matter::MatterFlagEnumMapping("
                f"{endpoint.endpoint_id}, {cluster.id}, {attribute.id}, {default}, {unknown}, "
                f"{{{', '.join(entries)}}})"
            )
        )
    )


async def _register_status(endpoint, var, cluster, config: dict) -> None:
    """Status follows `wired.present` (Active / Unavailable); a lower-preference source is
    Standby while a preferred wired source with a `present` sensor is present."""
    which = kind(config)
    options = config[which]
    sources = endpoint.all_power_sources
    if which == CONF_WIRED:
        if CONF_PRESENT in options:
            await _register_flag_mapping(
                endpoint,
                var,
                cluster,
                "Status",
                STATUS_UNAVAILABLE,
                -1,
                [(options[CONF_PRESENT], STATUS_ACTIVE)],
            )
        return
    preferred = [
        present
        for other_id, other_kind, order, present in sources
        if other_id != endpoint.endpoint_id
        and other_kind == CONF_WIRED
        and present is not None
        and order < options[CONF_ORDER]
    ]
    if preferred:
        await _register_flag_mapping(
            endpoint,
            var,
            cluster,
            "Status",
            STATUS_ACTIVE,
            -1,
            [(sensor_id, STATUS_STANDBY) for sensor_id in preferred],
        )


def power_sources(endpoints: dict) -> list[tuple]:
    """(endpoint, "battery" | "wired", order, `present` binary sensor id or None) of every
    Power Source on the node: `battery:` / `wired:` and the power_source device type."""
    sources = []
    for endpoint_id, config in endpoints.items():
        which = kind(config)
        if which is not None:
            options = config[which]
            sources.append(
                (
                    endpoint_id,
                    which,
                    options[CONF_ORDER],
                    options.get(CONF_PRESENT) if which == CONF_WIRED else None,
                )
            )
            continue
        device = config.get("power_source")
        if isinstance(device, dict):
            wired = "Wired" in (device.get(CONF_WITH_FEATURES) or ())
            sources.append(
                (
                    endpoint_id,
                    CONF_WIRED if wired else CONF_BATTERY,
                    device.get(CONF_ORDER, 0),
                    None,
                )
            )
    return sources


# --------------------------------------------------------------------------- #
# Whole configuration checks
# --------------------------------------------------------------------------- #

# Keys of the root node endpoint (endpoint 0) that are allowed. The root node and its mandatory clusters are created by
# esp-matter; only utility device types can be added to it.
# The Electrical Sensor device type has endpoint scope in the device library, so it cannot be
# on the root node; the Power Source device type has node scope.
ROOT_ALLOWED_KEYS = frozenset(
    {"id", CONF_BATTERY, CONF_WIRED, "on_attribute", CONF_CLUSTERS}
)


def validate_root_endpoint(matter_config: dict) -> None:
    root = matter_config.get(CONF_ENDPOINTS, {}).get(ROOT_ENDPOINT)
    if root is None:
        return
    path = [CONF_ENDPOINTS, ROOT_ENDPOINT]
    for key, value in root.items():
        if key in ROOT_ALLOWED_KEYS:
            continue
        if key == "electrical_sensor":
            raise cv.Invalid(
                "The Electrical Sensor device type has endpoint scope and cannot be on the root node "
                "(endpoint 0). Put it on its own endpoint with 'topology: node' to measure the "
                "entire node",
                path=[*path, key],
            )
        if key in ("parent", "label", "tags"):
            raise cv.Invalid(
                f"'{key}' is not available on endpoint 0 (the root node)",
                path=[*path, key],
            )
        if value in (None, {}, []):
            continue
        raise cv.Invalid(
            f"Endpoint 0 is the root node. Only {', '.join(sorted(k for k in ROOT_ALLOWED_KEYS if k not in ('id',)))} "
            f"can be configured on it, not '{key}'. Use another endpoint number for application device types",
            path=[*path, key],
        )
    if root.get(CONF_CLUSTERS):
        raise cv.Invalid(
            "'clusters' is not available on endpoint 0 (the root node)",
            path=[*path, CONF_CLUSTERS],
        )
    if CONF_WITH_FEATURES in root:
        raise cv.Invalid("'with_features' is not available on endpoint 0", path=path)
    if kind(root) is None:
        raise cv.Invalid(
            "Endpoint 0 is the root node, which is created automatically. Configure "
            "'battery' or 'wired' on it, or remove it",
            path=path,
        )


def validate_powers(matter_config: dict) -> None:
    """`powers:` must name configured endpoints."""
    endpoints = matter_config.get(CONF_ENDPOINTS, {})
    for endpoint_id, config in endpoints.items():
        which = kind(config)
        if which is None:
            continue
        for target in config[which].get(CONF_POWERS, ()):
            if target not in endpoints:
                raise cv.Invalid(
                    f"'{CONF_POWERS}' lists endpoint {target}, which is not a configured endpoint",
                    path=[CONF_ENDPOINTS, endpoint_id, which, CONF_POWERS],
                )
            if target == ROOT_ENDPOINT:
                raise cv.Invalid(
                    f"'{CONF_POWERS}' cannot list the root node endpoint",
                    path=[CONF_ENDPOINTS, endpoint_id, which, CONF_POWERS],
                )


def validate_power_configuration(matter_config: dict) -> None:
    validate_root_endpoint(matter_config)
    validate_powers(matter_config)
    sources = power_sources(matter_config.get(CONF_ENDPOINTS, {}))
    by_order: dict[int, list[int]] = {}
    endpoints = matter_config.get(CONF_ENDPOINTS, {})
    for endpoint_id, _kind, order, _present in sources:
        if "bridged_node" in endpoints.get(endpoint_id, {}):
            continue  # the Power Source of a bridged device does not compete with the node's own sources
        by_order.setdefault(order, []).append(endpoint_id)
    for order, endpoint_ids in by_order.items():
        if len(endpoint_ids) > 1:
            _LOGGER.warning(
                "Matter endpoints %s have Power Sources with the same order (%s). The order says "
                "which source a node prefers (the lowest is used first); set 'order:' to tell "
                "them apart, for example 0 for mains and 1 for the battery backup",
                ", ".join(map(str, endpoint_ids)),
                order,
            )
