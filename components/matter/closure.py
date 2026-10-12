"""Binding of an ESPHome cover to the Matter Closure device type (Closure Control cluster, Matter 1.5).

Kept in a separate module like lock_valve.py. See docs/cover.md ("Garage doors, gates and doors").
"""

import logging

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import cover
from esphome.config import Config
from esphome.const import CONF_DEVICE_CLASS

from .const import CONF_COVER_ID, CONF_ENDPOINTS, CONF_WITH_FEATURES

_LOGGER = logging.getLogger(__name__)

CLOSURE = "closure"

# Closure Control features the cover binding serves. Everything else needs
# hardware knowledge an ESPHome cover does not have (latching, ventilation, ...).
BOUND_FEATURES = ("Positioning",)

# device_class values that are window coverings and must not be bound to a closure.
WINDOW_COVERING_CLASSES = (
    "blind",
    "shade",
    "curtain",
    "awning",
    "shutter",
    "window",
    "damper",
)


def schema_options() -> dict:
    # Without a cover there is no delegate, the SDK never registers the Closure
    # Control server and the cluster would not exist on the node.
    return {cv.Required(CONF_COVER_ID): cv.use_id(cover.Cover)}


def validate_closure_features(config: dict) -> dict:
    """Only the Positioning feature is supported by the cover binding; it is always enabled."""
    unsupported = [
        name
        for name in config.get(CONF_WITH_FEATURES, ())
        if name not in BOUND_FEATURES
    ]
    if unsupported:
        raise cv.Invalid(
            "closure: the cover binding only supports the Positioning feature. Remove "
            f"{', '.join(unsupported)} from with_features",
            path=[CONF_WITH_FEATURES],
        )
    config = dict(config)
    config[CONF_WITH_FEATURES] = list(BOUND_FEATURES)
    return config


def validate_closure_bindings(matter_config: dict, full_config: Config) -> None:
    from .covers import _cover_config

    for endpoint_config in matter_config.get(CONF_ENDPOINTS, {}).values():
        device_config = endpoint_config.get(CLOSURE)
        if not isinstance(device_config, dict):
            continue
        _LOGGER.warning(
            "Endpoint uses the experimental Matter 1.5 `closure` device type: no "
            "controller documents support for it yet and some reject unknown device "
            "types. Use `window_covering` with `closure_compat: true` for a "
            "garage door that must work today"
        )
        cover_config = _cover_config(full_config, device_config[CONF_COVER_ID]) or {}
        device_class = cover_config.get(CONF_DEVICE_CLASS)
        if device_class in WINDOW_COVERING_CLASSES:
            raise cv.Invalid(
                f"Cover '{device_config[CONF_COVER_ID]}' has device_class '{device_class}', "
                "which is a window covering. Use `window_covering` for it; `closure` is "
                "for garage doors, gates and doors"
            )


def validate_unique_cover_bindings(matter_config: dict) -> None:
    """A cover can be bound to one window_covering or closure endpoint only."""
    from .covers import WINDOW_COVERING

    seen: dict[str, str] = {}
    for endpoint_id, endpoint_config in matter_config.get(CONF_ENDPOINTS, {}).items():
        for key in (WINDOW_COVERING, CLOSURE):
            device_config = endpoint_config.get(key)
            if (
                not isinstance(device_config, dict)
                or CONF_COVER_ID not in device_config
            ):
                continue
            cover_id = str(device_config[CONF_COVER_ID])
            place = f"endpoint {endpoint_id} ({key})"
            if cover_id in seen:
                raise cv.Invalid(
                    f"Cover '{cover_id}' is bound to {seen[cover_id]} and {place}. A cover "
                    "can be exposed through one Window Covering or Closure endpoint only; "
                    "use a second template cover that forwards to it if you need both"
                )
            seen[cover_id] = place


async def register_entity(
    var, endpoint_id: int, device_type_name: str, device_config: dict
) -> None:
    if device_type_name == CLOSURE:
        cover_ = await cg.get_variable(device_config[CONF_COVER_ID])
        # Compiles matter_closure.cpp (it needs the Closure Control cluster of esp-matter).
        cg.add_define("USE_MATTER_CLOSURE")
        cg.add(var.register_closure(cover_, endpoint_id))


def device_config_lines(
    device_type_name: str, config_var: str, endpoint_id: int, device_config: dict
) -> list[str]:
    if device_type_name == CLOSURE:
        return [
            (
                f"{config_var}.closure_control.delegate = "
                f"esphome::matter::get_closure_delegate({endpoint_id});"
            )
        ]
    return []
