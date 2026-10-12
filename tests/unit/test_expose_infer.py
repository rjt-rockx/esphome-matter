"""Inference rules, tags, labels, parent, clusters, glob warnings and metering of `expose:`.

Pure Python: lowering is driven directly with a fake ``CORE.raw_config`` (like test_expose.py).
"""

from __future__ import annotations

import logging

import esphome.config_validation as cv
from esphome.core import CORE
import pytest

from components.matter.expose.inference import NoMapping, Overrides, infer
from components.matter.expose.inventory import build_inventory
from components.matter.expose.lower import lower_expose
from components.matter.expose.numbering import endpoint_for_key
from components.matter.expose.schema import EXCLUDE_SCHEMA, EXPOSE_SCHEMA


def ent(domain, id_, platform="template", name=None, **kw):
    return {"platform": platform, "id": id_, "name": name or id_.replace("_", " ").title(), **kw}


def sensor(id_, device_class, unit=None, **kw):
    node = ent("sensor", id_, device_class=device_class, **kw)
    if unit:
        node["unit_of_measurement"] = unit
    return node


RAW = {
    "switch": [ent("switch", f"relay_{n}") for n in (1, 2, 10)],
    "light": [ent("light", "lamp", "monochromatic")],
    "lock": [ent("lock", "front")],
    "cover": [
        ent("cover", "blind", device_class="blind"),
        ent("cover", "garage", device_class="garage"),
        ent("cover", "plain"),
    ],
    "fan": [ent("fan", "fan_1"), ent("fan", "fan_2")],
    "valve": [ent("valve", "water")],
    "climate": [
        ent("climate", "heater", "thermostat"),
        ent("climate", "ac", "climate_ir_lg"),
    ],
    "binary_sensor": [
        ent("binary_sensor", "door", device_class="door"),
        ent("binary_sensor", "win", device_class="window"),
        ent("binary_sensor", "gate_open", device_class="opening"),
        ent("binary_sensor", "pir", device_class="motion"),
        ent("binary_sensor", "occ", device_class="occupancy"),
        ent("binary_sensor", "pres", device_class="presence"),
        ent("binary_sensor", "leak", device_class="moisture"),
        ent("binary_sensor", "smoke", device_class="smoke"),
        ent("binary_sensor", "co_alarm", device_class="carbon_monoxide"),
        ent("binary_sensor", "bare"),
        ent("binary_sensor", "plugged", device_class="plug"),
    ],
    "sensor": [
        sensor("pressure", "pressure", "hPa"),
        sensor("lux", "illuminance", "lx"),
        sensor("flow", "volume_flow_rate", "L/min"),
        sensor("co2", "carbon_dioxide", "ppm"),
        sensor("pm25", "pm25", "µg/m³"),
        sensor("pm10", "pm10", "µg/m³"),
        sensor("voc", "volatile_organic_compounds", "µg/m³"),
        sensor("batt", "battery", "%"),
        sensor("batt2", "battery", "%"),
        sensor("door_batt", "battery", "%"),
        sensor("temp", "temperature", "°C"),
        sensor("hum", "humidity", "%"),
        sensor("power", "power", "W"),
        sensor("energy", "energy", "kWh"),
        sensor("volts", "voltage", "V"),
        sensor("amps", "current", "A"),
        sensor("freq", "frequency", "Hz"),
        sensor("pf", "power_factor"),
        sensor("relay_1_power", "power", "W"),
        sensor("relay_2_power", "power", "W"),
        sensor("power_10", "power", "W"),
        sensor("odd", "duration", "s"),
    ],
    "event": [ent("event", "knob")],
    "button": [ent("button", "btn"), ent("button", "btn_2")],
}


@pytest.fixture
def raw():
    CORE.raw_config = RAW
    yield RAW
    CORE.raw_config = None


def run(expose, *, exclude=(), raw_override=None):
    if raw_override is not None:
        CORE.raw_config = raw_override
    cfg = {"expose": EXPOSE_SCHEMA(expose), "endpoints": {}}
    if exclude:
        cfg["exclude"] = EXCLUDE_SCHEMA(list(exclude))
    return lower_expose(cfg)


def entity(eid):
    return {e.id: e for e in build_inventory(RAW)}[eid]


def bound(block):
    return {k: getattr(v, "id", v) for k, v in block.items() if isinstance(v, (str, type(None))) or hasattr(v, "id")}


def ep_of(cfg, eid):
    """The lowered endpoint dict that binds entity `eid`."""
    for ep in cfg["endpoints"].values():
        for block in ep.values():
            if isinstance(block, dict) and eid in [getattr(v, "id", v) for v in block.values()]:
                return ep
        if _batt_id(ep.get("battery")) == eid:
            return ep
    raise AssertionError(f"{eid} is not bound in {list(cfg['endpoints'])}")


