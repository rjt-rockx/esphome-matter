"""Tests for the FINAL_VALIDATE re-check, `expose: all`, the plan output and the plan table."""

from __future__ import annotations

import logging

from conftest import CONFIGS, prepare_config, run_esphome
from esphome.core import CORE
import pytest

from components.matter.expose.final_check import check_expose, check_member
from components.matter.expose.inference import LoweredEndpoint, Overrides, infer
from components.matter.expose.inventory import Entity, make_entity
from components.matter.expose.lower import lower_expose
from components.matter.expose.plan import (
    ExposePlan,
    Member,
    PlannedEndpoint,
    ecosystem_warnings,
    endpoint_count_warning,
    format_plan,
)
from components.matter.expose.plan_table import (
    flash_estimate,
    plan_rows,
    plan_table_code,
)
from components.matter.expose.schema import EXPOSE_SCHEMA


def raw_entity(domain, platform="template", id_="x", **node):
    node = {"platform": platform, "id": id_, "name": id_.upper(), **node}
    return make_entity(domain, platform, node, None, [domain, 0])


def member(raw: Entity, overrides: Overrides | None = None, explicit=True) -> Member:
    o = overrides or Overrides()
    return Member(raw, o, infer(raw, o), explicit)


def validated(raw: Entity, **changes) -> Entity:
    """The validated version of a raw entity: same entity with platform defaults applied."""
    return Entity(**{**raw.__dict__, **changes})


def temp_sensor(**node):
    return raw_entity("sensor", id_="t", device_class="temperature", **node)


# --- re-check warnings -----------------------------------------------------------------------


def test_clean_entity_has_no_warnings():
    raw = raw_entity("sensor", id_="t", device_class="temperature", unit_of_measurement="°C")
    assert check_member(member(raw), validated(raw)) == []


def test_now_internal_warns_with_fix():
    raw = raw_entity("switch", id_="relay")
    (text,) = check_member(member(raw, explicit=False), validated(raw, internal=True))
    assert "'relay' is internal: true after validation" in text
    assert "give it a `name:` or add `exclude: [relay]`" in text
    (explicit,) = check_member(member(raw), validated(raw, internal=True))
    assert "remove 'relay' from `expose:`" in explicit


@pytest.mark.parametrize("category", ["diagnostic", "config"])
def test_entity_category_from_platform_default_warns(category):
    raw = temp_sensor(unit_of_measurement="°C")
    (text,) = check_member(member(raw), validated(raw, entity_category=category))
    assert f"entity_category: {category} after validation" in text
    assert "remove 't' from `expose:`" in text


def test_entity_category_already_known_at_schema_time_is_not_repeated():
    raw = temp_sensor(entity_category="diagnostic", unit_of_measurement="°C")
    assert check_member(member(raw), validated(raw)) == []


def test_platform_default_device_class_changes_the_device_type():
    raw = raw_entity("sensor", platform="template", id_="s", unit_of_measurement="°C")
    mem = member(raw)
    assert mem.lowered.primary == "temperature_sensor"  # inferred from the unit
    (text,) = check_member(mem, validated(raw, device_class="humidity"))
    assert "inferred as temperature_sensor" in text and "would now infer humidity_sensor" in text
    assert "Add `as: temperature_sensor` to keep it" in text


def test_device_class_change_to_unmappable_warns():
    raw = raw_entity("sensor", platform="template", id_="s", unit_of_measurement="°C")
    texts = check_member(member(raw), validated(raw, device_class="duration", unit="s"))
    assert any("inferred as temperature_sensor" in t and "no longer maps" in t for t in texts)


def test_explicit_as_is_never_second_guessed():
    raw = raw_entity("sensor", id_="s", device_class="temperature", unit_of_measurement="°C")
    mem = member(raw, Overrides(as_="temperature_sensor"))
    assert check_member(mem, validated(raw, device_class="humidity")) == []


