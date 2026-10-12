"""ESPHome cover to Matter Window Covering binding (codegen helpers)."""

import esphome.config_validation as cv
from esphome.components import cover
from esphome.config import Config
from esphome.const import CONF_DEVICE_CLASS
from esphome.core import CORE, ID

from .const import CONF_COVER_ID, CONF_END_PRODUCT_TYPE, CONF_ENDPOINTS, CONF_FEATURES

WINDOW_COVERING = "window_covering"

# Matter WindowCovering EndProductType values.
END_PRODUCT_TYPES = {
    "roller_shade": 0x00,
    "roman_shade": 0x01,
    "balloon_shade": 0x02,
    "woven_wood": 0x03,
    "pleated_shade": 0x04,
    "cellular_shade": 0x05,
    "layered_shade": 0x06,
    "layered_shade_2d": 0x07,
    "sheer_shade": 0x08,
    "tilt_only_interior_blind": 0x09,
    "interior_blind": 0x0A,
    "vertical_blind_strip_curtain": 0x0B,
    "interior_venetian_blind": 0x0C,
    "exterior_venetian_blind": 0x0D,
    "lateral_left_curtain": 0x0E,
    "lateral_right_curtain": 0x0F,
    "central_curtain": 0x10,
    "roller_shutter": 0x11,
    "exterior_vertical_screen": 0x12,
    "awning_terrace_patio": 0x13,
    "awning_vertical_screen": 0x14,
    "tilt_only_pergola": 0x15,
    "swinging_shutter": 0x16,
    "sliding_shutter": 0x17,
}

# Garage doors, gates and doors need the Closure Control cluster (Matter 1.5+).
UNSUPPORTED_DEVICE_CLASSES = ("garage", "gate", "door")


def schema_options() -> dict:
    """Extra window_covering device type options."""
    return {
        cv.Optional(CONF_COVER_ID): cv.use_id(cover.Cover),
        cv.Optional(CONF_END_PRODUCT_TYPE): cv.one_of(*END_PRODUCT_TYPES, lower=True),
    }


def _cover_config(full_config: Config, cover_id: ID) -> dict | None:
    try:
        path = full_config.get_path_for_id(cover_id)[:-1]
        return full_config.get_config_for_path(path)
    except KeyError:
        return None


def validate_cover_bindings(matter_config: dict, full_config: Config) -> None:
    for endpoint_config in matter_config.get(CONF_ENDPOINTS, {}).values():
        device_config = endpoint_config.get(WINDOW_COVERING)
        if not isinstance(device_config, dict):
            continue
        cover_id = device_config.get(CONF_COVER_ID)
        if cover_id is None:
            if CONF_END_PRODUCT_TYPE in device_config:
                raise cv.Invalid("end_product_type requires cover_id")
            continue
        if device_config.get(CONF_FEATURES):
            raise cv.Invalid(
                "window_covering features are selected automatically from the "
                "cover when cover_id is set; remove 'features'"
            )
        cover_config = _cover_config(full_config, cover_id)
        device_class = (cover_config or {}).get(CONF_DEVICE_CLASS)
        if device_class in UNSUPPORTED_DEVICE_CLASSES:
            raise cv.Invalid(
                f"Cover '{cover_id}' has device_class '{device_class}'. Garage "
                "doors, gates and doors are not Window Coverings. They need the "
                "Matter Closure Control cluster (Matter 1.5+), which is not "
                "supported yet"
            )


def config_lines(config_var: str, endpoint_id: int, device_config: dict) -> list[str]:
    """C++ lines that attach the cover binding to a window_covering config."""
    device_class = ""
    cover_config = _cover_config(CORE.config, device_config[CONF_COVER_ID])
    if cover_config:
        device_class = cover_config.get(CONF_DEVICE_CLASS) or ""
    end_product = END_PRODUCT_TYPES.get(device_config.get(CONF_END_PRODUCT_TYPE), -1)
    return [
        "esphome::matter::configure_window_covering("
        f'{config_var}.window_covering, {endpoint_id}, "{device_class}", {end_product});'
    ]
