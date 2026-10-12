"""Unit tests for the `expose/` package: numbering, inventory, selectors, inference, lowering.

Pure Python: lowering is driven directly with a fake ``CORE.raw_config``.
"""

from __future__ import annotations

import esphome.config_validation as cv
from esphome.core import CORE
import pytest

from components.matter.expose import lower
from components.matter.expose.inference import (
    LoweredEndpoint,
    NoMapping,
    Overrides,
    infer,
    register_rule,
)
from components.matter.expose.inventory import build_inventory
from components.matter.expose.lower import bare_matter_error, lower_expose
from components.matter.expose.numbering import endpoint_for_key, natural_key, resolve_numbers
from components.matter.expose.schema import EXCLUDE_SCHEMA, EXPOSE_SCHEMA
from components.matter.expose.selectors import parse_items, select


def sw(id_, name=None, **kw):
    return {"platform": "template", "id": id_, "name": name or id_, **kw}


RAW = {
    "switch": [sw("relay"), sw("relay_1"), sw("relay_2"), sw("relay_10"), sw("hidden", internal=True)],
    "light": [
        {"platform": "rgbww", "id": "desk", "name": "Desk"},
        {"platform": "monochromatic", "id": "lamp", "name": "Lamp"},
        {"platform": "partition", "id": "strip", "name": "Strip"},
    ],
    "sensor": [
        {"platform": "template", "id": "t", "name": "T", "device_class": "temperature"},
        {"platform": "template", "id": "h", "name": "H", "device_class": "humidity"},
        {"platform": "template", "id": "diag", "name": "D", "device_class": "temperature",
         "entity_category": "diagnostic"},
        {"platform": "scd4x", "co2": {"name": "CO2"},
         "temperature": {"id": "scd_t", "name": "ST"}, "humidity": {"id": "scd_h", "name": "SH"}},
        {"platform": "template", "name": "No id", "device_class": "temperature"},
    ],
    "button": [{"platform": "template", "id": "btn", "name": "Btn"}],
}


@pytest.fixture
def raw():
    CORE.raw_config = RAW
    yield RAW
    CORE.raw_config = None


def run(expose, *, exclude=(), config=None, pins=None):
    cfg = {"expose": EXPOSE_SCHEMA(expose), "endpoints": dict(pins or {})}
    if exclude:
        cfg["exclude"] = EXCLUDE_SCHEMA(list(exclude))
    return lower_expose({**cfg, **(config or {})})


def ids(config):
    """endpoint number -> list of entity ids bound in it."""
    out = {}
    for number, ep in config["endpoints"].items():
        found = []
        for key, block in ep.items():
            if isinstance(block, dict):
                for value in block.values():
                    value = getattr(value, "id", value)
                    if isinstance(value, str):
                        found.append(value)
        out[number] = found
    return out


# --- numbering (section 7.2/7.3) ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "number"),
    [
        ("relay", 38775),
        ("desk", 34748),
        ("group:air_quality", 31397),
        ("relay_1", 30374),
        ("relay_2", 18953),
        ("relay_3", 18812),
        ("t", 52400),
        ("h", 55900),
    ],
)
def test_known_vectors(key, number):
    assert endpoint_for_key(key) == number


def test_range():
    assert all(1 <= endpoint_for_key(f"k{i}") <= 65534 for i in range(2000))


def test_collision_is_hard_error_with_suggestion():
    assert endpoint_for_key("e84") == endpoint_for_key("e276") == 47922
    with pytest.raises(cv.Invalid) as exc:
        resolve_numbers(["e276", "e84"], {})
    assert str(exc.value.msg) == (
        "matter: endpoint 47922 collides: e84 and e276. Pin one of them to a free endpoint: "
        "{id: e84, endpoint: 6810} (6810 is free); e276 then keeps 47922."
    )
    # the suggestion must itself resolve
    assert resolve_numbers(["e276", "e84"], {"e84": 6810}) == {"e84": 6810, "e276": 47922}


