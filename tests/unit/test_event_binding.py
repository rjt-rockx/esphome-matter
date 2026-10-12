"""`event` entities bound to the Switch cluster (event_id / event_map) and their expose inference."""

from __future__ import annotations

import esphome.config_validation as cv
from esphome.core import CORE
import pytest

from components.matter.event_binding import (
    DEFAULT_EVENT_NAMES,
    gesture_args,
    normalize,
    resolve_event_map,
)
from components.matter.expose.inference import NoMapping, Overrides, infer
from components.matter.expose.inventory import build_inventory
from components.matter.expose.lower import lower_expose
from components.matter.expose.schema import EXPOSE_SCHEMA

from conftest import CONFIGS, prepare_config, run_esphome

MULTI = ["MomentarySwitchRelease", "MomentarySwitchLongPress", "MomentarySwitchMultiPress"]


def resolve(types=None, configured=None, features=MULTI, max_=2):
    return resolve_event_map("ep", types, configured, features, max_)


# --- default names -------------------------------------------------------------------------


def test_default_names_map_the_common_gestures():
    got = resolve(["single", "Double Click", "triple-press", "hold", "release", "ring"], max_=3)
    assert got == {
        "single": 1,
        "Double Click": 2,
        "triple-press": 3,
        "hold": "long_press",
        "release": "press_end",
        "ring": 1,
    }


def test_unknown_event_types_are_left_unmapped():
    assert resolve(["single", "wiggle"]) == {"single": 1}


def test_nothing_recognised_is_an_error_naming_the_fix():
    with pytest.raises(cv.Invalid, match=r"event_map: \{<event type>"):
        resolve(["wiggle", "shake"])


def test_no_event_types_in_the_config_needs_an_event_map():
    with pytest.raises(cv.Invalid, match="no `event_types`"):
        resolve(None)
    assert resolve(None, {"x": 1}) == {"x": 1}


def test_normalize():
    assert normalize(" Long-Press ") == "long_press"
    assert all(normalize(name) == name for name in DEFAULT_EVENT_NAMES)


# --- explicit map and checks ---------------------------------------------------------------


def test_event_map_keys_must_be_event_types_of_the_entity():
    with pytest.raises(cv.Invalid, match="'tapp'.*Its event_types are: tap, hold"):
        resolve(["tap", "hold"], {"tapp": 1})


def test_more_presses_than_multi_press_max():
    with pytest.raises(cv.Invalid, match="multi_press_max is 2.*at least 3"):
        resolve(["triple"], max_=2)
    assert resolve(["triple"], max_=3) == {"triple": 3}


def test_multi_press_needs_the_feature():
    with pytest.raises(cv.Invalid, match="needs the MomentarySwitchMultiPress feature"):
        resolve(["double"], features=["MomentarySwitchRelease"])
    # A single press and a long press need nothing extra.
    assert resolve(["single", "hold"], features=["MomentarySwitchRelease"]) == {
        "single": 1,
        "hold": "long_press",
    }


def test_latching_switch_is_rejected():
    with pytest.raises(cv.Invalid, match="cannot drive a LatchingSwitch"):
        resolve(["single"], features=["LatchingSwitch"])


# --- C++ arguments -------------------------------------------------------------------------


def test_long_press_releases_itself_unless_a_release_is_mapped():
    assert gesture_args({"single": 1, "hold": "long_press"}) == [
        ("single", "PRESS", 1, True),
        ("hold", "LONG_PRESS", 1, True),
    ]
    assert gesture_args({"hold": "long_press", "up": "press_end", "down": "press_start"}) == [
        ("hold", "LONG_PRESS", 1, False),
        ("up", "PRESS_END", 1, False),
        ("down", "PRESS_START", 1, False),
    ]


# --- expose inference ----------------------------------------------------------------------


def ent(domain, id_, **kw):
    return {"platform": "template", "id": id_, "name": id_.replace("_", " ").title(), **kw}


RAW = {
    "event": [
        ent("event", "knob", device_class="button", event_types=["single"]),
        ent("event", "bell", device_class="doorbell", event_types=["ring"]),
        ent("event", "plain", event_types=["press"]),
    ],
    "binary_sensor": [ent("binary_sensor", "wired")],
    "button": [ent("button", "btn")],
}


@pytest.fixture
def raw():
    CORE.raw_config = RAW
    yield RAW
    CORE.raw_config = None


def lowered(expose):
    cfg = lower_expose({"expose": EXPOSE_SCHEMA(expose), "endpoints": {}})
    return [
        {
            key: {k: getattr(v, "id", v) for k, v in ep[key].items()}
            for key in ("generic_switch", "doorbell")
            if key in ep
        }
        for ep in cfg["endpoints"].values()
    ]


