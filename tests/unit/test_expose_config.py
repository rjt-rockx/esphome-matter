"""End-to-end tests of `matter: expose:` through `esphome config` and code generation."""

from __future__ import annotations

import re
import textwrap

from conftest import COMPONENTS, HEADER, WIFI, run_esphome, toolchain_line
import pytest

ENTITIES = """
switch:
  - {platform: template, id: relay, name: Relay, optimistic: true, restore_mode: ALWAYS_OFF}
output:
  - {platform: template, id: out, type: float, write_action: []}
light:
  - {platform: monochromatic, id: lamp, name: Lamp, output: out, restore_mode: ALWAYS_OFF}
sensor:
  - platform: template
    id: room_temp
    name: Room
    unit_of_measurement: "°C"
    device_class: temperature
    lambda: return 21.0;
"""


def test_bare_matter_is_an_error(run_config):
    result = run_config("matter:\n" + ENTITIES)
    assert not result.ok
    assert "matter: nothing to expose. Add one of:" in result.output
    assert "expose: [relay, lamp, room_temp]" in result.output
    assert "expose: all" in result.output
    assert "expose: []" in result.output


def test_expose_empty_is_root_only(run_config):
    assert run_config("matter:\n  expose: []\n").ok


def test_unknown_id_suggests(run_config):
    result = run_config("matter:\n  expose: [rely]\n" + ENTITIES)
    assert not result.ok
    assert "no entity with id 'rely'" in result.output and "'relay'" in result.output


def test_collision_is_an_error(run_config):
    body = """
    matter:
      expose: [e84, e276]
    switch:
      - {platform: template, id: e84, name: A, optimistic: true}
      - {platform: template, id: e276, name: B, optimistic: true}
    """
    result = run_config(body)
    assert not result.ok
    assert "endpoint 47922 collides: e84 and e276" in result.output
    assert "e84 could use 6810 (free)" in result.output


def test_wrong_pairing_reports_stock_use_id_error(run_config):
    result = run_config(
        "matter:\n  expose: [{id: relay, as: humidity_sensor}]\n" + ENTITIES
    )
    assert not result.ok
    assert "doesn't inherit from sensor::Sensor" in result.output


def test_lowered_endpoints_are_printed(run_config):
    result = run_config("matter:\n  expose: [relay, lamp, room_temp]\n" + ENTITIES)
    assert result.ok, result.output
    for number in (38775, 33996, 61934):
        assert f"{number}:" in result.output


@pytest.mark.slow
def test_expose_codegen_equals_handwritten(tmp_path):
    def generate(name: str, matter: str) -> str:
        directory = tmp_path / name
        directory.mkdir()
        text = HEADER.format(toolchain=toolchain_line(), components=COMPONENTS) + WIFI
        path = directory / f"{name}.yaml"
        path.write_text(text + matter + ENTITIES)
        result = run_esphome("compile", "--only-generate", str(path), cwd=directory)
        assert result.ok, result.output[-3000:]
        (main,) = directory.glob(".esphome/build/*/src/main.cpp")
        return matter_codegen(main.read_text())

    exposed = generate("exposed", "matter:\n  expose: [relay, lamp, room_temp]\n")
    by_hand = generate(
        "hand",
        textwrap.dedent(
            """\
            matter:
              endpoints:
                33996: {dimmable_light: {light_id: lamp}}
                38775: {on_off_plug_in_unit: {switch_id: relay}}
                61934: {temperature_sensor: room_temp}
            """
        ),
    )
    assert "register_endpoint(38775" in exposed
    assert exposed == by_hand


def matter_codegen(code: str) -> str:
    """main.cpp without the config echo comment block and #line pragmas."""
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    return "\n".join(
        line for line in code.splitlines() if not line.lstrip().startswith(("#line", "//"))
    )