def test_pin_frees_the_other_key():
    numbers = resolve_numbers(["e84", "e276"], {"e276": 7})
    assert numbers == {"e276": 7, "e84": 47922}


def test_hash_vs_pin_collision():
    with pytest.raises(cv.Invalid, match="collides: relay and"):
        resolve_numbers(["relay"], {}, reserved={38775})
    with pytest.raises(cv.Invalid, match="collides: relay and x"):
        resolve_numbers(["relay"], {"x": 38775})


def test_duplicate_pins_error():
    with pytest.raises(cv.Invalid, match="pinned twice: a and b"):
        resolve_numbers(["a", "b"], {"a": 5, "b": 5})
    with pytest.raises(cv.Invalid, match="already defined under `endpoints:`"):
        resolve_numbers(["a"], {"a": 5}, reserved={5})


def test_pin_out_of_range():
    with pytest.raises(cv.Invalid, match="outside"):
        resolve_numbers(["a"], {"a": 0})
    with pytest.raises(cv.Invalid, match="outside"):
        resolve_numbers(["a"], {"a": 65535})


def test_natural_sort():
    assert sorted(["relay_10", "relay_2", "relay_1"], key=natural_key) == [
        "relay_1", "relay_2", "relay_10"
    ]


def test_numbers_do_not_depend_on_other_entities():
    a = resolve_numbers(["x", "y"], {})
    b = resolve_numbers(["y", "z", "x"], {})
    assert a["x"] == b["x"] and a["y"] == b["y"]


# --- inventory ------------------------------------------------------------------------------------


def test_inventory_fields_and_nesting(raw):
    inv = {e.id: e for e in build_inventory(raw) if e.id}
    assert inv["t"].device_class == "temperature" and inv["t"].domain == "sensor"
    assert inv["hidden"].internal and not inv["relay"].internal
    assert inv["diag"].entity_category == "diagnostic"
    assert inv["scd_t"].sub_key == "temperature" and inv["scd_t"].platform == "scd4x"
    assert inv["scd_t"].hint == "temperature"
    assert inv["desk"].platform == "rgbww"


def test_inventory_non_exposable_without_id(raw):
    noid = [e for e in build_inventory(raw) if not e.exposable]
    assert {e.name for e in noid} >= {"No id", "CO2"}


def test_inventory_two_levels():
    raw = {"sensor": [{"platform": "ina3221", "channel_1": {"current": {"id": "i1", "name": "I"}}}]}
    inv = build_inventory(raw)
    assert [(e.id, e.sub_key) for e in inv if e.id] == [("i1", "current")]


def test_inventory_pm_sub_sensor():
    raw = {"sensor": [{"platform": "pmsx003", "pm_2_5": {"id": "pm", "name": "PM"}}]}
    assert any(e.id == "pm" and e.sub_key == "pm_2_5" for e in build_inventory(raw))


def test_inventory_nameless_is_internal():
    raw = {"switch": [{"platform": "gpio", "id": "x"}]}
    assert build_inventory(raw)[0].internal


# --- selectors (section 5) ------------------------------------------------------------------------


def sel(raw_, expose, exclude=()):
    items = parse_items(EXPOSE_SCHEMA(expose))
    return select(build_inventory(raw_), items, list(exclude))


def names(selection):
    return [s.entity.id for s in selection.selected]


def test_bare_id_and_glob_natural_order(raw):
    assert names(sel(raw, ["relay_*"])) == ["relay_1", "relay_2", "relay_10"]
    assert names(sel(raw, ["relay"])) == ["relay"]


def test_glob_matches_nothing_is_error(raw):
    with pytest.raises(cv.Invalid, match="matches no entity"):
        sel(raw, ["zzz_*"])


def test_unknown_id_suggests(raw):
    with pytest.raises(cv.Invalid) as exc:
        sel(raw, ["rely_1"])
    assert "matter.expose: no entity with id 'rely_1'" in exc.value.msg
    assert "Did you mean" in exc.value.msg and "'relay_1'" in exc.value.msg