def types_of(ep):
    from components.matter.data_model.device_types import DEVICE_TYPES_BY_CONF_KEY

    return [k for k in ep if k in DEVICE_TYPES_BY_CONF_KEY]


def primary(eid):
    return infer(entity(eid), Overrides(via_glob=True)).primary


# --- inference without `as:` ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("eid", "device_type"),
    [
        ("relay_1", "on_off_plug_in_unit"),
        ("front", "door_lock"),
        ("blind", "window_covering"),
        ("plain", "window_covering"),
        ("fan_1", "fan"),
        ("water", "water_valve"),
        ("heater", "thermostat"),
        ("ac", "room_air_conditioner"),
        ("door", "contact_sensor"),
        ("win", "contact_sensor"),
        ("gate_open", "contact_sensor"),
        ("pir", "occupancy_sensor"),
        ("occ", "occupancy_sensor"),
        ("pres", "occupancy_sensor"),
        ("leak", "water_leak_detector"),
        ("smoke", "smoke_co_alarm"),
        ("co_alarm", "smoke_co_alarm"),
        ("pressure", "pressure_sensor"),
        ("lux", "light_sensor"),
        ("flow", "flow_sensor"),
        ("co2", "air_quality_sensor"),
        ("pm25", "air_quality_sensor"),
        ("pm10", "air_quality_sensor"),
        ("voc", "air_quality_sensor"),
        ("power", "electrical_sensor"),
        ("energy", "electrical_sensor"),
        ("volts", "electrical_sensor"),
        ("amps", "electrical_sensor"),
        ("freq", "electrical_sensor"),
        ("pf", "electrical_sensor"),
        ("batt", "power_source"),
        ("knob", "generic_switch"),
    ],
)
def test_inferred_device_type(raw, eid, device_type):
    assert primary(eid) == device_type


def test_garage_cover_uses_closure_compat_when_the_schema_has_it(raw, monkeypatch):
    from components.matter.expose import inference

    monkeypatch.setattr(inference, "closure_compat_supported", lambda: True)
    out = infer(entity("garage"), Overrides())
    assert out.device_types == {"window_covering": {"cover_id": "garage", "closure_compat": True}}
    plain = infer(entity("blind"), Overrides())
    assert "closure_compat" not in plain.device_types["window_covering"]


def test_garage_cover_falls_back_to_plain_window_covering(raw, monkeypatch, caplog):
    from components.matter.expose import inference

    monkeypatch.setattr(inference, "closure_compat_supported", lambda: False)
    inference._WARNED.clear()
    with caplog.at_level("WARNING"):
        cfg = run(["garage"])
    with pytest.raises(NoMapping, match="closure_compat"):
        infer(entity("garage"), Overrides(via_glob=True))
    assert ep_of_unbound(cfg)["window_covering"]["cover_id"].id == "garage"
    assert not ep_of_unbound(cfg)["window_covering"].get("closure_compat")  # schema default is False
    assert "plain Window Covering" in caplog.text and "garage" in caplog.text


def test_bare_binary_sensor_hint_names_the_fix(raw):
    with pytest.raises(NoMapping) as exc:
        infer(entity("bare"), Overrides())
    assert "device_class" in str(exc.value) and "as: generic_switch" in str(exc.value)
    with pytest.raises(NoMapping):
        infer(entity("plugged"), Overrides())


def test_unknown_sensor_class_lists_supported_ones(raw):
    with pytest.raises(NoMapping, match="volume_flow_rate"):
        infer(entity("odd"), Overrides())


def ep_of_unbound(cfg):
    (ep,) = cfg["endpoints"].values()
    return ep


def test_button_is_explicit_only(raw):
    with pytest.raises(NoMapping, match="list its id"):
        infer(entity("btn"), Overrides(via_glob=True))
    assert types_of(ep_of_unbound(run(["btn"]))) == ["generic_switch"]
    skipped = dict(next(iter(_plan_skips(["btn*"]))))
    assert "list its id" in skipped["btn"]


def _plan_skips(expose):
    from components.matter.expose.inventory import build_inventory as inv
    from components.matter.expose.selectors import parse_items, select

    sel = select(inv(RAW), parse_items(EXPOSE_SCHEMA(expose)), [])
    yield sel.skipped


def test_event_becomes_a_generic_switch_bound_to_its_events(raw):
    ep = ep_of_unbound(run(["knob"]))
    assert types_of(ep) == ["generic_switch"]
    assert ep["generic_switch"]["event_id"].id == "knob"
    assert "binary_sensor_id" not in ep["generic_switch"]


