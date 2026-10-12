"""Binding of an ESPHome light to the Matter OnOff, LevelControl and ColorControl clusters.

The runtime mapping lives in matter_lights.cpp and looks at the clusters and
attributes of the endpoint, not at the device type. This module has the YAML
surface (`light_id` and its options) and the code generation helpers.
"""

import logging

import esphome.config_validation as cv
from esphome.components import light
from esphome.config import Config
from esphome.const import CONF_LIGHT_ID, CONF_NAME, CONF_RESTORE_MODE

from .const import CONF_ENDPOINTS, CONF_MAX_LEVEL, CONF_MIN_LEVEL, CONF_WITH_FEATURES

_LOGGER = logging.getLogger(__name__)

CONF_ON_OFF_TRANSITION_TIME = "on_off_transition_time"
CONF_COLOR_LOOP_EFFECT = "color_loop_effect"
CONF_RESTORE_STATE = "restore_state"

# Device types that can be bound to an ESPHome light. On/Off device types get a
# level when the LevelControl cluster is added to the endpoint with `clusters:`.
LIGHT_DEVICE_TYPES = (
    "on_off_light",
    "dimmable_light",
    "color_temperature_light",
    "extended_color_light",
    "on_off_plug_in_unit",
    "dimmable_plug_in_unit",
    "mounted_on_off_control",
    "mounted_dimmable_load_control",
)

# Device types of which the config has a level_control member for min/max level.
LEVEL_RANGE_DEVICE_TYPES = (
    "dimmable_light",
    "color_temperature_light",
    "extended_color_light",
    "dimmable_plug_in_unit",
    "mounted_dimmable_load_control",
)

COLOR_LOOP_FEATURE = "ColorLoop"

# LevelControl attributes that are optional and that esp-matter doesn't create
# by itself. The first three only apply to the OnOff feature.
ON_OFF_TRANSITION_ATTRIBUTES = (
    "OnOffTransitionTime",
    "OnTransitionTime",
    "OffTransitionTime",
)
LEVEL_ATTRIBUTES = ("DefaultMoveRate",)


def _light_id_not_supported(device_type_name: str):
    def validator(value):
        raise cv.Invalid(
            f"{CONF_LIGHT_ID} is not supported by {device_type_name}. It can bind a "
            f"light to: {', '.join(LIGHT_DEVICE_TYPES)}"
        )

    return validator


def light_schema_options(device_type) -> dict:
    """Schema keys that a device type has for binding a light."""
    name = device_type.name
    if name not in LIGHT_DEVICE_TYPES:
        # Registered only to replace ESPHome's generic "invalid option" error.
        return {cv.Optional(CONF_LIGHT_ID): _light_id_not_supported(name)}

    from .data_model.units import percentage

    schema = {cv.Optional(CONF_LIGHT_ID): cv.use_id(light.LightState)}
    if name in LEVEL_RANGE_DEVICE_TYPES and any(
        cluster.name == "LevelControl" and cluster.required
        for cluster in device_type.server_clusters
    ):
        level = cv.All(percentage(), cv.int_range(min=1, max=254))
        schema[cv.Optional(CONF_MIN_LEVEL, default=1)] = level
        schema[cv.Optional(CONF_MAX_LEVEL, default=254)] = level
    schema[cv.Optional(CONF_ON_OFF_TRANSITION_TIME)] = cv.All(
        cv.positive_time_period_milliseconds,
        cv.Range(max=cv.TimePeriod(milliseconds=6553500)),
    )
    if name == "extended_color_light":
        schema[cv.Optional(CONF_COLOR_LOOP_EFFECT)] = cv.string_strict
    return schema


def validate_light_binding(device_type):
    """Cross checks of the light options in the config of one device type."""

    def validator(config):
        light_bound = CONF_LIGHT_ID in config
        if not light_bound:
            for key in (CONF_ON_OFF_TRANSITION_TIME, CONF_COLOR_LOOP_EFFECT):
                if key in config:
                    raise cv.Invalid(f"{key} requires {CONF_LIGHT_ID}")
        if CONF_COLOR_LOOP_EFFECT in config and COLOR_LOOP_FEATURE not in config.get(
            CONF_WITH_FEATURES, ()
        ):
            raise cv.Invalid(
                f"{CONF_COLOR_LOOP_EFFECT} requires the ColorLoop feature: add "
                f"`{CONF_WITH_FEATURES}: [Hue And Saturation, EnhancedHue, ColorLoop]`"
            )
        if (
            light_bound
            and COLOR_LOOP_FEATURE in config.get(CONF_WITH_FEATURES, ())
            and CONF_COLOR_LOOP_EFFECT not in config
        ):
            raise cv.Invalid(
                "The ColorLoop feature of a light needs an ESPHome effect that shows "
                f"the loop. Add an effect to the light and name it with "
                f"`{CONF_COLOR_LOOP_EFFECT}`, or remove ColorLoop from "
                f"`{CONF_WITH_FEATURES}`",
                path=[CONF_COLOR_LOOP_EFFECT],
            )
        return config

    return validator