def test_unmappable_explicit_errors_with_reason(raw):
    lowered = None
    with pytest.raises(cv.Invalid, match="'btn' cannot be exposed: a button has no faithful"):
        lowered = run(["btn"])
    assert lowered is None


def test_all_skips_with_reasons(raw):
    s = sel(raw, ["all"])
    got = names(s)
    assert "relay" in got and "t" in got and "scd_t" in got
    skipped = dict(s.skipped)
    assert skipped["hidden"] == "internal: true"
    assert skipped["diag"] == "entity_category: diagnostic"
    assert "button" in skipped["btn"]
    assert "strip" in skipped and "partition" in skipped["strip"]
    assert any(r == "no explicit `id:`" for r in skipped.values())


def test_glob_skips_internal_but_explicit_may_expose(raw):
    assert "hidden" not in names(sel(raw, ["*"]))
    assert names(sel(raw, ["hidden"])) == ["hidden"]
    assert names(sel(raw, ["diag"])) == ["diag"]


def test_exclude_beats_glob_and_all(raw):
    assert names(sel(raw, ["relay_*"], ["relay_2"])) == ["relay_1", "relay_10"]
    s = sel(raw, ["all"], ["relay*"])
    assert not any(n.startswith("relay") for n in names(s))


def test_excluding_explicit_id_is_error(raw):
    with pytest.raises(cv.Invalid, match="listed explicitly"):
        sel(raw, ["relay_1", "relay_2"], ["relay_1"])
    with pytest.raises(cv.Invalid, match="listed explicitly"):
        sel(raw, [{"id": "relay_1", "as": "on_off_light"}], ["relay_*"])


def test_exclude_unknown_id_is_error(raw):
    with pytest.raises(cv.Invalid, match="matter.exclude: no entity with id"):
        sel(raw, ["relay"], ["nope"])


def test_precedence_explicit_over_glob_over_all(raw):
    s = sel(
        raw,
        [
            "all",
            {"id": "relay_*", "as": "on_off_light"},
            {"id": "relay_2", "label": "Two"},
        ],
    )
    by = {x.entity.id: x for x in s.selected}
    assert by["relay_1"].overrides.as_ == "on_off_light"  # glob beats all
    assert by["relay_2"].overrides.as_ is None  # explicit wins as a whole
    assert by["relay_2"].overrides.label == "Two"
    assert by["relay"].overrides.as_ is None


def test_glob_overrides_apply_to_every_match(raw):
    s = sel(raw, [{"id": "relay_*", "as": "on_off_light"}])
    assert [x.overrides.as_ for x in s.selected] == ["on_off_light"] * 3


def test_conflicting_overrides_error(raw):
    with pytest.raises(cv.Invalid, match="conflicting overrides"):
        sel(raw, [{"id": "relay", "as": "on_off_light"}, {"id": "relay", "label": "x"}])
    with pytest.raises(cv.Invalid, match="conflicting overrides"):
        sel(raw, [{"id": "relay_*", "label": "a"}, {"id": "relay_1*", "label": "b"}])


def test_duplicate_same_or_empty_is_fine(raw):
    s = sel(raw, ["relay", {"id": "relay", "label": "R"}, {"id": "relay", "label": "R"}])
    assert names(s) == ["relay"] and s.selected[0].overrides.label == "R"


# --- inference registry ---------------------------------------------------------------------------


def test_spike_rules(raw):
    inv = {e.id: e for e in build_inventory(raw)}
    assert list(infer(inv["relay"], Overrides()).device_types) == ["on_off_plug_in_unit"]
    assert list(infer(inv["desk"], Overrides()).device_types) == ["extended_color_light"]
    assert list(infer(inv["lamp"], Overrides()).device_types) == ["dimmable_light"]
    assert infer(inv["t"], Overrides()).device_types == {"temperature_sensor": {"temperature": "t"}}
    assert infer(inv["h"], Overrides()).device_types == {
        "humidity_sensor": {"relative_humidity": "h"}
    }
    assert list(infer(inv["scd_t"], Overrides()).device_types) == ["temperature_sensor"]
    with pytest.raises(NoMapping, match="partition"):
        infer(inv["strip"], Overrides())