def test_binary_sensor_with_a_class_validates(raw):
    cfg = run(["door", "pir", "leak"])
    assert {tuple(types_of(ep)) for ep in cfg["endpoints"].values()} == {
        ("contact_sensor",),
        ("occupancy_sensor",),
        ("water_leak_detector",),
    }


def test_smoke_and_co_share_one_endpoint(raw):
    cfg = run(["smoke", "co_alarm"])
    (ep,) = cfg["endpoints"].values()
    assert set(ep["smoke_co_alarm"]) >= {"smoke_state", "co_state"}
    assert list(cfg["endpoints"]) == [endpoint_for_key("group:smoke_co_alarm")]


def test_air_quality_sensors_merge(raw):
    cfg = run(["co2", "pm25", "pm10", "voc"])
    assert list(cfg["endpoints"]) == [endpoint_for_key("group:air_quality")]
    (ep,) = cfg["endpoints"].values()
    assert set(ep["air_quality_sensor"]) >= {"carbon_dioxide", "pm_2_5", "pm_10", "total_voc"}


def test_nested_sub_sensor_hints_map(raw):
    raw2 = {
        "sensor": [
            {
                "platform": "scd4x",
                "co2": {"id": "scd_co2", "name": "CO2"},
                "temperature": {"id": "scd_t", "name": "T"},
                "humidity": {"id": "scd_h", "name": "H"},
            }
        ]
    }
    cfg = run(["scd_co2", "scd_t", "scd_h"], raw_override=raw2)
    kinds = sorted(types_of(ep)[0] for ep in cfg["endpoints"].values())
    assert kinds == ["air_quality_sensor", "humidity_sensor", "temperature_sensor"]


def _batt_id(value):
    """battery: <sensor> or the normalised {percent: <sensor>, description, order}."""
    if isinstance(value, dict) and "percent" in value:
        value = value["percent"]
    return getattr(value, "id", value)


# --- battery -------------------------------------------------------------------------------


def test_unrelated_battery_gets_its_own_endpoint_even_with_one_device(raw):
    cfg = run(["door", "batt"])
    assert len(cfg["endpoints"]) == 2
    assert types_of(ep_of(cfg, "batt")) == ["power_source"]


def test_related_battery_attaches(raw):
    cfg = run(["door", "door_batt"])
    (ep,) = cfg["endpoints"].values()
    assert types_of(ep) == ["contact_sensor"]
    assert _batt_id(ep["battery"]) == "door_batt"


def test_battery_with_several_endpoints_gets_its_own_endpoint(raw):
    cfg = run(["door", "pir", "batt"])
    assert len(cfg["endpoints"]) == 3
    assert types_of(ep_of(cfg, "batt")) == ["power_source"]


def test_battery_attach_to_picks_the_endpoint(raw):
    cfg = run(["door", "pir", {"id": "batt", "attach_to": "pir"}])
    assert len(cfg["endpoints"]) == 2
    ep = ep_of(cfg, "pir")
    assert _batt_id(ep["battery"]) == "batt"


def test_two_batteries_do_not_collide_when_standalone(raw):
    cfg = run(["door", "pir", "batt", "batt2"])
    assert len(cfg["endpoints"]) == 4


def test_battery_as_override_is_a_plain_power_source(raw):
    cfg = run([{"id": "batt", "as": "power_source"}])
    assert types_of(ep_of_unbound(cfg)) == ["power_source"]


# --- metering ------------------------------------------------------------------------------


def test_single_switch_gets_all_its_meters(raw):
    cfg = run(["relay_1", "power", "energy", "volts", "amps"])
    (ep,) = cfg["endpoints"].values()
    assert types_of(ep) == ["on_off_plug_in_unit", "electrical_sensor"]
    assert set(ep["electrical_sensor"]) >= {
        "active_power",
        "energy_imported",
        "voltage",
        "active_current",
    }


def test_meters_names_the_load(raw):
    cfg = run(["relay_1", "relay_2", {"id": "power", "meters": "relay_2"}])
    assert "electrical_sensor" in ep_of(cfg, "relay_2")
    assert "electrical_sensor" not in ep_of(cfg, "relay_1")


def test_meter_prefix_picks_the_load_when_unambiguous(raw):
    cfg = run(["relay_1", "relay_2", "relay_1_power", "relay_2_power"])
    assert len(cfg["endpoints"]) == 2
    assert getattr(ep_of(cfg, "relay_1")["electrical_sensor"]["active_power"], "id", None) == (
        "relay_1_power"
    )
    assert getattr(ep_of(cfg, "relay_2")["electrical_sensor"]["active_power"], "id", None) == (
        "relay_2_power"
    )


