"""ESPHome climate -> Matter Thermostat / Room Air Conditioner binding.

Kept out of endpoints.py / device_types.py so those only need small hooks. The
Thermostat features (Heating, Cooling, AutoMode) are not configured in YAML when
a climate is bound: they are derived from the climate traits in C++ when the
endpoint is built (see matter_climate.cpp), because only the C++ side knows the
final traits of every climate platform.
"""

import esphome.config_validation as cv
from esphome.components import climate

from .const import CONF_WITH_FEATURES

CONF_CLIMATE_ID = "climate_id"
# heat_pump only: put the Thermostat server on the Heat Pump endpoint itself.
CONF_THERMOSTAT_ON_ENDPOINT = "thermostat_on_endpoint"

# device type name -> prefer cooling when the climate exposes neither
# heating nor cooling (Matter requires at least one of them).
CLIMATE_DEVICE_TYPES = {
    "thermostat": False,
    "room_air_conditioner": True,
    # Thermostat is a mandatory server cluster of the Water Heater device type.
    "water_heater": False,
    # The Heat Pump device type only lists Thermostat as a client cluster, the
    # server is created next to it (see separate_thermostat_lines()).
    "heat_pump": False,
}

# Device types that can only heat: cooling modes of the climate are not exposed.
HEATING_ONLY = frozenset({"water_heater"})

# Features derived from the climate traits.
_DERIVED_FEATURES = ("Heating", "Cooling", "AutoMode")

# Features that need a Thermostat delegate (preset list, schedules, ...) which the
# climate binding does not have. Enabling them would only advertise attributes
# nothing maintains, so they are rejected together with climate_id.
_UNSUPPORTED_FEATURES = (
    "Presets",
    "Setback",
    "ScheduleConfiguration",
    "MatterScheduleConfiguration",
    "ThermostatSuggestions",
)

CLIMATE_INCLUDE = '#include "esphome/components/matter/matter_climate.h"'


def climate_schema(device_type_name: str) -> dict:
    """Extra schema keys for a device type."""
    if device_type_name not in CLIMATE_DEVICE_TYPES:
        return {}
    schema = {cv.Optional(CONF_CLIMATE_ID): cv.use_id(climate.Climate)}
    if device_type_name == "heat_pump":
        schema[cv.Optional(CONF_THERMOSTAT_ON_ENDPOINT, default=False)] = cv.boolean
    return schema


def validate_climate_features(config: dict) -> dict:
    if CONF_CLIMATE_ID not in config:
        return config
    conflicting = [
        f for f in config.get(CONF_WITH_FEATURES, ()) if f in _DERIVED_FEATURES
    ]
    if conflicting:
        raise cv.Invalid(
            f"features {', '.join(conflicting)} are derived from the climate "
            f"traits when {CONF_CLIMATE_ID} is set and must not be configured"
        )
    unsupported = [
        f for f in config.get(CONF_WITH_FEATURES, ()) if f in _UNSUPPORTED_FEATURES
    ]
    if unsupported:
        raise cv.Invalid(
            f"The Thermostat features {', '.join(unsupported)} are not supported with "
            f"{CONF_CLIMATE_ID}: the climate binding has no preset, setback or "
            "schedule handling (see docs/hvac.md, gap G7). Remove them, or drop "
            "climate_id and implement them with on_attribute"
        )
    return config


def has_thermostat_member(device_type_name: str) -> bool:
    """Whether esp-matter's config_t of the device type has a Thermostat member."""
    from .data_model import espmatter_names

    entry = espmatter_names.device_type_entry(device_type_name)
    return entry is not None and "thermostat" in entry["config_members"]


def separate_thermostat_lines(climate_var, device_type_name: str) -> list[str]:
    """Creates the Thermostat server for a device type that has no config member for it."""
    return [
        "{",
        "  esp_matter::cluster::thermostat::config_t thermostat_config{};",
        (
            "  esphome::matter::configure_climate_thermostat(thermostat_config, "
            f"{climate_var}, {_flags(device_type_name)});"
        ),
        (
            "  if (esp_matter::cluster::thermostat::create(endpoint, &thermostat_config, "
            "esp_matter::CLUSTER_FLAG_SERVER) == nullptr)"
        ),
        '    return MATTER_BUILD_FAIL("thermostat cluster create");',
        "}",
    ]


def prefer_cooling(device_type_name: str) -> bool:
    return CLIMATE_DEVICE_TYPES[device_type_name]


def heating_only(device_type_name: str) -> bool:
    return device_type_name in HEATING_ONLY


def _flags(device_type_name: str) -> str:
    text = str(prefer_cooling(device_type_name)).lower()
    if heating_only(device_type_name):
        text += ", true"
    return text


def config_lines(config_var: str, climate_var, device_type_name: str) -> list[str]:
    """Lines run before esp_matter::endpoint::<type>::add()."""
    return [
        (
            f"esphome::matter::configure_climate_thermostat({config_var}.thermostat, "
            f"{climate_var}, {_flags(device_type_name)});"
        )
    ]


def post_create_lines(climate_var, device_type_name: str) -> list[str]:
    """Lines run after the device type has been added to the endpoint."""
    return [
        (
            f"if (!esphome::matter::extend_climate_thermostat(endpoint, {climate_var}, "
            f"{_flags(device_type_name)}))"
        ),
        '  return MATTER_BUILD_FAIL("thermostat extension");',
    ]