def test_as_features_options(raw):
    inv = {e.id: e for e in build_inventory(raw)}
    out = infer(inv["relay"], Overrides(as_="on_off_light", features=["Lighting"], options={"x": 1}))
    assert out.device_types == {
        "on_off_light": {"switch_id": "relay", "with_features": ["Lighting"], "x": 1}
    }
    with pytest.raises(cv.Invalid, match="unknown device type 'on_of_light'.*on_off_light"):
        infer(inv["relay"], Overrides(as_="on_of_light"))
    with pytest.raises(cv.Invalid, match="collide with the binding"):
        infer(inv["relay"], Overrides(options={"switch_id": "x"}))


def test_registry_is_extensible(raw):
    from components.matter.expose import inference

    inv = {e.id: e for e in build_inventory(raw)}
    saved = dict(inference._RULES)
    try:
        register_rule(
            "button",
            lambda e, o: LoweredEndpoint({"on_off_plug_in_unit": {"switch_id": e.id}}, merge_group="g"),
        )
        assert infer(inv["btn"], Overrides()).merge_group == "g"
    finally:
        inference._RULES.clear()
        inference._RULES.update(saved)


# --- lowering -------------------------------------------------------------------------------------


def test_lowered_endpoints_and_numbers(raw):
    cfg = run(["relay", "desk", "lamp", "t"])
    assert ids(cfg) == {
        endpoint_for_key("relay"): ["relay"],
        endpoint_for_key("desk"): ["desk"],
        endpoint_for_key("lamp"): ["lamp"],
        endpoint_for_key("t"): ["t"],
    }
    assert sorted(cfg["endpoints"]) == list(cfg["endpoints"])  # sorted


def test_pin_and_was(raw):
    cfg = run([{"id": "relay", "endpoint": 5}, {"id": "t", "was": "old_t"}])
    assert 5 in cfg["endpoints"]
    assert endpoint_for_key("old_t") in cfg["endpoints"]
    assert endpoint_for_key("t") not in cfg["endpoints"]


def test_was_key_collision_with_other_entity(raw):
    with pytest.raises(cv.Invalid, match="used twice"):
        run(["relay", {"id": "t", "was": "relay"}])


def test_duplicate_pins_in_items(raw):
    with pytest.raises(cv.Invalid, match="pinned twice"):
        run([{"id": "relay", "endpoint": 5}, {"id": "t", "endpoint": 5}])


def test_item_pin_vs_handwritten_endpoint(raw):
    handwritten = {7: {"on_off_plug_in_unit": {"switch_id": "relay_1"}}}
    with pytest.raises(cv.Invalid, match="already defined under `endpoints:`"):
        run([{"id": "relay", "endpoint": 7}], pins=handwritten)


def test_mixing_with_endpoints_keeps_pins(raw):
    from components.matter.endpoints import ENDPOINT_SCHEMA

    hand = {7: ENDPOINT_SCHEMA({"on_off_plug_in_unit": {"switch_id": "relay_1"}})}
    cfg = run(["relay"], pins=hand)
    assert set(cfg["endpoints"]) == {7, endpoint_for_key("relay")}


def test_hash_collision_through_lowering():
    CORE.raw_config = {"switch": [sw("e84"), sw("e276")]}
    try:
        with pytest.raises(cv.Invalid, match=r"endpoint 47922 collides: e84 and e276"):
            run(["e84", "e276"])
        # pinning one key to the shared number does not help: the other still hashes there
        with pytest.raises(cv.Invalid, match="collides: e84 and e276 \\(pinned\\)"):
            run(["e84", {"id": "e276", "endpoint": 47922}])
    finally:
        CORE.raw_config = None