def test_meter_trailing_number_picks_the_load(raw):
    cfg = run(["relay_1", "relay_10", "power_10"])
    assert "electrical_sensor" in ep_of(cfg, "relay_10")


def test_ambiguous_meter_gets_a_standalone_electrical_endpoint(raw):
    cfg = run(["relay_1", "relay_2", "power", "volts"])
    assert endpoint_for_key("group:electrical") in cfg["endpoints"]
    ep = cfg["endpoints"][endpoint_for_key("group:electrical")]
    assert types_of(ep) == ["electrical_sensor"]
    assert set(ep["electrical_sensor"]) >= {"active_power", "voltage"}


def test_meter_without_any_load_is_standalone(raw):
    cfg = run(["power", "energy"])
    assert list(cfg["endpoints"]) == [endpoint_for_key("group:electrical")]


def test_two_unmatched_power_sensors_spill_into_separate_endpoints(raw):
    cfg = run(["relay_1", "relay_2", "power", "grid_power"], raw_override=_with_extra_power())
    meters = [ep for ep in cfg["endpoints"].values() if types_of(ep) == ["electrical_sensor"]]
    assert len(meters) == 2


def _with_extra_power():
    return {**RAW, "sensor": [*RAW["sensor"], sensor("grid_power", "power", "W")]}


def test_meters_on_a_non_meter_is_an_error(raw):
    with pytest.raises(cv.Invalid, match="meters:"):
        run(["relay_1", {"id": "temp", "meters": "relay_1"}])


# --- tags: natural order, trailing integers ------------------------------------------------


def tag_numbers(cfg, eids):
    out = {}
    for eid in eids:
        (tag,) = ep_of(cfg, eid)["tags"]
        out[eid] = tag["tag"]
    return out


def test_tags_follow_trailing_integers_not_the_hash(raw):
    cfg = run(["relay_1", "relay_2", "relay_10"])
    assert tag_numbers(cfg, ["relay_1", "relay_2", "relay_10"]) == {
        "relay_1": 1,
        "relay_2": 2,
        "relay_10": 10,
    }


def test_ordinals_when_ids_do_not_end_in_numbers(raw):
    raw2 = {"switch": [ent("switch", n) for n in ("zebra", "apple", "mango")]}
    cfg = run(["zebra", "apple", "mango"], raw_override=raw2)
    got = {}
    for eid in ("apple", "mango", "zebra"):
        got[eid] = ep_of(cfg, eid)["tags"][0]["tag"]
    assert got == {"apple": 1, "mango": 2, "zebra": 3}


def test_tags_do_not_depend_on_the_hash(raw):
    assert endpoint_for_key("relay_1") > endpoint_for_key("relay_2")  # hash order is reversed
    cfg = run(["relay_2", "relay_10", "relay_1"])
    assert tag_numbers(cfg, ["relay_1", "relay_2", "relay_10"]) == {
        "relay_1": 1,
        "relay_2": 2,
        "relay_10": 10,
    }


def test_explicit_tags_replace_automatic_ones(raw):
    cfg = run(
        [
            {"id": "relay_1", "tags": [{"namespace": "common_position", "tag": "left"}]},
            "relay_2",
        ]
    )
    (tag,) = ep_of(cfg, "relay_1")["tags"]
    assert (tag["namespace"], tag["tag"]) == (8, 0)
    assert ep_of(cfg, "relay_2")["tags"][0]["tag"] == 2  # trailing integer, Number 2


def test_explicit_number_tags_are_not_reused(raw):
    raw2 = {"fan": [ent("fan", "zz"), ent("fan", "fan_1"), ent("fan", "fan_2")]}
    cfg = run(
        [{"id": "zz", "tags": [{"namespace": "common_number", "tag": 1}]}, "fan_1", "fan_2"],
        raw_override=raw2,
    )
    got = {eid: ep_of(cfg, eid)["tags"][0]["tag"] for eid in ("zz", "fan_1", "fan_2")}
    # fan_1 would take Number 1, which zz uses: it gets a free number, fan_2 keeps its own
    assert got == {"zz": 1, "fan_2": 2, "fan_1": 3}


def test_tag_validation_names_unknown_tags(raw):
    with pytest.raises(cv.Invalid, match="Unknown tag"):
        EXPOSE_SCHEMA([{"id": "relay_1", "tags": [{"namespace": "common_position", "tag": "x"}]}])


def test_explicit_tags_on_a_non_duplicate_are_kept(raw):
    cfg = run([{"id": "lamp", "tags": [{"namespace": "common_position", "tag": "top"}]}])
    assert ep_of_unbound(cfg)["tags"][0]["tag"] == 2


