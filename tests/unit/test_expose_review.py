"""Regression tests for the review of `expose:` (scenarios a1-a10, c1-c4 of the review)."""

from __future__ import annotations

import esphome.config_validation as cv
from esphome.core import CORE
import pytest

from components.matter.expose.inference import NoMapping, Overrides, infer
from components.matter.expose.inventory import build_inventory
from components.matter.expose.lower import lower_expose
from components.matter.expose.numbering import endpoint_for_key
from components.matter.expose.schema import EXCLUDE_SCHEMA, EXPOSE_SCHEMA


def ent(id_, platform="template", name=None, **kw):
    return {"platform": platform, "id": id_, "name": name or id_, **kw}


def sens(id_, device_class, unit=None, name=None):
    node = ent(id_, name=name, device_class=device_class)
    if unit:
        node["unit_of_measurement"] = unit
    return node


def run(raw, expose, *, endpoints=None, exclude=()):
    from components.matter.endpoints import ENDPOINT_SCHEMA

    CORE.raw_config = raw
    try:
        cfg = {
            "expose": EXPOSE_SCHEMA(expose),
            "endpoints": {n: ENDPOINT_SCHEMA(v) for n, v in (endpoints or {}).items()},
        }
        if exclude:
            cfg["exclude"] = EXCLUDE_SCHEMA(list(exclude))
        return lower_expose(cfg)
    finally:
        CORE.raw_config = None


def idof(value):
    return getattr(value, "id", value)


def endpoint_with(cfg, eid):
    for ep in cfg["endpoints"].values():
        for key, block in ep.items():
            if key == "battery" and idof(block) == eid:
                return ep
            if isinstance(block, dict) and eid in [idof(v) for v in block.values()]:
                return ep
    raise AssertionError(f"{eid} is not bound")


def types(ep):
    from components.matter.data_model.device_types import DEVICE_TYPES_BY_CONF_KEY

    return [k for k in ep if k in DEVICE_TYPES_BY_CONF_KEY]


def count(cfg, device_type):
    return sum(1 for ep in cfg["endpoints"].values() if device_type in ep)


# --- a1, #4: a second battery is never a hard error -------------------------------------------


def test_second_battery_for_one_endpoint_goes_standalone():
    raw = {
        "switch": [ent("sw1")],
        "sensor": [
            sens("sw1_batt", "battery", "%"),
            sens("sw1_batt_2", "battery", "%"),
            sens("bat_x", "battery", "%"),
            sens("bat_y", "battery", "%"),
        ],
    }
    cfg = run(raw, ["sw1", "sw1_batt", "sw1_batt_2"])
    assert idof(endpoint_with(cfg, "sw1")["battery"]) == "sw1_batt"
    assert types(endpoint_with(cfg, "sw1_batt_2")) == ["power_source"]
    cfg = run(raw, ["sw1", "bat_x", "bat_y"])  # unrelated: both standalone, no error
    assert count(cfg, "power_source") == 2


# --- a2, a8, a7, #2: multi-attribute `as:` and the rules share one table ------------------------


def test_as_air_quality_sensor_binds_by_device_class():
    raw = {"sensor": [sens("c", "carbon_dioxide", "ppm")]}
    cfg = run(raw, [{"id": "c", "as": "air_quality_sensor"}])
    (ep,) = cfg["endpoints"].values()
    assert idof(ep["air_quality_sensor"]["carbon_dioxide"]) == "c"


def test_as_smoke_co_alarm_binds_by_device_class():
    raw = {"binary_sensor": [ent("s", device_class="smoke")]}
    cfg = run(raw, [{"id": "s", "as": "smoke_co_alarm"}])
    (ep,) = cfg["endpoints"].values()
    assert idof(ep["smoke_co_alarm"]["smoke_state"]) == "s"


def test_as_electrical_sensor_binds_by_device_class(caplog):
    raw = {"switch": [ent("sw_a")], "sensor": [sens("pa", "power", "W")]}
    cfg = run(raw, ["sw_a", {"id": "pa", "as": "electrical_sensor"}])
    assert types(endpoint_with(cfg, "pa")) == ["electrical_sensor"]  # standalone by `as:`
    assert "electrical_sensor" not in endpoint_with(cfg, "sw_a")


def test_as_multi_attribute_without_a_class_names_the_choices():
    raw = {"sensor": [ent("mystery")]}
    with pytest.raises(cv.Invalid, match="device_class"):
        run(raw, [{"id": "mystery", "as": "electrical_sensor"}])


