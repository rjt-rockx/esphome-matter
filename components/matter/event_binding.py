"""Bind an ESPHome `event` entity to the Switch cluster of a Generic Switch (or Doorbell).

An event entity reports finished gestures ("single", "double", "hold", "ring", ...). The binding
replays each of them as the press and release edges of a physical button through the same state
machine that drives a bound `binary_sensor`, so the Switch events and their order are identical
(see generic_switch.py and docs/generic-switch.md):

  =====================  ==========================================================
  Gesture (event_map)    Switch events
  =====================  ==========================================================
  1                      a short press: InitialPress, ShortRelease, MultiPressComplete(1)
  N (2..255)             N short presses in a row, MultiPressComplete(N) at the end
  long_press             InitialPress, LongPress, LongRelease (the release at once
                         unless another event type is mapped to `press_end`)
  press_start            the press edge of a button
  press_end              the release edge (ShortRelease, or LongRelease after a
                         long press)
  =====================  ==========================================================

Events of disabled Switch features are not sent, exactly like for a bound binary sensor.
"""

import re

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.core import CORE

from .const import CONF_ENDPOINTS, CONF_MATTER, CONF_WITH_FEATURES

CONF_EVENT_ID = "event_id"
CONF_EVENT_MAP = "event_map"

# CORE.data["matter"][KEY_EVENT_MAPS][endpoint id][device type key] -> {event type: gesture}
KEY_EVENT_MAPS = "event_maps"

GESTURE_LONG_PRESS = "long_press"
GESTURE_PRESS_START = "press_start"
GESTURE_PRESS_END = "press_end"
NAMED_GESTURES = (GESTURE_LONG_PRESS, GESTURE_PRESS_START, GESTURE_PRESS_END)

# Event type names that are mapped when `event_map` is not configured. Names are compared in
# lower case with spaces and dashes read as underscores.
DEFAULT_EVENT_NAMES: dict[str, int | str] = {
    **dict.fromkeys(
        (
            "press", "pressed", "single", "single_press", "single_click", "click", "tap",
            "short_press", "short", "ring", "doorbell", "doorbell_pressed", "ding", "button",
        ),
        1,
    ),
    **dict.fromkeys(("double", "double_press", "double_click", "double_tap", "press_2"), 2),
    **dict.fromkeys(("triple", "triple_press", "triple_click", "triple_tap", "press_3"), 3),
    **dict.fromkeys(("quadruple", "quadruple_press", "quad", "press_4"), 4),
    **dict.fromkeys(
        ("long", "long_press", "hold", "held", "long_click", "press_and_hold"),
        GESTURE_LONG_PRESS,
    ),
    **dict.fromkeys(
        ("press_start", "initial_press", "button_down", "down"), GESTURE_PRESS_START
    ),
    **dict.fromkeys(
        (
            "press_end", "release", "released", "short_release", "long_release", "hold_release",
            "long_press_end", "button_up", "up",
        ),
        GESTURE_PRESS_END,
    ),
}  # fmt: skip


def _gesture(value):
    if isinstance(value, bool):
        raise cv.Invalid(
            "expected a number of presses (1-255) or one of " + ", ".join(NAMED_GESTURES)
        )
    if isinstance(value, int) or (isinstance(value, str) and value.strip().isdigit()):
        return cv.int_range(min=1, max=255)(int(value))
    return cv.one_of(*NAMED_GESTURES, lower=True)(value)


def schema_options() -> dict:
    """Extra endpoint config options of the device types with a Switch cluster."""
    from esphome.components.event import Event

    return {
        cv.Optional(CONF_EVENT_ID): cv.use_id(Event),
        cv.Optional(CONF_EVENT_MAP): cv.Schema({cv.string_strict: _gesture}),
    }