def light_effect_names(light_config) -> list[str]:
    """Names of the effects in a (validated) light config."""
    names = []
    for effect in light_config.get("effects", []) or []:
        for effect_config in effect.values():
            if isinstance(effect_config, dict) and CONF_NAME in effect_config:
                names.append(str(effect_config[CONF_NAME]))
    return names


def _light_config(full_config: Config, light_id):
    try:
        path = full_config.get_path_for_id(light_id)[:-1]
        return full_config.get_config_for_path(path)
    except KeyError:
        return None


def validate_light_effects(matter_config: dict, full_config: Config) -> None:
    """The color loop effect has to exist on the bound light."""
    for endpoint_id, endpoint_config in matter_config.get(CONF_ENDPOINTS, {}).items():
        for device_config in endpoint_config.values():
            if not isinstance(device_config, dict):
                continue
            effect = device_config.get(CONF_COLOR_LOOP_EFFECT)
            light_id = device_config.get(CONF_LIGHT_ID)
            if effect is None or light_id is None:
                continue
            light_config = _light_config(full_config, light_id)
            if light_config is None:
                continue
            names = light_effect_names(light_config)
            if effect.lower() not in (name.lower() for name in names):
                available = (
                    f"it has: {', '.join(names)}" if names else "it has no effects"
                )
                raise cv.Invalid(
                    f"Matter endpoint {endpoint_id}: {CONF_COLOR_LOOP_EFFECT} "
                    f"'{effect}' is not an effect of light '{light_id}' ({available}). "
                    "ColorLoopSet runs this effect on the light"
                )


def _restore_description(light_config) -> str | None:
    """The restore setting of a light, or None when ESPHome doesn't restore it."""
    restore_mode = light_config.get(CONF_RESTORE_MODE)
    if restore_mode is not None:
        return None if restore_mode == "ALWAYS_OFF" else f"restore_mode: {restore_mode}"
    restore_state = light_config.get(CONF_RESTORE_STATE)
    if restore_state in (None, "none", "NONE"):
        return None
    return f"{CONF_RESTORE_STATE}: {restore_state}"


def light_restore_warning(matter_config: dict, full_config: Config) -> None:
    """Matter owns the power-on state (StartUpOnOff, StartUpCurrentLevel).

    A light that also restores its own state turns on or off at boot before
    Matter has applied its power-on behaviour, and the two can disagree.
    """
    for endpoint_config in matter_config.get(CONF_ENDPOINTS, {}).values():
        for device_config in endpoint_config.values():
            if not isinstance(device_config, dict):
                continue
            light_id = device_config.get(CONF_LIGHT_ID)
            if light_id is None:
                continue
            light_config = _light_config(full_config, light_id)
            if light_config is None:
                continue
            restore = _restore_description(light_config)
            if restore is not None:
                _LOGGER.warning(
                    "Light '%s' is exposed through Matter with %s and should use "
                    "restore_mode: ALWAYS_OFF so Matter can restore its power-on state "
                    "(StartUpOnOff and StartUpCurrentLevel)",
                    light_id,
                    restore,
                )


def level_attribute_names(oo_enabled: bool) -> tuple[str, ...]:
    """Optional LevelControl attributes to create for a bound light."""
    if oo_enabled:
        return ON_OFF_TRANSITION_ATTRIBUTES + LEVEL_ATTRIBUTES
    return LEVEL_ATTRIBUTES


def initial_value_lines(attribute_name: str, device_config: dict) -> list[str]:
    """Lines that set a non default initial value of the ``created_attribute``."""
    if attribute_name == "OnOffTransitionTime" and (
        transition := device_config.get(CONF_ON_OFF_TRANSITION_TIME)
    ):
        deciseconds = int(transition.total_milliseconds // 100)
        return [
            "  {",
            f"    esp_matter_attr_val_t transition_val = esp_matter_uint16({deciseconds});",
            f"    if (esp_matter::attribute::set_val(created_attribute, &transition_val, false) != ESP_OK)",
            "      return false;",
            "  }",
        ]
    return []
