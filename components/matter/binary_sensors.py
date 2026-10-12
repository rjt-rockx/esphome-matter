"""device_class aware handling of ESPHome binary_sensors bound to Matter device types.

See docs/binary-sensors.md. This module is deliberately self-contained: the rest of the component only calls
`extra_schema`, `resolve_binding`, `implied_occupancy_feature` and `check_binary_sensors`.
"""

import logging
from dataclasses import dataclass

import esphome.config_validation as cv
from esphome.components.binary_sensor import BinarySensor
from esphome.config import Config
from esphome.const import CONF_DEVICE_CLASS

from .const import CONF_ENDPOINTS, CONF_WITH_FEATURES

_LOGGER = logging.getLogger(__name__)

CONF_INVERTED = "inverted"

# ESPHome (like Home Assistant) reports ON = open for these device classes. Matter's BooleanState attribute, when used
# by the Contact Sensor device type, reports TRUE = closed (contact made). They are opposite.
OPENING_DEVICE_CLASSES = frozenset({"door", "window", "opening", "garage_door"})

# Accepted device classes per (device type, sensor attribute conf key). A binary_sensor without a device_class is always
# accepted (except for the polarity warning on contact_sensor). Any other class produces a configuration warning.
_ACCEPTED_DEVICE_CLASSES = {
    ("contact_sensor", "boolean_state"): OPENING_DEVICE_CLASSES,
    ("water_leak_detector", "boolean_state"): frozenset({"moisture"}),
    ("water_freeze_detector", "boolean_state"): frozenset({"cold"}),
    # ESPHome has no "rain" device class. "moisture" is the closest match.
    ("rain_sensor", "boolean_state"): frozenset({"moisture"}),
    ("occupancy_sensor", "occupancy"): frozenset({"motion", "occupancy", "presence"}),
    ("smoke_co_alarm", "smoke_state"): frozenset({"smoke"}),
    ("smoke_co_alarm", "co_state"): frozenset({"carbon_monoxide"}),
    ("smoke_co_alarm", "hardware_fault"): frozenset({"problem", "tamper"}),
    ("smoke_co_alarm", "end_of_service"): frozenset({"problem"}),
}

# Device types that device classes are a natural fit for. Used to make the warnings actionable.
_SUGGESTED_DEVICE_TYPES = {
    "door": "contact_sensor",
    "window": "contact_sensor",
    "opening": "contact_sensor",
    "garage_door": "contact_sensor",
    "moisture": "water_leak_detector",
    "cold": "water_freeze_detector",
    "motion": "occupancy_sensor",
    "occupancy": "occupancy_sensor",
    "presence": "occupancy_sensor",
    "smoke": "smoke_co_alarm (smoke_state)",
    "carbon_monoxide": "smoke_co_alarm (co_state)",
}

# Occupancy Sensing feature (choice feature, names from clusters.json) implied by the device_class of the bound
# binary_sensor when `features:` doesn't contain an explicit sensor-type feature. OccupancySensorType and
# OccupancySensorTypeBitmap are derived by connectedhomeip from the feature map: PassiveInfrared -> PIR,
# Ultrasonic -> Ultrasonic, PhysicalContact -> PhysicalContact. Every other feature (Other, ActiveInfrared, Radar,
# RFSensing, Vision) is reported as legacy type PIR with an empty type bitmap.
OCCUPANCY_FEATURE_BY_DEVICE_CLASS = {
    "motion": "PassiveInfrared",
    "presence": "Radar",
    "occupancy": "Other",
}
DEFAULT_OCCUPANCY_FEATURE = "Other"
OCCUPANCY_CHOICE_FEATURES = frozenset(
    {
        "Other",
        "PassiveInfrared",
        "Ultrasonic",
        "PhysicalContact",
        "ActiveInfrared",
        "Radar",
        "RFSensing",
        "Vision",
    }
)

# Header with the converters/updaters below. Only included in the generated code when needed because the updaters
# depend on cluster implementations that are only compiled when the cluster is enabled.
BINARY_SENSORS_INCLUDE = '#include "esphome/components/matter/matter_binary_sensors.h"'


def is_binary_attribute(sensor_attribute) -> bool:
    return sensor_attribute.sensor_type is BinarySensor


def inverted_key(device_type, sensor_attribute) -> str:
    """Config key of the polarity override. `inverted` unless the device type binds several binary sensors."""
    binary_attributes = [a for a in device_type.sensor_attributes if is_binary_attribute(a)]
    if len(binary_attributes) <= 1:
        return CONF_INVERTED
    return f"{sensor_attribute.conf_key}_{CONF_INVERTED}"


def extra_schema(device_type) -> dict:
    """Extra device type config options for device types that bind binary_sensors."""
    return {
        cv.Optional(inverted_key(device_type, attribute)): cv.boolean
        for attribute in device_type.sensor_attributes
        if is_binary_attribute(attribute)
    }


def get_device_class(full_config: Config, sensor_id) -> str | None:
    """Look up the device_class of a binary_sensor by ID. Returns None if not set or not found."""
    try:
        path = full_config.get_path_for_id(sensor_id)[:-1]
        sensor_config = full_config.get_config_for_path(path)
    except (KeyError, AttributeError):
        return None
    return sensor_config.get(CONF_DEVICE_CLASS) or None


