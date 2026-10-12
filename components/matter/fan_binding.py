"""Codegen helpers for binding an ESPHome fan to the Matter Fan Control cluster."""

import logging

import esphome.config_validation as cv
from esphome.components import fan
from esphome.config import Config

from .const import CONF_WITH_FEATURES
from .util import restore_warning

_LOGGER = logging.getLogger(__name__)

CONF_FAN_ID = "fan_id"

# Fan Control features that are selected from the ESPHome fan traits.
TRAIT_FEATURES = ("MultiSpeed", "Rocking", "AirflowDirection")


def fan_id_schema():
    return cv.use_id(fan.Fan)


def validate_fan_features(config):
    """The trait based features can't be selected for a bound fan."""
    if CONF_FAN_ID in config:
        trait_features = [
            f for f in config.get(CONF_WITH_FEATURES, ()) if f in TRAIT_FEATURES
        ]
        if trait_features:
            raise cv.Invalid(
                f"The features {', '.join(trait_features)} follow the capabilities of "
                f"the fan and can't be set together with {CONF_FAN_ID}"
            )
    return config


def config_lines(config_var: str, fan_var, enabled_features: dict[str, bool]):
    """Lines for the fan device type config in the endpoint build callback."""
    with_auto = "true" if enabled_features.get("Auto") else "false"
    return [
        (
            f"{config_var}.fan_control.fan_mode_sequence = "
            f"esphome::matter::fan_mode_sequence({fan_var}->get_traits(), {with_auto});"
        )
    ]


def feature_lines(cluster_var: str, fan_var):
    """Lines that add the trait based features to the created Fan Control cluster."""
    return [
        f"if (esphome::matter::add_fan_features({cluster_var}, {fan_var}) != ESP_OK)",
        "  return false;",
    ]


def fan_restore_warning(matter_config: dict, full_config: Config):
    restore_warning(matter_config, full_config, CONF_FAN_ID, "Fan")