def entity(eid):
    return {e.id: e for e in build_inventory(RAW)}[eid]


def test_event_becomes_a_bound_generic_switch(raw):
    (ep,) = lowered(["knob"])
    assert ep["generic_switch"]["event_id"] == "knob"
    (ep,) = lowered(["plain"])
    assert ep["generic_switch"]["event_id"] == "plain"


def test_event_with_doorbell_class_becomes_a_doorbell(raw):
    (ep,) = lowered(["bell"])
    assert list(ep) == ["doorbell"] and ep["doorbell"]["event_id"] == "bell"
    assert infer(entity("bell"), Overrides()).primary == "doorbell"


def test_event_can_be_forced_to_either_type(raw):
    (ep,) = lowered([{"id": "knob", "as": "doorbell"}])
    assert ep["doorbell"]["event_id"] == "knob"
    (ep,) = lowered([{"id": "bell", "as": "generic_switch"}])
    assert ep["generic_switch"]["event_id"] == "bell"


def test_binary_sensor_as_doorbell(raw):
    (ep,) = lowered([{"id": "wired", "as": "doorbell"}])
    assert ep["doorbell"]["binary_sensor_id"] == "wired"


def test_button_as_doorbell_is_unbound(raw):
    (ep,) = lowered([{"id": "btn", "as": "doorbell"}])
    assert "event_id" not in ep["doorbell"] and "binary_sensor_id" not in ep["doorbell"]


def test_features_replace_the_defaults_so_a_doorbell_can_drop_multi_press(raw):
    (ep,) = lowered([{"id": "bell", "features": ["MomentarySwitchRelease"]}])
    assert "MomentarySwitchMultiPress" not in ep["doorbell"]["with_features"]
    assert "MomentarySwitchRelease" in ep["doorbell"]["with_features"]
    (ep,) = lowered(["bell"])
    assert "MomentarySwitchMultiPress" in ep["doorbell"]["with_features"]


@pytest.mark.parametrize("name", ["audio_doorbell", "video_doorbell"])
def test_camera_doorbells_are_refused_with_the_reason(raw, name):
    with pytest.raises(NoMapping, match="use `as: doorbell`"):
        infer(entity("bell"), Overrides(as_=name))


# --- configuration -------------------------------------------------------------------------


def test_event_configs_validate(tmp_path):
    for name in ("esp32-event-switch.yaml", "esp32-event-expose.yaml"):
        result = run_esphome("config", str(prepare_config(CONFIGS / name, tmp_path)), cwd=tmp_path)
        assert result.ok, result.output


EVENTS = """
event:
  - platform: template
    id: ev
    name: Ev
    event_types: [single, double, triple, hold]
"""


def config(endpoint: str) -> str:
    return f"matter:\n  endpoints:\n    1:\n{endpoint}\n{EVENTS}"


def test_event_id_and_binary_sensor_id_are_exclusive(run_config):
    result = run_config(
        config("      generic_switch: {event_id: ev, binary_sensor_id: b}")
        + "\nbinary_sensor:\n  - {platform: template, id: b, name: B}\n"
    )
    assert not result.ok
    assert "binary_sensor_id and event_id cannot be combined" in result.output


def test_triple_press_needs_multi_press_max(run_config):
    result = run_config(config("      generic_switch: {event_id: ev}"))
    assert not result.ok
    assert "'triple' maps to 3 presses but multi_press_max is 2" in result.output
    assert run_config(config("      generic_switch: {event_id: ev, multi_press_max: 3}")).ok


def test_latching_event_switch_is_rejected(run_config):
    result = run_config(
        config("      generic_switch: {event_id: ev, with_features: [LatchingSwitch]}")
    )
    assert not result.ok
    assert "cannot drive a LatchingSwitch" in result.output


def test_event_map_without_event_id(run_config):
    result = run_config(config("      generic_switch: {event_map: {single: 1}}"))
    assert not result.ok
    assert "event_map requires event_id" in result.output


def test_event_id_must_be_an_event(run_config):
    result = run_config(
        config("      generic_switch: {event_id: b}")
        + "\nbinary_sensor:\n  - {platform: template, id: b, name: B}\n"
    )
    assert not result.ok


def test_event_map_value_is_validated(run_config):
    result = run_config(
        config("      generic_switch: {event_id: ev, event_map: {single: sideways}}")
    )
    assert not result.ok
    assert "long_press" in result.output