def test_all_with_air_quality_gases_and_alarms_validates():
    raw = {
        "sensor": [
            sens("co2", "carbon_dioxide", "ppm"),
            sens("co", "carbon_monoxide", "ppm"),
            sens("no2", "nitrogen_dioxide", "µg/m³"),
            sens("vocp", "volatile_organic_compounds_parts", "ppb"),
            sens("aqi", "aqi"),
            sens("pm4", "pm4"),
        ],
        "binary_sensor": [
            ent("smoke", device_class="smoke"),
            ent("cob", device_class="carbon_monoxide"),
        ],
    }
    cfg = run(raw, "all")
    assert count(cfg, "air_quality_sensor") == 1 and count(cfg, "smoke_co_alarm") == 1
    air = endpoint_with(cfg, "co2")["air_quality_sensor"]
    assert {"carbon_dioxide", "carbon_monoxide", "nitrogen_dioxide", "total_voc"} <= set(air)


# --- c3, a6, a8, #2/#8: merge-group collisions spill ---------------------------------------------


def test_two_co2_sensors_under_all_get_separate_endpoints():
    raw = {"sensor": [sens("co2_a", "carbon_dioxide", "ppm"), sens("co2_b", "carbon_dioxide", "ppm")]}
    cfg = run(raw, "all")
    assert count(cfg, "air_quality_sensor") == 2
    assert endpoint_with(cfg, "co2_a") is not endpoint_with(cfg, "co2_b")


def test_two_smoke_detectors_spill():
    raw = {"binary_sensor": [ent("smoke_1", device_class="smoke"), ent("smoke_2", device_class="smoke")]}
    cfg = run(raw, "all")
    assert count(cfg, "smoke_co_alarm") == 2


def test_two_unmatched_same_kind_power_meters_under_all_spill():
    raw = {
        "switch": [ent("sw_a"), ent("sw_b")],
        "sensor": [sens("pa", "power", "W"), sens("pb", "power", "W")],
    }
    cfg = run(raw, "all")
    assert count(cfg, "electrical_sensor") == 2
    for sw in ("sw_a", "sw_b"):
        assert "electrical_sensor" not in endpoint_with(cfg, sw)


def test_as_override_and_unknown_device_type_in_as(caplog):
    raw = {"sensor": [sens("co2_a", "carbon_dioxide", "ppm"), sens("co2_b", "carbon_dioxide", "ppm")]}
    cfg = run(raw, ["co2_a", {"id": "co2_b", "as": "air_quality_sensor"}])
    assert count(cfg, "air_quality_sensor") == 2
    with pytest.raises(cv.Invalid, match="unknown device type 'carbon_dioxide_sensor'"):
        run(raw, [{"id": "co2_b", "as": "carbon_dioxide_sensor"}])


# --- a3, a4, a5, #3, #5: no relation, no attach ---------------------------------------------------


def test_unrelated_sensors_do_not_attach_to_a_lone_lamp_under_all():
    raw = {
        "switch": [ent("lamp", name="Lamp")],
        "sensor": [
            sens("adc_v", "voltage", "V", name="Battery voltage"),
            sens("mains_f", "frequency", "Hz", name="Mains frequency"),
            sens("remote_batt", "battery", "%", name="Remote battery"),
        ],
    }
    cfg = run(raw, "all")
    assert "electrical_sensor" not in endpoint_with(cfg, "lamp")
    assert "battery" not in endpoint_with(cfg, "lamp")
    meter = endpoint_with(cfg, "adc_v")
    assert types(meter) == ["electrical_sensor"]
    assert endpoint_with(cfg, "mains_f") is meter
    assert types(endpoint_with(cfg, "remote_batt")) == ["power_source"]


def test_a_meter_listed_by_id_still_attaches_to_the_only_load():
    raw = {"switch": [ent("lamp")], "sensor": [sens("adc_v", "voltage", "V")]}
    cfg = run(raw, ["lamp", "adc_v"])
    assert "electrical_sensor" in endpoint_with(cfg, "lamp")