# --- labels only in duplicate sets ---------------------------------------------------------


def test_labels_only_for_duplicates(raw):
    cfg = run(["relay_1", "relay_2", "lamp", "door"])
    assert ep_of(cfg, "relay_1")["label"] == "Relay 1"
    assert ep_of(cfg, "relay_2")["label"] == "Relay 2"
    assert "label" not in ep_of(cfg, "lamp")
    assert "label" not in ep_of(cfg, "door")
    assert "tags" not in ep_of(cfg, "lamp")


def test_siblings_of_different_types_are_not_duplicates(raw):
    cfg = run(["relay_1", "lamp", "fan_1", "door"])
    assert all("label" not in ep and "tags" not in ep for ep in cfg["endpoints"].values())


def test_explicit_label_always_applies(raw):
    cfg = run([{"id": "door", "label": "Front"}])
    assert ep_of_unbound(cfg)["label"] == "Front"


def test_electrical_sensor_does_not_make_a_duplicate(raw):
    cfg = run(["relay_1", "lamp", "power", "energy"])
    assert "tags" not in ep_of(cfg, "relay_1")


# --- parent, clusters ----------------------------------------------------------------------


def test_parent_resolves_to_the_endpoint_number(raw):
    cfg = run(["heater", {"id": "temp", "parent": "heater"}])
    assert ep_of(cfg, "temp")["parent"] == endpoint_for_key("heater")
    assert "parent" not in ep_of(cfg, "heater")


def test_parent_must_be_exposed(raw):
    with pytest.raises(cv.Invalid, match="parent"):
        run([{"id": "temp", "parent": "heater"}])


def test_parent_cannot_be_itself(raw):
    with pytest.raises(cv.Invalid, match="own parent"):
        run([{"id": "temp", "parent": "temp"}])


def test_parent_number_is_passed_through(raw):
    cfg = run([{"id": "temp", "parent": 7}])
    assert ep_of_unbound(cfg)["parent"] == 7


def test_children_with_a_parent_are_not_siblings_of_top_level(raw):
    cfg = run(["temp", {"id": "hum", "parent": "temp"}, "heater"])
    assert all("tags" not in ep for ep in cfg["endpoints"].values())


def test_clusters_are_passed_to_the_endpoint(raw):
    cfg = run([{"id": "relay_1", "clusters": {"identify": {}}}])
    assert "identify" in ep_of_unbound(cfg)["clusters"]


def test_attach_item_cannot_carry_endpoint_keys(raw):
    with pytest.raises(cv.Invalid, match="put it on the item of 'heater'"):
        run(["heater", {"id": "hum", "attach_to": "heater", "tags": [{"namespace": 7, "tag": 1}]}])


# --- glob warning --------------------------------------------------------------------------


def test_unnamed_entities_skipped_by_a_glob_warn(caplog):
    raw2 = {
        "switch": [
            ent("switch", "relay_1"),
            {"platform": "template", "id": "relay_2"},
            {"platform": "template", "id": "relay_3"},
        ]
    }
    CORE.raw_config = raw2
    try:
        with caplog.at_level(logging.INFO):
            cfg = run(["relay_*"])
    finally:
        CORE.raw_config = None
    assert len(cfg["endpoints"]) == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    (rec,) = warnings
    text = rec.getMessage()
    assert "relay_2, relay_3" in text and "`name:`" in text and "explicitly" in text


def test_no_warning_when_every_match_is_named(raw, caplog):
    with caplog.at_level(logging.WARNING):
        run(["relay_*"])
    assert not [r for r in caplog.records if "no `name:`" in r.getMessage()]


# --- esphome config on tests/configs/esp32-expose-infer.yaml --------------------------------


def test_expose_infer_config_validates_without_as(tmp_path):
    from conftest import CONFIGS, prepare_config, run_esphome

    config = prepare_config(CONFIGS / "esp32-expose-infer.yaml", tmp_path)
    result = run_esphome("config", str(config), cwd=tmp_path)
    assert result.ok, result.output[-3000:]
    out = result.output
    for device_type in (
        "door_lock",
        "window_covering",
        "fan",
        "thermostat",
        "room_air_conditioner",
        "water_valve",
        "contact_sensor",
        "occupancy_sensor",
        "water_leak_detector",
        "smoke_co_alarm",
        "pressure_sensor",
        "light_sensor",
        "flow_sensor",
        "air_quality_sensor",
        "electrical_sensor",
        "power_source",
        "generic_switch",
    ):
        assert device_type in out, device_type