def test_pin_one_of_the_colliding_keys():
    CORE.raw_config = {"switch": [sw("e84"), sw("e276")]}
    try:
        cfg = run(["e84", {"id": "e276", "endpoint": 6810}])
        assert ids(cfg) == {47922: ["e84"], 6810: ["e276"]}
    finally:
        CORE.raw_config = None


def test_expose_empty_is_valid_root_only(raw):
    cfg = run([])
    assert cfg["endpoints"] == {}


def test_expose_all_with_exclude(raw):
    cfg = run("all", exclude=["relay*"])
    bound = {v for found in ids(cfg).values() for v in found}
    assert {"desk", "lamp", "t", "h", "scd_t", "scd_h"} <= bound
    assert not any(b.startswith("relay") for b in bound)
    assert "hidden" not in bound and "diag" not in bound


def test_merge_group_and_attach_to(raw):
    from components.matter.expose import inference

    saved = dict(inference._RULES)
    try:
        register_rule(
            "sensor",
            lambda e, o: LoweredEndpoint(
                {"temperature_sensor": {"temperature": e.id}}, merge_group="air_quality"
            )
            if e.id == "t"
            else None,
        )
        # `t` is rewritten into group:air_quality (precedes the spike sensor rule)
        _RULES = inference._RULES["sensor"]
        _RULES.insert(0, _RULES.pop())
        cfg = run(["t", {"id": "h", "attach_to": "t"}])
        assert list(cfg["endpoints"]) == [endpoint_for_key("group:air_quality")]
        assert set(cfg["endpoints"][endpoint_for_key("group:air_quality")]) >= {
            "temperature_sensor",
            "humidity_sensor",
        }
    finally:
        inference._RULES.clear()
        inference._RULES.update(saved)


def test_attach_to_unexposed_target(raw):
    with pytest.raises(cv.Invalid, match="attach_to"):
        run([{"id": "h", "attach_to": "t"}])


def test_label_dropped_when_base_lacks_it(raw, caplog):
    assert lower._supports_label() is False
    with caplog.at_level("DEBUG"):
        cfg = run([{"id": "relay", "label": "Plug"}])
    assert "label" not in cfg["endpoints"][endpoint_for_key("relay")]
    assert "dropped" in caplog.text


def test_wrong_pairing_is_left_to_use_id(raw):
    # relay is a switch; the humidity_sensor binding is a sensor id. The stock ID pass reports it.
    cfg = run([{"id": "relay", "as": "humidity_sensor"}])
    block = cfg["endpoints"][endpoint_for_key("relay")]["humidity_sensor"]
    assert block["relative_humidity"].id == "relay"


def test_plan_logged(raw, caplog):
    with caplog.at_level("INFO"):
        run(["relay", "t"])
    assert "matter.expose plan" in caplog.text
    assert str(endpoint_for_key("relay")) in caplog.text


# --- bare matter: (section 5 text) ----------------------------------------------------------------


def test_bare_matter_error_text(raw):
    with pytest.raises(cv.Invalid) as exc:
        lower_expose({"endpoints": {}})
    text = exc.value.msg
    assert text.startswith("matter: nothing to expose. Add one of:\n")
    assert "  expose: [relay, relay_1, relay_2, relay_10, desk, lamp, t, h, scd_t, scd_h]" in text
    assert "# inferred from your config; edit as needed" in text
    assert "\n  expose: all\n" in text
    assert text.endswith("expose: []                       # root node only")
    assert "hidden" not in text and "strip" not in text and "btn" not in text


def test_bare_error_when_nothing_inferable():
    CORE.raw_config = {}
    try:
        assert "expose: [<entity id>]" in bare_matter_error(build_inventory({}))
    finally:
        CORE.raw_config = None


def test_endpoints_alone_is_fine(raw):
    cfg = {"endpoints": {1: {}}}
    assert lower_expose(cfg) is cfg


def test_empty_endpoints_key_counts_as_given():
    CORE.raw_config = {"matter": {"endpoints": {}}}
    try:
        cfg = {"endpoints": {}}
        assert lower_expose(cfg) is cfg
    finally:
        CORE.raw_config = None