def test_name_prefix_needs_a_word_boundary():
    raw = {
        "switch": [ent("relay_1", name="Relay 1"), ent("relay_2", name="Relay 2")],
        "sensor": [
            sens("relay_12_power", "power", "W", name="Relay 12 Power"),
            sens("p_10", "power", "W", name="Relay 10 Power"),
        ],
    }
    cfg = run(raw, "all")
    for sw in ("relay_1", "relay_2"):
        assert "electrical_sensor" not in endpoint_with(cfg, sw)
    assert count(cfg, "electrical_sensor") == 2  # the two power sensors collide: they spill


def test_name_only_relation_with_a_boundary_attaches():
    raw = {
        "switch": [ent("relay_1", name="Relay 1"), ent("relay_2", name="Relay 2")],
        "sensor": [sens("sensor_a", "power", "W", name="Relay 1 Power")],
    }
    cfg = run(raw, "all")
    assert "electrical_sensor" in endpoint_with(cfg, "relay_1")


def test_name_that_only_shares_digits_does_not_attach():
    raw = {
        "switch": [ent("relay_1", name="Relay 1"), ent("relay_2", name="Relay 2")],
        "sensor": [sens("sensor_a", "power", "W", name="Relay 12 Power")],
    }
    cfg = run(raw, "all")
    assert types(endpoint_with(cfg, "sensor_a")) == ["electrical_sensor"]
    assert "electrical_sensor" not in endpoint_with(cfg, "relay_1")


def test_id_match_is_scored_before_name_match():
    raw = {
        "switch": [ent("relay_1", name="Relay 1"), ent("relay_2", name="Relay 2")],
        "sensor": [sens("relay_2_power", "power", "W", name="Relay 1 Power")],
    }
    cfg = run(raw, "all")
    assert "electrical_sensor" in endpoint_with(cfg, "relay_2")


# --- #6, c1: hand-written endpoints are siblings -----------------------------------------------


def test_hand_written_sibling_makes_an_exposed_endpoint_a_duplicate():
    raw = {
        "sensor": [
            sens("t_a", "temperature", "°C", name="Ta"),
            sens("t_b", "temperature", "°C", name="Tb"),
        ]
    }
    cfg = run(raw, ["t_a"], endpoints={9: {"temperature_sensor": {"temperature": "t_b"}}})
    ours = endpoint_with(cfg, "t_a")
    assert ours["tags"][0]["tag"] == 1 and ours["label"] == "Ta"
    from components.matter import conformance

    plans = conformance.plan_endpoints(cfg["endpoints"], log=False)  # no identical-tag error
    assert plans[9].auto_tag.tag == 2


def test_hand_written_tags_are_occupied():
    raw = {"sensor": [sens("t_1", "temperature", "°C"), sens("t_2", "temperature", "°C")]}
    cfg = run(
        raw,
        ["t_1"],
        endpoints={
            9: {
                "temperature_sensor": {"temperature": "t_2"},
                "tags": [{"namespace": "common_number", "tag": 1}],
            }
        },
    )
    assert endpoint_with(cfg, "t_1")["tags"][0]["tag"] == 2  # 1 is taken by endpoint 9


# --- #7, c2: stable numbers ---------------------------------------------------------------------


def test_trailing_numbers_survive_an_unrelated_id():
    raw = {"sensor": [sens(i, "temperature", "°C") for i in ("t_2", "t_3", "t_aux")]}
    cfg = run(raw, ["t_2", "t_3", "t_aux"])
    got = {i: endpoint_with(cfg, i)["tags"][0]["tag"] for i in ("t_2", "t_3", "t_aux")}
    assert got == {"t_2": 2, "t_3": 3, "t_aux": 1}
    cfg = run(raw, ["t_2", "t_3"])  # removing t_aux moves nobody
    assert endpoint_with(cfg, "t_2")["tags"][0]["tag"] == 2


# --- #10, a10: truncation never makes labels identical ----------------------------------------------


def test_truncated_duplicate_labels_stay_distinct(caplog):
    long = "A very long name that overflows the sixteen byte label"
    raw = {
        "binary_sensor": [
            ent("wl1", name=long, device_class="moisture"),
            ent("wl2", name=long + " two", device_class="moisture"),
        ]
    }
    with caplog.at_level("WARNING"):
        cfg = run(raw, "all")
    labels = [endpoint_with(cfg, i)["label"] for i in ("wl1", "wl2")]
    assert len(set(labels)) == 2
    assert all(len(label.encode()) <= 16 for label in labels)
    assert "identical after truncation" in caplog.text


# --- #11: parent by number and by key ------------------------------------------------------------


