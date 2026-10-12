"""Optional features of the sensor and alarm clusters.

* Occupancy Sensing: ``hold_time`` (HoldTime, HoldTimeLimits and the legacy ``*OccupiedToUnoccupiedDelay`` aliases)
* Smoke CO Alarm: ``on_self_test``, ``hardware_fault``, ``end_of_service``, ``device_muted``, ``test_in_progress``
  (the binary_sensor bindings themselves are SensorAttributes, see data_model/attributes.py)
* Boolean State Configuration: ``alarms`` and ``sensitivity`` on the contact, leak, freeze, rain and occupancy sensors
* Air Quality: ``air_quality`` (a 0-6 index sensor, or derived from a concentration sensor with thresholds)
* Refrigerator Alarm (``door_open``) on the refrigerator device type. The cabinet mode cluster is a delegate layer
  cluster (delegates.py, ``clusters: refrigerator_and_temperature_controlled_cabinet_mode``)

Kept in a separate module so the shared endpoint code only needs a handful of hooks:
``extend_schema``/``validate_device_config`` (device type schema), ``validate_endpoint``, ``check_final`` (config time
checks), ``register`` (code generation, called while the device type is collected). See docs/sensors.md and
docs/binary-sensors.md.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome import automation
from esphome.components import binary_sensor, number, sensor
from esphome.const import CONF_DEVICE_CLASS, CONF_TRIGGER_ID, CONF_UNIT_OF_MEASUREMENT
from esphome.core import CORE, ID

from .const import CONF_ENDPOINTS, CONF_WITH_FEATURES
from .data_model.clusters import CLUSTERS_BY_NAME
from .types import matter_ns

_LOGGER = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# C++ types
# --------------------------------------------------------------------------- #

MatterEndpointMappingBase = matter_ns.class_("MatterEndpointMappingBase")
MatterOccupancyHoldTimeMapping = matter_ns.class_(
    "MatterOccupancyHoldTimeMapping", MatterEndpointMappingBase
)
MatterSmokeCoAlarmMapping = matter_ns.class_(
    "MatterSmokeCoAlarmMapping", MatterEndpointMappingBase
)
MatterSmokeCoSelfTestTrigger = matter_ns.class_(
    "MatterSmokeCoSelfTestTrigger", automation.Trigger.template()
)
MatterBooleanStateConfigMapping = matter_ns.class_(
    "MatterBooleanStateConfigMapping", MatterEndpointMappingBase
)
MatterAlarmsSuppressedTrigger = matter_ns.class_(
    "MatterAlarmsSuppressedTrigger", automation.Trigger.template(cg.bool_, cg.bool_)
)
MatterAlarmsActiveTrigger = matter_ns.class_(
    "MatterAlarmsActiveTrigger", automation.Trigger.template(cg.bool_, cg.bool_)
)
MatterRefrigeratorAlarmMapping = matter_ns.class_(
    "MatterRefrigeratorAlarmMapping", MatterEndpointMappingBase
)
MatterAirQualityMapping = matter_ns.class_(
    "MatterAirQualityMapping", MatterEndpointMappingBase
)
MatterSensorAttributeMapping = matter_ns.class_(
    "MatterSensorAttributeMapping", MatterEndpointMappingBase
)

# --------------------------------------------------------------------------- #
# Option names
# --------------------------------------------------------------------------- #

CONF_HOLD_TIME = "hold_time"
CONF_DEFAULT = "default"
CONF_MIN = "min"
CONF_MAX = "max"
CONF_NUMBER = "number"
CONF_LEGACY_ATTRIBUTES = "legacy_attributes"

CONF_ON_SELF_TEST = "on_self_test"
CONF_SELF_TEST_DURATION = "self_test_duration"

CONF_ALARMS = "alarms"
CONF_VISUAL = "visual"
CONF_AUDIBLE = "audible"
CONF_SUPPRESS = "suppress"
CONF_ACTIVE = "active"
CONF_ON_ACTIVE = "on_active"
CONF_ON_SUPPRESSED = "on_suppressed"
CONF_SENSITIVITY = "sensitivity"
CONF_LEVELS = "levels"

CONF_AIR_QUALITY = "air_quality"
CONF_SENSOR = "sensor"
CONF_THRESHOLDS = "thresholds"
CONF_HYSTERESIS = "hysteresis"

CONF_DOOR_OPEN = "door_open"

# Matter cluster ids
CLUSTER_OCCUPANCY_SENSING = 0x0406
CLUSTER_BOOLEAN_STATE_CONFIGURATION = 0x0080
CLUSTER_AIR_QUALITY = 0x005B
CLUSTER_REFRIGERATOR_ALARM = 0x0057

DEVICE_TYPE_OCCUPANCY_SENSOR = "occupancy_sensor"
DEVICE_TYPE_SMOKE_CO_ALARM = "smoke_co_alarm"
DEVICE_TYPE_EVSE = "evse"
REFRIGERATOR = "refrigerator"

CLUSTER_NAME_BSC = "BooleanStateConfiguration"
CLUSTER_NAME_AIR_QUALITY = "AirQuality"
CLUSTER_NAME_REFRIGERATOR_ALARM = "RefrigeratorAlarm"

# --------------------------------------------------------------------------- #
# Occupancy Sensing: HoldTime
# --------------------------------------------------------------------------- #

# The HoldTime and HoldTimeLimits attributes are uint16 seconds; 0xFFFF is not a valid hold time.
MAX_HOLD_TIME_SECONDS = 0xFFFE
# SDK defaults in esp-matter's Occupancy Sensing integration when HoldTime is enabled.
DEFAULT_HOLD_TIME_SECONDS = 30
DEFAULT_HOLD_TIME_MAX_SECONDS = 300
MIN_HOLD_TIME_MAX_SECONDS = (
    10  # HoldTimeMax >= max(10, HoldTimeMin) (Occupancy Sensing, HoldTimeLimits)
)


def _seconds(value) -> int:
    seconds = int(cv.positive_time_period_seconds(value).total_seconds)
    if not 1 <= seconds <= MAX_HOLD_TIME_SECONDS:
        raise cv.Invalid(f"must be between 1s and {MAX_HOLD_TIME_SECONDS}s")
    return seconds


def _validate_hold_time(config: dict) -> dict:
    config = dict(config)
    default = config.get(CONF_DEFAULT, DEFAULT_HOLD_TIME_SECONDS)
    minimum = config.get(CONF_MIN, 1)
    maximum = config.get(CONF_MAX, max(DEFAULT_HOLD_TIME_MAX_SECONDS, default, minimum))
    if maximum < MIN_HOLD_TIME_MAX_SECONDS:
        raise cv.Invalid(
            f"max must be at least {MIN_HOLD_TIME_MAX_SECONDS}s (Matter requires "
            "HoldTimeMax >= 10 seconds)"
        )
    if not minimum <= default <= maximum:
        raise cv.Invalid(
            f"hold time limits must satisfy min <= default <= max (got min {minimum}s, "
            f"default {default}s, max {maximum}s)"
        )
    config[CONF_DEFAULT] = default
    config[CONF_MIN] = minimum
    config[CONF_MAX] = maximum
    return config


HOLD_TIME_SCHEMA = cv.maybe_simple_value(
    cv.All(
        cv.Schema(
            {
                cv.Optional(CONF_DEFAULT): _seconds,
                cv.Optional(CONF_MIN): _seconds,
                cv.Optional(CONF_MAX): _seconds,
                cv.Optional(CONF_NUMBER): cv.use_id(number.Number),
                cv.Optional(CONF_LEGACY_ATTRIBUTES, default=True): cv.boolean,
            }
        ),
        _validate_hold_time,
    ),
    key=CONF_DEFAULT,
)

# sensor-type feature -> create function of the deprecated "OccupiedToUnoccupiedDelay" alias of HoldTime
_LEGACY_DELAY_ATTRIBUTES = {
    "PassiveInfrared": "create_pir_occupied_to_unoccupied_delay",
    "Ultrasonic": "create_ultrasonic_occupied_to_unoccupied_delay",
    "PhysicalContact": "create_physical_contact_occupied_to_unoccupied_delay",
}
_LEGACY_DELAY_NAMES = {
    "PassiveInfrared": "PIROccupiedToUnoccupiedDelay",
    "Ultrasonic": "UltrasonicOccupiedToUnoccupiedDelay",
    "PhysicalContact": "PhysicalContactOccupiedToUnoccupiedDelay",
}

# --------------------------------------------------------------------------- #
# Smoke CO Alarm
# --------------------------------------------------------------------------- #

DEFAULT_SELF_TEST_DURATION = "5s"

# --------------------------------------------------------------------------- #
# Boolean State Configuration
# --------------------------------------------------------------------------- #

BSC_ALARM_VISUAL = 0x01
BSC_ALARM_AUDIBLE = 0x02
MIN_SENSITIVITY_LEVELS = 2

# ESPHome binary sensor attributes of the device types that carry the Boolean State Configuration cluster.
_BSC_PRIMARY_KEYS = ("boolean_state", "occupancy")


def _bsc_alarms_schema():
    return cv.All(
        cv.Schema(
            {
                cv.Optional(CONF_VISUAL, default=False): cv.boolean,
                cv.Optional(CONF_AUDIBLE, default=False): cv.boolean,
                cv.Optional(CONF_SUPPRESS, default=False): cv.boolean,
                cv.Optional(CONF_ACTIVE): cv.use_id(binary_sensor.BinarySensor),
                cv.Optional(CONF_ON_ACTIVE): automation.validate_automation(
                    {
                        cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(
                            MatterAlarmsActiveTrigger
                        )
                    }
                ),
                cv.Optional(CONF_ON_SUPPRESSED): automation.validate_automation(
                    {
                        cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(
                            MatterAlarmsSuppressedTrigger
                        )
                    }
                ),
            }
        ),
        _validate_alarms,
    )


def _validate_alarms(config: dict) -> dict:
    if CONF_ON_SUPPRESSED in config and not config[CONF_SUPPRESS]:
        raise cv.Invalid("on_suppressed requires `suppress: true`")
    if not (config[CONF_VISUAL] or config[CONF_AUDIBLE]):
        raise cv.Invalid(
            "enable at least one of 'visual' or 'audible' (the alarms the device can signal)"
        )
    return config


def _bsc_sensitivity_schema():
    def check(config):
        levels = config[CONF_LEVELS]
        if CONF_DEFAULT in config and config[CONF_DEFAULT] >= levels:
            raise cv.Invalid(
                f"default must be a level index below levels ({levels}), 0 to {levels - 1}"
            )
        return config

    return cv.All(
        cv.Schema(
            {
                cv.Required(CONF_LEVELS): cv.int_range(min=2, max=10),
                cv.Optional(CONF_DEFAULT): cv.int_range(min=0, max=9),
                cv.Optional(CONF_NUMBER): cv.use_id(number.Number),
            }
        ),
        check,
    )


# --------------------------------------------------------------------------- #
# Air Quality
# --------------------------------------------------------------------------- #

# AirQualityEnum values. Good (1) is implied below the lowest threshold.
AIR_QUALITY_LEVELS = {
    "fair": 2,
    "moderate": 3,
    "poor": 4,
    "very_poor": 5,
    "extremely_poor": 6,
}
# Optional AirQuality features needed to report a level (Good and Poor are always available).
AIR_QUALITY_LEVEL_FEATURES = {
    "fair": "Fair",
    "moderate": "Moderate",
    "very_poor": "VeryPoor",
    "extremely_poor": "ExtremelyPoor",
}

# Thresholds for common concentration sensors, used when `thresholds` is omitted. A level applies to values strictly
# above its threshold (the upper end of the previous category), so a reading equal to a threshold still belongs to the
# lower level. They are indicative, not a Matter requirement:
#   pm25: US EPA AQI breakpoints, 2024 revision (ug/m3, 24 h average): Good 0-9.0, Moderate 9.1-35.4, Unhealthy for
#         Sensitive Groups 35.5-55.4, Unhealthy 55.5-125.4, Very Unhealthy 125.5-225.4, Hazardous 225.5 and above.
#   pm10: US EPA AQI breakpoints (ug/m3): Good 0-54, Moderate 55-154, USG 155-254, Unhealthy 255-354, Very Unhealthy
#         355-424, Hazardous 425 and above.
#   carbon_dioxide: common indoor air guidance (ppm).
# Format: device_class -> (unit, {level: threshold}).
AIR_QUALITY_PRESETS = {
    "pm25": (
        "ug/m3",
        {
            "fair": 9.0,
            "moderate": 35.4,
            "poor": 55.4,
            "very_poor": 125.4,
            "extremely_poor": 225.4,
        },
    ),
    "pm10": (
        "ug/m3",
        {
            "fair": 54.0,
            "moderate": 154.0,
            "poor": 254.0,
            "very_poor": 354.0,
            "extremely_poor": 424.0,
        },
    ),
    "carbon_dioxide": (
        "ppm",
        {
            "fair": 800.0,
            "moderate": 1000.0,
            "poor": 1500.0,
            "very_poor": 2000.0,
            "extremely_poor": 5000.0,
        },
    ),
}
# Spellings of a unit that are accepted for a preset.
_UNIT_SPELLINGS = {
    "ug/m3": {"µg/m³", "μg/m³", "ug/m3", "µg/m3", "μg/m3", "ug/m³"},
    "ppm": {"ppm"},
}
DEFAULT_AIR_QUALITY_HYSTERESIS = 0.02


def _validate_thresholds(thresholds: dict) -> dict:
    if not thresholds:
        raise cv.Invalid("thresholds needs at least one level")
    previous = None
    previous_name = None
    for name in AIR_QUALITY_LEVELS:
        if name not in thresholds:
            continue
        value = thresholds[name]
        if previous is not None and value <= previous:
            raise cv.Invalid(
                f"thresholds must increase with the level: '{name}' ({value}) must be "
                f"greater than '{previous_name}' ({previous})"
            )
        previous, previous_name = value, name
    return thresholds


def _air_quality_schema():
    derived = cv.Schema(
        {
            cv.Required(CONF_SENSOR): cv.use_id(sensor.Sensor),
            cv.Optional(CONF_THRESHOLDS): cv.All(
                cv.Schema(
                    {cv.Optional(name): cv.float_ for name in AIR_QUALITY_LEVELS}
                ),
                _validate_thresholds,
            ),
            # Fraction of a threshold that the value must fall below it before the level drops again.
            cv.Optional(
                CONF_HYSTERESIS, default=DEFAULT_AIR_QUALITY_HYSTERESIS
            ): cv.All(cv.percentage, cv.Range(min=0.0, max=0.25)),
        }
    )

    def validate(value):
        if isinstance(value, dict):
            return derived(value)
        # Short form: a sensor that already reports the AirQualityEnum value (0-6).
        return {CONF_SENSOR: cv.use_id(sensor.Sensor)(value), "direct": True}

    return validate


# --------------------------------------------------------------------------- #
# Schema hooks
# --------------------------------------------------------------------------- #


def _has_server_cluster(device_type, name: str) -> bool:
    return any(c.name == name for c in device_type.server_clusters)


def extend_schema(device_type, schema: dict) -> None:
    """Add the options of this module to a device type schema."""
    if device_type.name == DEVICE_TYPE_OCCUPANCY_SENSOR:
        schema[cv.Optional(CONF_HOLD_TIME)] = HOLD_TIME_SCHEMA
    if device_type.name == DEVICE_TYPE_SMOKE_CO_ALARM:
        schema[
            cv.Optional(CONF_SELF_TEST_DURATION, default=DEFAULT_SELF_TEST_DURATION)
        ] = _seconds
        schema[cv.Optional(CONF_ON_SELF_TEST)] = automation.validate_automation(
            {
                cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(
                    MatterSmokeCoSelfTestTrigger
                )
            }
        )
    if _has_server_cluster(device_type, CLUSTER_NAME_BSC):
        schema[cv.Optional(CONF_ALARMS)] = _bsc_alarms_schema()
        schema[cv.Optional(CONF_SENSITIVITY)] = _bsc_sensitivity_schema()
    if _has_server_cluster(device_type, CLUSTER_NAME_AIR_QUALITY):
        schema[cv.Optional(CONF_AIR_QUALITY)] = _air_quality_schema()
    if device_type.name == REFRIGERATOR:
        schema[cv.Optional(CONF_DOOR_OPEN)] = cv.use_id(binary_sensor.BinarySensor)


def creates_cluster(cluster_name: str, device_config: dict) -> bool:
    """True if options of the device type create the optional cluster on its own."""
    return cluster_name == CLUSTER_NAME_BSC and bool(
        CONF_ALARMS in device_config or CONF_SENSITIVITY in device_config
    )


def _add_features(config: dict, *features: str) -> dict:
    enabled = list(config.get(CONF_WITH_FEATURES, ()))
    for feature in features:
        if feature not in enabled:
            enabled.append(feature)
    config = dict(config)
    config[CONF_WITH_FEATURES] = enabled
    return config


def validate_device_config(device_type, config: dict) -> dict:
    """Cross-option validation; also enables the cluster features that follow from the options."""
    if device_type.name == DEVICE_TYPE_SMOKE_CO_ALARM:
        has_binding = any(
            config.get(key) is not None for key in ("smoke_state", "co_state")
        )
        selected = set(config.get(CONF_WITH_FEATURES, ()))
        if not has_binding and not selected & {"SmokeAlarm", "COAlarm"}:
            raise cv.Invalid(
                "smoke_co_alarm needs a smoke_state or co_state binary_sensor, or "
                "`with_features: [SmokeAlarm]` / `[COAlarm]`"
            )
        if (
            config.get("test_in_progress") is not None
            and CONF_ON_SELF_TEST not in config
        ):
            _LOGGER.info(
                "smoke_co_alarm binds test_in_progress without on_self_test: a self test requested "
                "from Matter stays active until the binary_sensor turns OFF"
            )

    features = []
    alarms = config.get(CONF_ALARMS)
    if alarms:
        if alarms[CONF_VISUAL]:
            features.append("Visual")
        if alarms[CONF_AUDIBLE]:
            features.append("Audible")
        if alarms[CONF_SUPPRESS]:
            features.append("AlarmSuppress")
    if CONF_SENSITIVITY in config:
        features.append("SensitivityLevel")

    if CONF_AIR_QUALITY in config:
        air_quality = config[CONF_AIR_QUALITY]
        if air_quality.get("direct"):
            features.extend(AIR_QUALITY_LEVEL_FEATURES.values())
        elif CONF_THRESHOLDS in air_quality:
            features.extend(
                feature
                for level, feature in AIR_QUALITY_LEVEL_FEATURES.items()
                if level in air_quality[CONF_THRESHOLDS]
            )
        else:
            # Preset thresholds (resolved against the sensor's device_class in check_final) use every level.
            features.extend(AIR_QUALITY_LEVEL_FEATURES.values())

    if features:
        config = _add_features(config, *features)
    return config


# --------------------------------------------------------------------------- #
# Final validation (needs the whole configuration)
# --------------------------------------------------------------------------- #


def _entity_config(full_config, entity_id) -> dict:
    try:
        path = full_config.get_path_for_id(entity_id)[:-1]
        entity_config = full_config.get_config_for_path(path)
    except (KeyError, IndexError, AttributeError):
        return {}
    return entity_config if isinstance(entity_config, dict) else {}


def resolve_air_quality(air_quality: dict, full_config) -> tuple[str, dict[str, float]]:
    """('direct', {}) or ('derived', {level: threshold}) for an `air_quality` option."""
    if air_quality.get("direct"):
        return "direct", {}
    if CONF_THRESHOLDS in air_quality:
        return "derived", dict(air_quality[CONF_THRESHOLDS])
    device_class = _entity_config(full_config, air_quality[CONF_SENSOR]).get(
        CONF_DEVICE_CLASS
    )
    preset = AIR_QUALITY_PRESETS.get(device_class)
    if preset is None:
        raise cv.Invalid(
            f"sensor '{air_quality[CONF_SENSOR]}' has "
            + (f"device_class '{device_class}'" if device_class else "no device_class")
            + f", which has no default air quality thresholds. Set `{CONF_THRESHOLDS}:` "
            f"(levels {', '.join(AIR_QUALITY_LEVELS)}: the level applies to values above the threshold) "
            f"or use a sensor with device_class {', '.join(AIR_QUALITY_PRESETS)}",
            path=[CONF_AIR_QUALITY],
        )
    return "derived", dict(preset[1])


def check_final(matter_config: dict, full_config) -> None:
    """Config time checks that need the other components."""
    from .data_model.device_types import DEVICE_TYPES_BY_CONF_KEY

    for endpoint_id, endpoint_config in matter_config.get(CONF_ENDPOINTS, {}).items():
        for conf_key, device_config in endpoint_config.items():
            device_type = DEVICE_TYPES_BY_CONF_KEY.get(conf_key)
            if device_type is None or not isinstance(device_config, dict):
                continue
            where = f"Matter endpoint {endpoint_id} {conf_key}"
            _check_hold_time(where, device_config, full_config)
            _check_sensitivity(where, device_config, full_config)
            _check_air_quality(endpoint_id, conf_key, where, device_config, full_config)


def _check_number_range(where, option, number_id, minimum, maximum, full_config):
    entity = _entity_config(full_config, number_id)
    low, high = entity.get("min_value"), entity.get("max_value")
    if low is not None and low > minimum:
        _LOGGER.warning(
            "%s: number '%s' (%s) has min_value %s, but Matter allows values from %s",
            where,
            number_id,
            option,
            low,
            minimum,
        )
    if high is not None and high < maximum:
        _LOGGER.warning(
            "%s: number '%s' (%s) has max_value %s, but Matter allows values up to %s",
            where,
            number_id,
            option,
            high,
            maximum,
        )
    if (low is not None and low < minimum) or (high is not None and high > maximum):
        _LOGGER.warning(
            "%s: number '%s' (%s) allows %s to %s, which is wider than the Matter limits %s to %s. "
            "Values outside the limits are clamped and the clamped value is shown again",
            where,
            number_id,
            option,
            low,
            high,
            minimum,
            maximum,
        )


def _check_hold_time(where, device_config, full_config):
    hold = device_config.get(CONF_HOLD_TIME)
    if hold and hold.get(CONF_NUMBER) is not None:
        _check_number_range(
            where,
            CONF_HOLD_TIME,
            hold[CONF_NUMBER],
            hold[CONF_MIN],
            hold[CONF_MAX],
            full_config,
        )


def _check_sensitivity(where, device_config, full_config):
    sensitivity = device_config.get(CONF_SENSITIVITY)
    if sensitivity and sensitivity.get(CONF_NUMBER) is not None:
        _check_number_range(
            where,
            CONF_SENSITIVITY,
            sensitivity[CONF_NUMBER],
            0,
            sensitivity[CONF_LEVELS] - 1,
            full_config,
        )


def _check_air_quality(endpoint_id, conf_key, where, device_config, full_config):
    air_quality = device_config.get(CONF_AIR_QUALITY)
    if not air_quality:
        return
    try:
        mode, _ = resolve_air_quality(air_quality, full_config)
    except cv.Invalid as err:
        raise cv.Invalid(
            err.msg, path=[CONF_ENDPOINTS, endpoint_id, conf_key, CONF_AIR_QUALITY]
        ) from err
    entity = _entity_config(full_config, air_quality[CONF_SENSOR])
    unit = entity.get(CONF_UNIT_OF_MEASUREMENT)
    if mode == "derived" and CONF_THRESHOLDS not in air_quality:
        # The preset thresholds are only valid in the unit they were defined for.
        expected, _ = AIR_QUALITY_PRESETS[entity.get(CONF_DEVICE_CLASS)]
        if unit not in _UNIT_SPELLINGS[expected]:
            raise cv.Invalid(
                f"sensor '{air_quality[CONF_SENSOR]}' reports "
                + (f"'{unit}'" if unit else "no unit_of_measurement")
                + f", but the default thresholds of device_class '{entity.get(CONF_DEVICE_CLASS)}' are in "
                f"'{expected}'. Set unit_of_measurement on the sensor or give `{CONF_THRESHOLDS}:` in its unit",
                path=[CONF_ENDPOINTS, endpoint_id, conf_key, CONF_AIR_QUALITY],
            )
    if mode == "direct" and unit:
        _LOGGER.warning(
            "%s: air_quality sensor '%s' reports '%s'. The short form expects a 0-6 index (0 unknown, 1 good, "
            "..., 6 extremely poor). Use `air_quality: {sensor: ..., thresholds: ...}` to derive the level "
            "from a measurement",
            where,
            air_quality[CONF_SENSOR],
            unit,
        )


# -- Extras ----------------------------------------------------------------- #


@dataclass
class Extra:
    """C++ the Endpoint adds for a cluster, evaluated lazily when the build callback is generated.

    ``creates_cluster``: ``lines`` creates the whole cluster (instead of the generic create call);
    otherwise it runs after all clusters of the endpoint exist. ``elements`` are ("attrs"|"cmds"|"events", spec
    name) pairs that the lines create in the esp-matter data model; the conformance tool reads them from the
    exported layout.
    """

    cluster: str
    lines: Callable[[], list[str]]
    elements: Callable[[], list[tuple[str, str]]] = list
    creates_cluster: bool = False


async def register(endpoint, var, device_type, device_config: dict) -> None:
    """Called by Endpoint._configure_device_type for every device type of an endpoint."""
    if (
        device_type.name == DEVICE_TYPE_OCCUPANCY_SENSOR
        and CONF_HOLD_TIME in device_config
    ):
        await _register_hold_time(endpoint, var, device_config)
    if device_type.name == DEVICE_TYPE_SMOKE_CO_ALARM:
        await _register_smoke_co(endpoint, var, device_config)
    if device_type.name == DEVICE_TYPE_EVSE:
        _register_evse_events(endpoint)
    if _has_server_cluster(device_type, CLUSTER_NAME_BSC) and creates_cluster(
        CLUSTER_NAME_BSC, device_config
    ):
        await _register_boolean_state_configuration(
            endpoint, var, device_type, device_config
        )
    if CONF_AIR_QUALITY in device_config:
        await _register_air_quality(endpoint, var, device_config)
    if device_config.get(CONF_DOOR_OPEN) is not None:
        await _register_refrigerator_alarm(endpoint, var, device_config)


def _create_block(cluster, calls: list[str]) -> list[str]:
    """A block that looks up the cluster and fails the endpoint build if any create call fails."""
    var = f"{cluster.espm_namespace}_cluster"
    conditions = " ||\n      ".join([f"{var} == nullptr", *calls])
    return [
        "{",
        f"  auto *{var} = esp_matter::cluster::get(endpoint, {cluster.id});",
        f"  if ({conditions})",
        '    return MATTER_BUILD_FAIL("sensor extras cluster create");',
        "}",
    ]


def _include(endpoint, header: str) -> None:
    endpoint.global_includes.add(f'#include "esphome/components/matter/{header}"')


def _declare(cls, name: str) -> ID:
    return ID(name, is_declaration=True, type=cls)


# -- Occupancy Sensing ------------------------------------------------------ #


async def _register_hold_time(endpoint, var, device_config: dict) -> None:
    hold = device_config[CONF_HOLD_TIME]
    cluster = CLUSTERS_BY_NAME["OccupancySensing"]
    namespace = f"esp_matter::cluster::{cluster.espm_namespace}::attribute"
    cluster_var = f"{cluster.espm_namespace}_cluster"
    cluster_config = endpoint._cluster_configs[cluster.name]

    def legacy_features() -> list[str]:
        # The deprecated per sensor-type aliases of HoldTime exist for the PIR, Ultrasonic and PhysicalContact
        # features only; the cluster serves them from the same state as HoldTime.
        if not hold[CONF_LEGACY_ATTRIBUTES]:
            return []
        return [
            f
            for f in _LEGACY_DELAY_ATTRIBUTES
            if cluster_config.enabled_features.get(f)
        ]

    def lines() -> list[str]:
        calls = [
            f"{namespace}::create_hold_time({cluster_var}, {hold[CONF_DEFAULT]}) == nullptr",
            f"{namespace}::create_hold_time_limits({cluster_var}, nullptr, 0, 0) == nullptr",
        ]
        calls.extend(
            f"{namespace}::{_LEGACY_DELAY_ATTRIBUTES[feature]}({cluster_var}, {hold[CONF_DEFAULT]}) == nullptr"
            for feature in legacy_features()
        )
        return _create_block(cluster, calls)

    def elements() -> list[tuple[str, str]]:
        return [("attrs", "HoldTime"), ("attrs", "HoldTimeLimits")] + [
            ("attrs", _LEGACY_DELAY_NAMES[feature]) for feature in legacy_features()
        ]

    endpoint.extras.append(Extra(cluster.name, lines, elements))

    _include(endpoint, "matter_occupancy.h")
    cg.add_define("USE_MATTER_OCCUPANCY_HOLD_TIME")
    mapping = cg.new_Pvariable(
        _declare(
            MatterOccupancyHoldTimeMapping, f"matter_hold_time_{endpoint.endpoint_id}"
        ),
        endpoint.endpoint_id,
        hold[CONF_MIN],
        hold[CONF_MAX],
        hold[CONF_DEFAULT],
    )
    if hold.get(CONF_NUMBER) is not None:
        cg.add(mapping.set_number(await cg.get_variable(hold[CONF_NUMBER])))
    cg.add(var.register_mapping(mapping))


# -- Energy EVSE ------------------------------------------------------------ #

# esp-matter's Energy EVSE creator makes none of the events that the cluster makes mandatory (Energy EVSE cluster,
# Events: EVConnected, EVNotDetected, EnergyTransferStarted, EnergyTransferStopped, Fault).
_EVSE_EVENTS = (
    ("ev_connected", "EVConnected"),
    ("ev_not_detected", "EVNotDetected"),
    ("energy_transfer_started", "EnergyTransferStarted"),
    ("energy_transfer_stopped", "EnergyTransferStopped"),
    ("fault", "Fault"),
)


def _register_evse_events(endpoint) -> None:
    cluster = CLUSTERS_BY_NAME["EnergyEVSE"]
    namespace = f"esp_matter::cluster::{cluster.espm_namespace}"
    cluster_var = f"{cluster.espm_namespace}_cluster"

    def lines() -> list[str]:
        return _create_block(
            cluster,
            [
                f"{namespace}::event::create_{function}({cluster_var}) == nullptr"
                for function, _name in _EVSE_EVENTS
            ],
        )

    def elements() -> list[tuple[str, str]]:
        return [("events", name) for _function, name in _EVSE_EVENTS]

    endpoint.extras.append(Extra(cluster.name, lines, elements))


# -- Smoke CO Alarm --------------------------------------------------------- #


async def _register_smoke_co(endpoint, var, device_config: dict) -> None:
    cluster = CLUSTERS_BY_NAME["SmokeCOAlarm"]
    namespace = f"esp_matter::cluster::{cluster.espm_namespace}"
    cluster_var = f"{cluster.espm_namespace}_cluster"
    muted = device_config.get("device_muted") is not None

    def lines() -> list[str]:
        # The code driven cluster accepts SelfTestRequest on its own; the esp-matter command only makes the
        # data model complete.
        calls = [
            f"{namespace}::command::create_self_test_request({cluster_var}) == nullptr"
        ]
        if muted:
            calls.append(
                f"{namespace}::event::create_alarm_muted({cluster_var}) == nullptr"
            )
            calls.append(
                f"{namespace}::event::create_mute_ended({cluster_var}) == nullptr"
            )
        return _create_block(cluster, calls)

    def elements() -> list[tuple[str, str]]:
        result = [("cmds", "SelfTestRequest")]
        if muted:
            result += [("events", "AlarmMuted"), ("events", "MuteEnded")]
        return result

    endpoint.extras.append(Extra(cluster.name, lines, elements))

    _include(endpoint, "matter_smoke_co.h")
    cg.add_define("USE_MATTER_SMOKE_CO")
    # A self test that the application does not track ends after `self_test_duration`.
    complete_ms = (
        0
        if device_config.get("test_in_progress") is not None
        else device_config[CONF_SELF_TEST_DURATION] * 1000
    )
    mapping = cg.new_Pvariable(
        _declare(MatterSmokeCoAlarmMapping, f"matter_smoke_co_{endpoint.endpoint_id}"),
        endpoint.endpoint_id,
        complete_ms,
    )
    for conf in device_config.get(CONF_ON_SELF_TEST, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID])
        await automation.build_automation(trigger, [], conf)
        cg.add(mapping.add_self_test_trigger(trigger))
    cg.add(var.register_mapping(mapping))


# -- Boolean State Configuration -------------------------------------------- #


async def _register_boolean_state_configuration(
    endpoint, var, device_type, device_config: dict
) -> None:
    cluster = CLUSTERS_BY_NAME[CLUSTER_NAME_BSC]
    namespace = f"esp_matter::cluster::{cluster.espm_namespace}"
    alarms = device_config.get(CONF_ALARMS)
    sensitivity = device_config.get(CONF_SENSITIVITY)
    # `alarms` and `sensitivity` enable the features (validate_device_config); `with_features` alone works too and
    # advertises the minimum (the alarms of the enabled features, two sensitivity levels).
    features = set(device_config.get(CONF_WITH_FEATURES, ()))
    visual = "Visual" in features
    audible = "Audible" in features
    suppress = "AlarmSuppress" in features
    levels = (
        sensitivity[CONF_LEVELS]
        if sensitivity
        else (MIN_SENSITIVITY_LEVELS if "SensitivityLevel" in features else None)
    )
    supported = (BSC_ALARM_VISUAL if visual else 0) | (
        BSC_ALARM_AUDIBLE if audible else 0
    )
    # Makes sure the endpoint creates the cluster (its features were enabled by validate_device_config).
    endpoint._cluster_configs[cluster.name]

    def lines() -> list[str]:
        out = [
            f"{namespace}::config_t boolean_state_configuration_config{{}};",
            (
                f"auto *boolean_state_configuration_cluster = {namespace}::create(endpoint, "
                "&boolean_state_configuration_config, esp_matter::CLUSTER_FLAG_SERVER);"
            ),
            "if (boolean_state_configuration_cluster == nullptr)",
            '  return MATTER_BUILD_FAIL("boolean state configuration cluster create");',
        ]

        def add_feature(feature: str, fields: dict[str, int]) -> None:
            out.append("{")
            out.append(
                f"  {namespace}::feature::{feature}::config_t {feature}_config{{}};"
            )
            out.extend(
                f"  {feature}_config.{name} = {value};"
                for name, value in fields.items()
            )
            out.append(
                f"  if ({namespace}::feature::{feature}::add(boolean_state_configuration_cluster, "
                f"&{feature}_config) != ESP_OK)"
            )
            out.append(
                '    return MATTER_BUILD_FAIL("boolean state configuration feature");'
            )
            out.append("}")

        if visual:
            add_feature("visual", {"alarms_supported": supported})
        if audible:
            add_feature("audible", {"alarms_supported": supported})
        if suppress:
            add_feature("alarm_suppress", {})
        if levels is not None:
            add_feature("sensitivity_level", {"supported_sensitivity_levels": levels})
        optional = []
        if supported:
            # Optional AlarmsEnabled attribute (the cluster serves it and persists it).
            optional.append(
                f"{namespace}::attribute::create_alarms_enabled(boolean_state_configuration_cluster, 0)"
            )
        if sensitivity and CONF_DEFAULT in sensitivity:
            optional.append(
                f"{namespace}::attribute::create_default_sensitivity_level(boolean_state_configuration_cluster, "
                f"{sensitivity[CONF_DEFAULT]})"
            )
        for call in optional:
            out.append(f"if ({call} == nullptr)")
            out.append(
                '  return MATTER_BUILD_FAIL("boolean state configuration attribute");'
            )
        return out

    def elements() -> list[tuple[str, str]]:
        result = []
        if supported:
            result.append(("attrs", "AlarmsEnabled"))
        if sensitivity and CONF_DEFAULT in sensitivity:
            result.append(("attrs", "DefaultSensitivityLevel"))
        return result

    endpoint.extras.append(Extra(cluster.name, lines, elements, creates_cluster=True))

    _include(endpoint, "matter_boolean_config.h")
    cg.add_define("USE_MATTER_BOOLEAN_CONFIG")
    mapping = cg.new_Pvariable(
        _declare(MatterBooleanStateConfigMapping, f"matter_bsc_{endpoint.endpoint_id}"),
        endpoint.endpoint_id,
    )
    if sensitivity and sensitivity.get(CONF_NUMBER) is not None:
        cg.add(
            mapping.set_sensitivity_number(
                await cg.get_variable(sensitivity[CONF_NUMBER])
            )
        )
    if alarms:
        for conf in alarms.get(CONF_ON_ACTIVE, []):
            trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID])
            await automation.build_automation(
                trigger, [(cg.bool_, "visual"), (cg.bool_, "audible")], conf
            )
            cg.add(mapping.add_alarms_active_trigger(trigger))
        for conf in alarms.get(CONF_ON_SUPPRESSED, []):
            trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID])
            await automation.build_automation(
                trigger, [(cg.bool_, "visual"), (cg.bool_, "audible")], conf
            )
            cg.add(mapping.add_alarms_suppressed_trigger(trigger))
    cg.add(var.register_mapping(mapping))

    # The alarms become active while the sensor is ON (leak, rain, freeze detected, contact open, occupied).
    if alarms:
        source = alarms.get(CONF_ACTIVE)
        if source is None:
            source = next(
                (
                    device_config[key]
                    for key in _BSC_PRIMARY_KEYS
                    if device_config.get(key) is not None
                ),
                None,
            )
        if source is not None:
            cg.add(mapping.set_alarm_source(await cg.get_variable(source)))


# -- Air Quality ------------------------------------------------------------ #


async def _register_air_quality(endpoint, var, device_config: dict) -> None:
    air_quality = device_config[CONF_AIR_QUALITY]
    cluster = CLUSTERS_BY_NAME[CLUSTER_NAME_AIR_QUALITY]
    mode, thresholds = resolve_air_quality(air_quality, CORE.config)
    if mode == "derived":
        # Only the levels that have a threshold (and Good below the first one) are reported, so only their
        # features are needed. validate_device_config enabled all of them for preset thresholds.
        cluster_config = endpoint._cluster_configs[cluster.name]
        for level, feature in AIR_QUALITY_LEVEL_FEATURES.items():
            if level not in thresholds:
                cluster_config.enabled_features.pop(feature, None)
    _include(endpoint, "matter_air_quality.h")
    cg.add_define("USE_MATTER_AIR_QUALITY")
    if cluster.chip_include:
        endpoint.global_includes.add(cluster.chip_include)
    sensor_var = await cg.get_variable(air_quality[CONF_SENSOR])
    attribute_id = cluster.get_attribute("AirQuality").id
    if mode == "direct":
        mapping = cg.new_Pvariable(
            _declare(
                MatterSensorAttributeMapping,
                f"matter_air_quality_{endpoint.endpoint_id}",
            ),
            sensor_var,
            endpoint.endpoint_id,
            cluster.id,
            attribute_id,
            cg.RawExpression("esphome::matter::conversion::air_quality_index"),
            cg.RawExpression("esphome::matter::update_air_quality_attribute"),
        )
    else:
        entries = ", ".join(
            "{" + f"{level}, {thresholds[name]!r}f" + "}"
            for name, level in AIR_QUALITY_LEVELS.items()
            if name in thresholds
        )
        mapping = cg.new_Pvariable(
            _declare(
                MatterAirQualityMapping, f"matter_air_quality_{endpoint.endpoint_id}"
            ),
            sensor_var,
            endpoint.endpoint_id,
            cluster.id,
            attribute_id,
            cg.RawExpression(
                f"std::vector<esphome::matter::MatterAirQualityThreshold>{{{entries}}}"
            ),
            air_quality[CONF_HYSTERESIS],
        )
    cg.add(var.register_mapping(mapping))


async def _register_refrigerator_alarm(endpoint, var, device_config: dict) -> None:
    cluster = CLUSTERS_BY_NAME[CLUSTER_NAME_REFRIGERATOR_ALARM]
    namespace = f"esp_matter::cluster::{cluster.espm_namespace}"
    endpoint._cluster_configs[cluster.name]
    notify = cluster.get_event("Notify")

    def lines() -> list[str]:
        # Mask and Supported are DoorOpen (bit 0), State starts cleared.
        return [
            f"{namespace}::config_t refrigerator_alarm_config{{}};",
            "refrigerator_alarm_config.mask = 1;",
            "refrigerator_alarm_config.supported = 1;",
            "refrigerator_alarm_config.state = 0;",
            (
                f"auto *refrigerator_alarm_cluster = {namespace}::create(endpoint, &refrigerator_alarm_config, "
                "esp_matter::CLUSTER_FLAG_SERVER);"
            ),
            (
                "if (refrigerator_alarm_cluster == nullptr || "
                f"esp_matter::event::create(refrigerator_alarm_cluster, {notify.id}) == nullptr)"
            ),
            '  return MATTER_BUILD_FAIL("refrigerator alarm cluster create");',
        ]

    endpoint.extras.append(
        Extra(cluster.name, lines, lambda: [("events", "Notify")], creates_cluster=True)
    )

    _include(endpoint, "matter_refrigerator_alarm.h")
    cg.add_define("USE_MATTER_REFRIGERATOR_ALARM")
    mapping = cg.new_Pvariable(
        _declare(
            MatterRefrigeratorAlarmMapping,
            f"matter_refrigerator_alarm_{endpoint.endpoint_id}",
        ),
        endpoint.endpoint_id,
        await cg.get_variable(device_config[CONF_DOOR_OPEN]),
    )
    cg.add(var.register_mapping(mapping))