def test_light_color_keys_vs_device_type():
    raw = raw_entity("light", platform="partition", id_="strip")
    mem = Member(
        raw,
        Overrides(as_="on_off_light"),
        LoweredEndpoint({"on_off_light": {"light_id": "strip"}}),
    )
    full = validated(raw, traits=frozenset({"red", "green", "blue"}))
    (text,) = check_member(mem, full)
    assert "color keys for extended_color_light but is exposed as on_off_light" in text
    assert "`as: extended_color_light`" in text
    # the right type is silent
    ok = Member(
        raw,
        Overrides(as_="extended_color_light"),
        LoweredEndpoint({"extended_color_light": {"light_id": "strip"}}),
    )
    assert check_member(ok, full) == []


def test_unit_missing_and_mismatched():
    raw = temp_sensor()
    mem = member(raw)
    (missing,) = check_member(mem, validated(raw, unit=None))
    assert "has no unit_of_measurement" in missing
    (wrong,) = check_member(mem, validated(raw, unit="W"))
    assert "unit 'W' is not one of" in wrong


def test_unit_changed_during_validation():
    raw = temp_sensor(unit_of_measurement="K")
    (text,) = check_member(member(raw), validated(raw, unit="°C"))
    assert "unit changed from 'K' to '°C'" in text


def test_check_expose_is_read_only_and_logs(caplog):
    class FakeFull:
        def __init__(self, conf):
            self.conf = conf

        def get_path_for_id(self, id_):
            return ["sensor", 0, "id"]

        def get_config_for_path(self, path):
            return self.conf

    raw = temp_sensor(unit_of_measurement="°C")
    plan = ExposePlan([PlannedEndpoint("t", {"temperature_sensor": {}}, members=[member(raw)])], [])
    conf = {"platform": "template", "id": "t", "name": "T", "device_class": "temperature",
            "unit_of_measurement": "°C", "entity_category": "diagnostic"}
    before = dict(conf)
    CORE.data["matter"] = {"expose_plan": plan}
    try:
        with caplog.at_level(logging.WARNING):
            found = check_expose({}, FakeFull(conf))
    finally:
        CORE.data.pop("matter", None)
    assert len(found) == 1 and found[0] in caplog.text
    assert conf == before  # never mutated


def test_check_expose_without_plan_is_silent():
    CORE.data.pop("matter", None)
    assert check_expose({}, object()) == []


# --- expose: all, endpoint count, ecosystem --------------------------------------------------


def sw(id_, name=None, **kw):
    node = {"platform": "template", "id": id_, **kw}
    if name != "":
        node["name"] = name or id_
    return node


def lower_all(raw, expose="all", **extra):
    CORE.raw_config = raw
    try:
        return lower_expose({"expose": EXPOSE_SCHEMA(expose), "endpoints": {}, **extra})
    finally:
        CORE.raw_config = None


def test_all_skips_nameless_internal_diagnostic_and_platform_default_diagnostic():
    raw = {
        "switch": [
            sw("ok"),
            sw("nameless", name=""),
            sw("hidden", internal=True),
            sw("cfg", entity_category="config"),
        ],
        "sensor": [
            {"platform": "internal_temperature", "id": "chip", "name": "Chip"},
            {"platform": "template", "id": "diag", "name": "D", "device_class": "temperature",
             "entity_category": "diagnostic"},
        ],
        "button": [{"platform": "template", "id": "b", "name": "B"}],
    }
    cfg = lower_all(raw)
    plan = CORE.data["matter"]["expose_plan"]
    assert [ep.entities for ep in plan.endpoints] == [["ok"]]
    assert len(cfg["endpoints"]) == 1
    reasons = dict(plan.skipped)
    assert reasons["nameless"] == "no name (internal)"
    assert reasons["hidden"] == "internal: true"
    assert reasons["cfg"] == "entity_category: config"
    assert reasons["chip"] == "entity_category: diagnostic (platform default)"
    assert reasons["diag"] == "entity_category: diagnostic"
    assert "no faithful Matter device type" in reasons["b"]