def resolve_inverted(device_type, sensor_attribute, device_config: dict, device_class) -> bool:
    """Whether the binary_sensor state must be inverted before it is written to the Matter attribute.

    An explicit `inverted:` option always wins. Otherwise only contact_sensor is inverted automatically, and only
    when the binary_sensor has an opening device class (ON = open in ESPHome, StateValue TRUE = closed in Matter).
    """
    explicit = device_config.get(inverted_key(device_type, sensor_attribute))
    if explicit is not None:
        return explicit
    return device_type.name == "contact_sensor" and device_class in OPENING_DEVICE_CLASSES


# Smoke CO Alarm attribute (conf key) -> (converter in matter_binary_sensors.h, updater in matter_binary_sensors.h)
_SMOKE_CO_BINDINGS = {
    "smoke_state": ("alarm_state", "update_smoke_state_attribute"),
    "co_state": ("alarm_state", "update_co_state_attribute"),
    "hardware_fault": ("boolean_state", "update_hardware_fault_attribute"),
    "test_in_progress": ("boolean_state", "update_test_in_progress_attribute"),
    "end_of_service": ("end_of_service", "update_end_of_service_attribute"),
    "device_muted": ("mute_state", "update_device_muted_attribute"),
}


@dataclass(frozen=True)
class BinaryBinding:
    converter: str  # esphome::matter::conversion::<converter>
    updater: str | None  # esphome::matter::<updater> or None for a plain esp_matter attribute update
    include: str  # C++ include needed by the generated code
    inverted: bool


def resolve_binding(device_type, sensor_attribute, device_config: dict, device_class) -> BinaryBinding:
    inverted = resolve_inverted(device_type, sensor_attribute, device_config, device_class)
    suffix = "_inverted" if inverted else ""
    cluster_name = sensor_attribute.cluster.name
    if cluster_name == "BooleanState":
        return BinaryBinding(
            f"boolean_state{suffix}",
            "update_boolean_state_attribute",
            sensor_attribute.cluster.chip_include,
            inverted,
        )
    if cluster_name == "OccupancySensing":
        return BinaryBinding(
            f"occupancy{suffix}",
            "update_occupancy_attribute",
            BINARY_SENSORS_INCLUDE,
            inverted,
        )
    if cluster_name == "SmokeCOAlarm":
        converter, updater = _SMOKE_CO_BINDINGS[sensor_attribute.conf_key]
        return BinaryBinding(f"{converter}{suffix}", updater, BINARY_SENSORS_INCLUDE, inverted)
    raise cv.Invalid(
        f"Binary sensors cannot be bound to the {cluster_name} cluster ({sensor_attribute.conf_key})"
    )


def implied_occupancy_feature(device_config: dict, device_class) -> str | None:
    """Occupancy Sensing sensor-type feature to enable, or None if `features:` already contains one."""
    if OCCUPANCY_CHOICE_FEATURES.intersection(device_config.get(CONF_WITH_FEATURES, ())):
        return None
    return OCCUPANCY_FEATURE_BY_DEVICE_CLASS.get(device_class, DEFAULT_OCCUPANCY_FEATURE)


def check_binary_sensors(matter_config: dict, full_config: Config, device_types_by_key: dict):
    """Config time warnings for device_class mismatches and ambiguous contact sensor polarity."""
    for endpoint_id, endpoint_config in matter_config.get(CONF_ENDPOINTS, {}).items():
        for conf_key, device_config in endpoint_config.items():
            device_type = device_types_by_key.get(conf_key)
            if device_type is None or not isinstance(device_config, dict):
                continue
            for attribute in device_type.sensor_attributes:
                sensor_id = device_config.get(attribute.conf_key)
                if sensor_id is None or not is_binary_attribute(attribute):
                    continue
                _check_binding(
                    endpoint_id,
                    device_type,
                    attribute,
                    device_config,
                    sensor_id,
                    get_device_class(full_config, sensor_id),
                )


def _check_binding(endpoint_id, device_type, attribute, device_config, sensor_id, device_class):
    where = f"Matter endpoint {endpoint_id} {device_type.name}.{attribute.conf_key}"
    accepted = _ACCEPTED_DEVICE_CLASSES.get((device_type.name, attribute.conf_key))
    explicit_inversion = device_config.get(inverted_key(device_type, attribute))

    if device_class is not None and accepted is not None and device_class not in accepted:
        suggestion = _SUGGESTED_DEVICE_TYPES.get(device_class)
        hint = f" A '{device_class}' binary_sensor fits {suggestion}." if suggestion else ""
        _LOGGER.warning(
            "%s: binary_sensor '%s' has device_class '%s' which doesn't fit a %s. Expected one of: %s.%s",
            where,
            sensor_id,
            device_class,
            device_type.name,
            ", ".join(sorted(accepted)),
            hint,
        )

    if device_type.name == "contact_sensor" and explicit_inversion is None and device_class is None:
        _LOGGER.warning(
            "%s: binary_sensor '%s' has no device_class. Matter reports contact closed as TRUE, so ON is reported as "
            "'closed'. If ON means open (door, window, ...), set device_class (door, window, opening or "
            "garage_door) on the binary_sensor, or set `inverted: true`.",
            where,
            sensor_id,
        )

    if (
        device_type.name == "contact_sensor"
        and explicit_inversion is False
        and device_class in OPENING_DEVICE_CLASSES
    ):
        _LOGGER.warning(
            "%s: `inverted: false` reports ON (open, device_class '%s') as contact closed. This is almost "
            "certainly not intended; remove `inverted` to invert automatically.",
            where,
            device_class,
        )