def test_parent_by_key_and_by_number_agree():
    raw = {
        "climate": [ent("heater", "thermostat")],
        "sensor": [sens("t_a", "temperature", "°C"), sens("t_b", "temperature", "°C")],
    }
    number = endpoint_for_key("heater")
    cfg = run(raw, ["heater", {"id": "t_a", "parent": "heater"}, {"id": "t_b", "parent": number}])
    a, b = endpoint_with(cfg, "t_a"), endpoint_with(cfg, "t_b")
    assert a["parent"] == b["parent"] == number
    assert a["tags"][0]["tag"] != b["tags"][0]["tag"]  # siblings under one parent


# --- #12: label conflict --------------------------------------------------------------------------


def test_label_conflict_in_a_merge_group_raises():
    raw = {"sensor": [sens("c", "carbon_dioxide", "ppm"), sens("p", "pm25", "µg/m³")]}
    with pytest.raises(cv.Invalid, match="conflicting `label:` for group:air_quality: 'A' and 'B'"):
        run(raw, [{"id": "c", "label": "A"}, {"id": "p", "label": "B"}])


def test_tags_conflict_shows_both_values():
    raw = {"sensor": [sens("c", "carbon_dioxide", "ppm"), sens("p", "pm25", "µg/m³")]}
    one = [{"namespace": "common_number", "tag": 1}]
    two = [{"namespace": "common_number", "tag": 2}]
    with pytest.raises(cv.Invalid, match=r"conflicting `tags:` for group:air_quality: .*1.*2"):
        run(raw, [{"id": "c", "tags": one}, {"id": "p", "tags": two}])


# --- #1: errors after the endpoint schema name the item ------------------------------------------------


def test_lowering_error_names_the_entity_and_endpoint():
    raw = {"switch": [ent("sw_a"), ent("sw_b")]}
    with pytest.raises(cv.Invalid) as exc:
        run(raw, [{"id": "sw_a", "features": ["bogus"]}])
    assert "'sw_a'" in exc.value.msg and "lowered to endpoint" in exc.value.msg
    with pytest.raises(cv.Invalid) as exc:
        run(raw, [{"id": "sw_a", "options": {"nope": 1}}])
    assert "'sw_a'" in exc.value.msg and "is not an option" in exc.value.msg
    assert exc.value.path == ["expose"]


# --- #13: a bare matter: stays valid ---------------------------------------------------------------------


def test_bare_matter_is_valid():
    CORE.raw_config = {"switch": [ent("sw_a")]}
    try:
        cfg = {}
        assert lower_expose(cfg) is cfg
    finally:
        CORE.raw_config = None


# --- #9: smoke alarm without a power source warns --------------------------------------------------------


def test_smoke_alarm_without_power_source_warns(caplog):
    raw = {"binary_sensor": [ent("smoke", device_class="smoke")], "sensor": [sens("smoke_batt", "battery", "%")]}
    with caplog.at_level("WARNING"):
        run(raw, ["smoke"])
    assert "Smoke CO Alarm is exposed without a Power Source" in caplog.text
    caplog.clear()
    with caplog.at_level("WARNING"):
        cfg = run(raw, ["smoke", "smoke_batt"])
    assert "without a Power Source" not in caplog.text
    assert idof(endpoint_with(cfg, "smoke")["battery"]) == "smoke_batt"


# --- #14 ----------------------------------------------------------------------------------------------------


def test_garage_cover_under_all_is_skipped_without_closure_compat(monkeypatch):
    from components.matter.expose import inference
    from components.matter.expose.plan import KEY_EXPOSE_PLAN

    monkeypatch.setattr(inference, "closure_compat_supported", lambda: False)
    raw = {"cover": [ent("gar", device_class="garage"), ent("blind", device_class="blind")]}
    cfg = run(raw, "all")
    assert count(cfg, "window_covering") == 1
    plan = CORE.data["matter"][KEY_EXPOSE_PLAN]
    assert "closure_compat" in dict(plan.skipped)["gar"]


def test_no_rule_hint_lists_the_missing_classes():
    raw = {"sensor": [ent("odd", device_class="duration")]}
    CORE.raw_config = raw
    try:
        (entity,) = build_inventory(raw)
        with pytest.raises(NoMapping) as exc:
            infer(entity, Overrides())
    finally:
        CORE.raw_config = None
    assert "reactive_power" in str(exc.value)
    assert "volatile_organic_compounds_parts" in str(exc.value)