def test_more_than_16_endpoints_warns_with_heap_note(caplog):
    raw = {"switch": [sw(f"sw_{i}") for i in range(17)]}
    with caplog.at_level(logging.WARNING):
        lower_all(raw)
    text = caplog.text
    assert "17 endpoints is above 16" in text
    assert "heap" in text and "KB" in text
    assert "CONFIG_ESP_MATTER_MAX_DYNAMIC_ENDPOINT_COUNT" in text


def test_16_endpoints_do_not_warn(caplog):
    raw = {"switch": [sw(f"sw_{i}") for i in range(16)]}
    with caplog.at_level(logging.WARNING):
        lower_all(raw)
    assert "endpoints is above" not in caplog.text
    assert endpoint_count_warning(16) is None and endpoint_count_warning(17)


def test_hand_written_endpoints_count_towards_the_limit(caplog):
    raw = {"switch": [sw(f"sw_{i}") for i in range(10)]}
    pins = {1000 + i: {} for i in range(7)}
    with caplog.at_level(logging.WARNING):
        lower_all(raw, endpoints=pins)
    assert "17 endpoints is above 16" in caplog.text


def planned(key, device_type, number, **kw):
    return PlannedEndpoint(key, {device_type: {}}, entities=[key], number=number, **kw)


def test_ecosystem_warnings_for_valve_soil_and_test_vendor():
    plan = ExposePlan(
        [planned("tap", "water_valve", 5), planned("soil", "soil_sensor", 6)], [], test_vendor=True
    )
    found = ecosystem_warnings(plan)
    assert "tap: Apple Home does not show water valves" in found
    assert "soil: Apple Home rejects soil sensors" in found
    assert any("0xFFF1" in f and "Google Developer Console" in f for f in found)
    own = ExposePlan([planned("tap", "water_valve", 5)], [], test_vendor=False)
    assert not any("Google" in f for f in ecosystem_warnings(own))


def test_plan_text_lists_ecosystem_and_heap():
    plan = ExposePlan([planned("tap", "water_valve", 5)], [("x", "excluded")])
    text = format_plan(plan.endpoints, plan.skipped, [], plan)
    assert "skipped x: excluded" in text
    assert "ecosystem: tap: Apple Home does not show water valves" in text
    assert "heap: about 0.4 KB for 1 endpoints" in text


# --- plan table ------------------------------------------------------------------------------


def test_plan_table_rows_and_code():
    plan = ExposePlan(
        [
            planned("b", "on_off_light", 20, label='Say "hi"'),
            PlannedEndpoint(
                "a", {"temperature_sensor": {}}, entities=["a"], attached=["bat"], number=10
            ),
        ],
        [],
    )
    rows = plan_rows(plan)
    assert rows == [
        (10, "temperature_sensor", "a,+bat", ""),
        (20, "on_off_light", "b", 'Say "hi"'),
    ]
    code = plan_table_code(rows)
    assert code.startswith("static constexpr esphome::matter::PlanRow MATTER_PLAN[] = {")
    assert '{20, "on_off_light", "b", "Say \\"hi\\""}' in code
    assert flash_estimate([]) == 0
    assert flash_estimate(rows) > flash_estimate(rows[:1]) > 0


# --- esphome config on tests/configs/esp32-expose-all.yaml -----------------------------------


@pytest.fixture(scope="module")
def expose_all_output(tmp_path_factory):
    directory = tmp_path_factory.mktemp("expose_all")
    config = prepare_config(CONFIGS / "esp32-expose-all.yaml", directory)
    result = run_esphome("config", str(config), cwd=directory)
    assert result.ok, result.output[-3000:]
    return result.output


def plan_lines(output: str) -> list[str]:
    text = output.split("matter.expose plan:\n")[1].split("INFO Configuration is valid")[0]
    return text.strip().splitlines()