def normalize(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", name.strip().lower())


def event_types_of(full_config, event_id) -> list[str] | None:
    """The `event_types` of the event entity with this id, or None when its config has none."""
    try:
        path = full_config.get_path_for_id(event_id)[:-1]
        event_config = full_config.get_config_for_path(path)
    except (KeyError, AttributeError, TypeError):
        return None
    types = event_config.get("event_types") if isinstance(event_config, dict) else None
    return [str(t) for t in types] if types else None


def resolve_event_map(
    where: str,
    event_types: list[str] | None,
    configured: dict | None,
    features,
    multi_press_max: int,
) -> dict[str, int | str]:
    """The event type -> gesture map of one binding. Raises cv.Invalid with an actionable message.

    `configured` is `event_map`. Without it, the names of the entity's `event_types` are matched
    against DEFAULT_EVENT_NAMES and the types without a known name are not mapped.
    """
    if configured:
        result = dict(configured)
        if event_types is not None:
            unknown = sorted(set(result) - set(event_types))
            if unknown:
                raise cv.Invalid(
                    f"{where}: event_map has {', '.join(map(repr, unknown))}, which the event "
                    f"entity does not have. Its event_types are: {', '.join(event_types)}",
                    path=[CONF_EVENT_MAP],
                )
    else:
        if event_types is None:
            raise cv.Invalid(
                f"{where}: the event entity has no `event_types` in its configuration, so the "
                "gestures cannot be derived. Add `event_map: {<event type>: <presses or "
                "long_press>}`",
                path=[CONF_EVENT_MAP],
            )
        result = {}
        for event_type in event_types:
            gesture = DEFAULT_EVENT_NAMES.get(normalize(event_type))
            if gesture is not None:
                result[event_type] = gesture
        if not result:
            raise cv.Invalid(
                f"{where}: none of the event types ({', '.join(event_types)}) is a known button "
                "gesture. Add `event_map: {<event type>: <presses or long_press>}`. Known names: "
                + ", ".join(sorted(DEFAULT_EVENT_NAMES)),
                path=[CONF_EVENT_MAP],
            )

    if "LatchingSwitch" in features:
        raise cv.Invalid(
            f"{where}: event entities report momentary gestures and cannot drive a "
            "LatchingSwitch. Use a binary_sensor for a toggle switch",
            path=[CONF_WITH_FEATURES],
        )
    multi = "MomentarySwitchMultiPress" in features or "ActionSwitch" in features
    for event_type, gesture in result.items():
        if not isinstance(gesture, int) or gesture == 1:
            continue
        if not multi:
            raise cv.Invalid(
                f"{where}: event type '{event_type}' maps to {gesture} presses, which needs the "
                "MomentarySwitchMultiPress feature (add it to `with_features`)",
                path=[CONF_EVENT_MAP],
            )
        if gesture > multi_press_max:
            raise cv.Invalid(
                f"{where}: event type '{event_type}' maps to {gesture} presses but "
                f"multi_press_max is {multi_press_max}. Raise `multi_press_max` to at least "
                f"{gesture}",
                path=[CONF_EVENT_MAP],
            )
    return result


def gesture_args(event_map: dict[str, int | str]) -> list[tuple[str, str, int, bool]]:
    """(event type, MatterEventGesture member, count, auto_release) per mapped event type."""
    has_end = GESTURE_PRESS_END in event_map.values()
    out = []
    for event_type, gesture in event_map.items():
        if gesture == GESTURE_LONG_PRESS:
            out.append((event_type, "LONG_PRESS", 1, not has_end))
        elif gesture == GESTURE_PRESS_START:
            out.append((event_type, "PRESS_START", 1, False))
        elif gesture == GESTURE_PRESS_END:
            out.append((event_type, "PRESS_END", 1, False))
        else:
            out.append((event_type, "PRESS", int(gesture), True))
    return out


def validate_event_bindings(matter_config: dict, full_config, device_types_by_key: dict) -> None:
    """FINAL_VALIDATE: resolve and check the event maps and keep them for the code generation."""
    from .generic_switch import CONF_MULTI_PRESS_MAX, DEFAULT_MULTI_PRESS_MAX

    store = CORE.data.setdefault(CONF_MATTER, {}).setdefault(KEY_EVENT_MAPS, {})
    store.clear()
    for endpoint_id, endpoint_config in matter_config.get(CONF_ENDPOINTS, {}).items():
        for conf_key, device_config in endpoint_config.items():
            device_type = device_types_by_key.get(conf_key)
            if device_type is None or not isinstance(device_config, dict):
                continue
            where = f"Matter endpoint {endpoint_id} {conf_key}"
            if CONF_EVENT_ID in device_config:
                store.setdefault(endpoint_id, {})[conf_key] = resolve_event_map(
                    f"{where}.event_id",
                    event_types_of(full_config, device_config[CONF_EVENT_ID]),
                    device_config.get(CONF_EVENT_MAP),
                    device_config.get(CONF_WITH_FEATURES, ()),
                    device_config.get(CONF_MULTI_PRESS_MAX, DEFAULT_MULTI_PRESS_MAX),
                )
            elif CONF_EVENT_MAP in device_config:
                raise cv.Invalid(f"{where}: event_map requires event_id", path=[CONF_EVENT_MAP])


async def register_event_binding(var, endpoint_id: int, conf_key: str, device_config: dict, make):
    """Create the event -> Switch events mapping of an endpoint (`make(event)` builds the object)."""
    event = await cg.get_variable(device_config[CONF_EVENT_ID])
    event_map = CORE.data[CONF_MATTER][KEY_EVENT_MAPS][endpoint_id][conf_key]
    mapping = await make(event)
    for event_type, gesture, count, auto_release in gesture_args(event_map):
        cg.add(
            mapping.add_event(
                event_type,
                cg.RawExpression(f"esphome::matter::MatterEventGesture::{gesture}"),
                count,
                auto_release,
            )
        )
    cg.add(var.register_mapping(mapping))
