"""ESPHome cover to Matter Window Covering binding (codegen helpers)."""

import logging

import esphome.config_validation as cv
from esphome.components import cover
from esphome.config import Config
from esphome.const import CONF_DEVICE_CLASS
from esphome.core import CORE, ID

from .const import CONF_COVER_ID, CONF_END_PRODUCT_TYPE, CONF_ENDPOINTS, CONF_WITH_FEATURES

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

# Garage doors, gates and doors are closures in Matter 1.5+ (Closure Control, see closure.py), but retail
# openers (Konnected, Meross, ...) advertise Window Covering because most controllers cannot use Closure yet.
# Exposing them as Window Covering is therefore allowed; `closure_compat: true` acknowledges the choice.
CLOSURE_DEVICE_CLASSES = ("garage", "gate", "door")
CONF_CLOSURE_COMPAT = "closure_compat"
_LOGGER = logging.getLogger(__name__)


def schema_options() -> dict:
    """Extra window_covering device type options."""
    return {
        cv.Optional(CONF_COVER_ID): cv.use_id(cover.Cover),
        cv.Optional(CONF_END_PRODUCT_TYPE): cv.one_of(*END_PRODUCT_TYPES, lower=True),
        # Compatibility mode: expose a garage/gate/door cover as Window Covering.
        cv.Optional(CONF_CLOSURE_COMPAT, default=False): cv.boolean,
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
            if device_config.get(CONF_CLOSURE_COMPAT):
                raise cv.Invalid("closure_compat requires cover_id")
            continue
        if device_config.get(CONF_WITH_FEATURES):
            raise cv.Invalid(
                "window_covering features are selected automatically from the "
                "cover when cover_id is set; remove 'features'"
            )
        cover_config = _cover_config(full_config, cover_id)
        device_class = (cover_config or {}).get(CONF_DEVICE_CLASS)
        is_closure = device_class in CLOSURE_DEVICE_CLASSES
        if device_config.get(CONF_CLOSURE_COMPAT) and not is_closure:
            raise cv.Invalid(
                "closure_compat is for covers with device_class garage, gate or door; "
                f"cover '{cover_id}' has device_class '{device_class}'"
            )
        if is_closure and not device_config.get(CONF_CLOSURE_COMPAT):
            _LOGGER.info(
                "Cover '%s' has device_class '%s' and is exposed as a Matter Window "
                "Covering. This is deliberate: no controller supports the Matter 1.5 "
                "Closure device types yet, while Window Covering works everywhere "
                "(it is shown as a shutter or blind). Set `closure_compat: true` to "
                "silence this note, or use the experimental `closure` device type",
                cover_id,
                device_class,
            )


def _binding(device_config: dict) -> tuple[str, int]:
    device_class = ""
    cover_config = _cover_config(CORE.config, device_config[CONF_COVER_ID])
    if cover_config:
        device_class = cover_config.get(CONF_DEVICE_CLASS) or ""
    end_product = END_PRODUCT_TYPES.get(device_config.get(CONF_END_PRODUCT_TYPE), -1)
    return device_class, end_product


def config_init(endpoint_id: int, device_config: dict) -> str:
    """Constructor argument of the window_covering endpoint config.

    esp-matter only accepts EndProductType through the constructor.
    """
    device_class, end_product = _binding(device_config)
    return (
        "esphome::matter::window_covering_end_product_type("
        f'{endpoint_id}, "{device_class}", {end_product})'
    )


def config_lines(config_var: str, endpoint_id: int, device_config: dict) -> list[str]:
    """C++ lines that attach the cover binding to a window_covering config."""
    device_class, _ = _binding(device_config)
    return [
        "esphome::matter::configure_window_covering("
        f'{config_var}.window_covering, {endpoint_id}, "{device_class}");'
    ]