def test_expose_all_config_validates_and_prints_the_plan(expose_all_output):
    lines = plan_lines(expose_all_output)
    first_skip = next(i for i, line in enumerate(lines) if line.startswith("skipped"))
    table = [line.split() for line in lines[:first_skip]]
    assert table[0][:2] == ["endpoint", "key"]
    assert [(r[0], r[1], r[2], r[3]) for r in table[1:]] == [
        ("33996", "lamp", "dimmable_light", "lamp"),
        ("38775", "relay", "on_off_plug_in_unit", "relay"),
        ("61934", "room_temp", "temperature_sensor", "room_temp"),
    ]
    skipped = [line for line in lines if line.startswith("skipped")]
    assert skipped == [
        "skipped <no id: switch.5>: no explicit `id:`",
        "skipped skip_me: excluded",
        "skipped hidden_relay: internal: true",
        "skipped nameless_relay: no name (internal)",
        "skipped cfg_relay: entity_category: config",
        "skipped some_button: a button has no events; list its id in `expose:` to get an unbound "
        "Generic Switch that `on_press:` drives with matter.send_switch_event",
        "skipped diag_temp: entity_category: diagnostic",
        "skipped chip_temp: entity_category: diagnostic (platform default)",
        "skipped nameless_sensor: no name (internal)",
    ]
    assert lines[-2].startswith("ecosystem: Google Home needs a Google Developer Console project")
    assert lines[-1] == "heap: about 1.3 KB for 3 endpoints (rough estimate, unmeasured)"


def test_expose_all_has_no_final_validate_warnings(expose_all_output):
    assert "after validation" not in expose_all_output


def test_internal_temperature_explicit_warns_once(run_config):
    result = run_config(
        """
        matter:
          expose: [chip]
        sensor:
          - platform: internal_temperature
            id: chip
            name: Chip
        """
    )
    assert result.ok, result.output
    assert result.output.count("is exposed explicitly although") == 1
    assert "after validation" not in result.output


def test_nameless_explicit_entity_warns_at_schema_time(run_config):
    result = run_config(
        """
        matter:
          expose: [relay]
        switch:
          - {platform: template, id: relay, optimistic: true, restore_mode: ALWAYS_OFF}
        """
    )
    assert result.ok, result.output
    assert "no name (internal)" in result.output


def test_unit_missing_on_exposed_temperature_warns_in_final_validate(run_config):
    result = run_config(
        """
        matter:
          expose: [{id: t, as: temperature_sensor}]
        sensor:
          - platform: template
            id: t
            name: T
            lambda: return 1.0;
        """
    )
    assert result.ok, result.output
    assert "has no unit_of_measurement" in result.output


# --- generated plan table (slow: runs code generation) ---------------------------------------


def generate(tmp_path, logger_level: str | None):
    config = prepare_config(CONFIGS / "esp32-expose-all.yaml", tmp_path)
    if logger_level:
        config.write_text(
            config.read_text().replace("logger:\n", f"logger:\n  level: {logger_level}\n", 1)
        )
    result = run_esphome("compile", "--only-generate", str(config), cwd=tmp_path)
    assert result.ok, result.output[-3000:]
    (main,) = tmp_path.glob(".esphome/build/*/src/main.cpp")
    defines = next(tmp_path.glob(".esphome/build/*/src/esphome/core/defines.h"))
    return main.read_text(), defines.read_text()


@pytest.mark.slow
def test_plan_table_is_generated_when_the_logger_shows_config(tmp_path):
    main, defines = generate(tmp_path, None)
    assert "#define USE_MATTER_PLAN" in defines
    assert "static constexpr esphome::matter::PlanRow MATTER_PLAN[] = {" in main
    assert '{33996, "dimmable_light", "lamp", ""}' in main
    assert '{38775, "on_off_plug_in_unit", "relay", ""}' in main
    assert '{61934, "temperature_sensor", "room_temp", ""}' in main
    assert "esphome::matter::set_plan(MATTER_PLAN" in main


@pytest.mark.slow
def test_plan_table_costs_nothing_when_the_logger_hides_config(tmp_path):
    main, defines = generate(tmp_path, "INFO")
    assert "USE_MATTER_PLAN" not in defines
    assert "MATTER_PLAN" not in main
