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

from .const import CONF_MAX_LEVEL, CONF_MIN_LEVEL, CONF_WITH_FEATURES
from .util import iter_bound_entities, restore_warning

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


def _warn_transition_rounding(value):
    """The attribute has a resolution of 1/10 s."""
    milliseconds = value.total_milliseconds
    if 0 < milliseconds < 100:
        _LOGGER.warning(
            "%s %s is shorter than 1/10 s, the resolution of the OnOffTransitionTime "
            "attribute, and becomes 0 (no transition)",
            CONF_ON_OFF_TRANSITION_TIME,
            value,
        )
    elif milliseconds % 100:
        _LOGGER.warning(
            "%s %s is rounded down to 1/10 s, the resolution of the "
            "OnOffTransitionTime attribute",
            CONF_ON_OFF_TRANSITION_TIME,
            value,
        )
    return value


# Range of a color_temperature_light whose light has none (6500 K to 2000 K).
DEFAULT_MIN_MIREDS = 153
DEFAULT_MAX_MIREDS = 500

# Light platforms that have a color temperature range.
COLOR_TEMPERATURE_PLATFORMS = ("rgbct", "rgbww", "cwww")


def light_has_color_temperature(light_config) -> bool:
    """Whether a (validated) light config describes a color temperature range."""
    if light_config.get("platform") in COLOR_TEMPERATURE_PLATFORMS:
        return True
    return any(
        key in light_config
        for key in ("cold_white_color_temperature", "warm_white_color_temperature")
    )


def warn_missing_color_temperature(matter_config: dict, full_config: Config) -> None:
    """Warn about a light without a color temperature range on a CT device type.

    An extended_color_light then doesn't advertise ColorTemperature (not
    conformant to the device type). A color_temperature_light can't without it
    and gets a default range (153 to 500 mireds) that the light can't follow.
    """
    for endpoint_id, device_type, _, light_id, light_config in iter_bound_entities(
        matter_config, full_config, CONF_LIGHT_ID
    ):
        if device_type not in ("extended_color_light", "color_temperature_light"):
            continue
        if light_has_color_temperature(light_config):
            continue
        if device_type == "extended_color_light":
            _LOGGER.warning(
                "Light '%s' has no color temperature range, so Matter endpoint %s "
                "(extended_color_light) doesn't advertise the ColorTemperature "
                "feature. The Matter specification requires ColorTemperature for "
                "this device type, so controllers and certification tools may "
                "consider the endpoint non-conformant. Controllers that follow the "
                "features show no color temperature control for it.",
                light_id,
                endpoint_id,
            )
        else:
            _LOGGER.warning(
                "Light '%s' has no color temperature range but Matter endpoint %s "
                "is a color_temperature_light: it advertises a default range of "
                "%d to %d mireds that the light can't follow. Use a light with "
                "cold_white_color_temperature and warm_white_color_temperature, "
                "or a dimmable_light (or extended_color_light for RGB).",
                light_id,
                endpoint_id,
                DEFAULT_MIN_MIREDS,
                DEFAULT_MAX_MIREDS,
            )


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
        _warn_transition_rounding,
    )
    if name == "extended_color_light":
        schema[cv.Optional(CONF_COLOR_LOOP_EFFECT)] = cv.string_strict
    return schema


def validate_light_binding(device_type):
    """Cross checks of the light options in the config of one device type."""

    def validator(config):
        light_bound = CONF_LIGHT_ID in config
        if (
            CONF_MIN_LEVEL in config
            and CONF_MAX_LEVEL in config
            and config[CONF_MIN_LEVEL] > config[CONF_MAX_LEVEL]
        ):
            raise cv.Invalid(
                f"{CONF_MIN_LEVEL} ({config[CONF_MIN_LEVEL]}) is higher than "
                f"{CONF_MAX_LEVEL} ({config[CONF_MAX_LEVEL]})",
                path=[CONF_MIN_LEVEL],
            )
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


def validate_light_effects(matter_config: dict, full_config: Config) -> None:
    """The color loop effect has to exist on the bound light."""
    for endpoint_id, _, device_config, light_id, light_config in iter_bound_entities(
        matter_config, full_config, CONF_LIGHT_ID
    ):
        effect = device_config.get(CONF_COLOR_LOOP_EFFECT)
        if effect is None:
            continue
        names = light_effect_names(light_config)
        if effect.lower() not in (name.lower() for name in names):
            available = f"it has: {', '.join(names)}" if names else "it has no effects"
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
    restore_warning(
        matter_config,
        full_config,
        CONF_LIGHT_ID,
        "Light",
        _restore_description,
        " (StartUpOnOff and StartUpCurrentLevel)",
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
            "    if (esp_matter::attribute::set_val(created_attribute, &transition_val, false) != ESP_OK)",
            f'      return MATTER_BUILD_FAIL("attribute {attribute_name} initial value");',
            "  }",
        ]
    return []
