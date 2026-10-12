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

# device type name -> prefer cooling when the climate exposes neither
# heating nor cooling (Matter requires at least one of them).
CLIMATE_DEVICE_TYPES = {
    "thermostat": False,
    "room_air_conditioner": True,
}

# Features derived from the climate traits.
_DERIVED_FEATURES = ("Heating", "Cooling", "AutoMode")

CLIMATE_INCLUDE = '#include "esphome/components/matter/matter_climate.h"'


def climate_schema(device_type_name: str) -> dict:
    """Extra schema keys for a device type."""
    if device_type_name not in CLIMATE_DEVICE_TYPES:
        return {}
    return {cv.Optional(CONF_CLIMATE_ID): cv.use_id(climate.Climate)}


def validate_climate_features(config: dict) -> dict:
    if CONF_CLIMATE_ID not in config:
        return config
    conflicting = [f for f in config.get(CONF_WITH_FEATURES, ()) if f in _DERIVED_FEATURES]
    if conflicting:
        raise cv.Invalid(
            f"features {', '.join(conflicting)} are derived from the climate "
            f"traits when {CONF_CLIMATE_ID} is set and must not be configured"
        )
    return config


def prefer_cooling(device_type_name: str) -> bool:
    return CLIMATE_DEVICE_TYPES[device_type_name]


def config_lines(config_var: str, climate_var, device_type_name: str) -> list[str]:
    """Lines run before esp_matter::endpoint::<type>::add()."""
    return [
        f"esphome::matter::configure_climate_thermostat({config_var}.thermostat, "
        f"{climate_var}, {str(prefer_cooling(device_type_name)).lower()});"
    ]


def post_create_lines(climate_var, device_type_name: str) -> list[str]:
    """Lines run after the device type has been added to the endpoint."""
    return [
        f"if (!esphome::matter::extend_climate_thermostat(endpoint, {climate_var}, "
        f"{str(prefer_cooling(device_type_name)).lower()}))",
        '  return MATTER_BUILD_FAIL("thermostat extension");',
    ]
