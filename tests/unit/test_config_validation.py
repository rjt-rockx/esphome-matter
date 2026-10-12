"""Schema validation of device types and entity bindings via `esphome config`."""

from __future__ import annotations

import textwrap

from conftest import COMPONENTS, CONFIGS, prepare_config, run_esphome
import pytest

SWITCH = """
switch:
  - platform: template
    id: relay
    optimistic: true
    restore_mode: ALWAYS_OFF
"""

LIGHT = """
output:
  - platform: template
    id: out
    type: float
    write_action: []
light:
  - platform: monochromatic
    id: lamp
    output: out
    restore_mode: ALWAYS_OFF
"""


# Snippets are appended to test bodies indented by 8 spaces; keep them aligned.
SWITCH = textwrap.indent(SWITCH, " " * 8)
LIGHT = textwrap.indent(LIGHT, " " * 8)


def bound_code(*names: str) -> bool:
    """True if the component source implements a binding (lock_id, ...)."""
    text = "".join(p.read_text() for p in (COMPONENTS / "matter").glob("*.py"))
    return all(n in text for n in names)


# --------------------------------------------------------------------------
# Every shipped test config validates
# --------------------------------------------------------------------------


@pytest.mark.parametrize("config", sorted(CONFIGS.glob("*.yaml")), ids=lambda p: p.stem)
def test_shipped_configs_validate(config, tmp_path):
    prepared = prepare_config(config, tmp_path)
    result = run_esphome("config", str(prepared), cwd=tmp_path)
    assert result.ok, result.output[-2000:]


# --------------------------------------------------------------------------
# Positive bindings
# --------------------------------------------------------------------------


@pytest.mark.parametrize("device_type", ["on_off_light", "on_off_plug_in_unit", "mounted_on_off_control"])
def test_switch_id_binding(run_config, device_type):
    body = f"""
        matter:
          endpoints:
            1:
              {device_type}:
                switch_id: relay
        """ + SWITCH
    result = run_config(body)
    assert result.ok, result.output


def test_switch_unknown_id(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              on_off_light:
                switch_id: nope
        """
        + SWITCH
    )
    assert not result.ok
    assert "nope" in result.output


def test_switch_restore_mode_warning(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              on_off_light:
                switch_id: relay
        switch:
          - platform: template
            id: relay
            optimistic: true
            restore_mode: ALWAYS_ON
        """
    )
    assert result.ok, result.output
    assert "ALWAYS_OFF" in result.output


def test_light_id_binding(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              dimmable_light:
                light_id: lamp
        """
        + LIGHT
    )
    assert result.ok, result.output


def test_fan_id_binding(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              fan:
                fan_id: f
        output:
          - platform: gpio
            id: relay_out
            pin: GPIO7
        fan:
          - platform: binary
            id: f
            output: relay_out
            restore_mode: ALWAYS_OFF
        """
    )
    assert result.ok, result.output


def test_cover_id_binding(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              window_covering:
                cover_id: shade
                end_product_type: exterior_venetian_blind
        cover:
          - platform: template
            id: shade
            device_class: shade
            optimistic: true
            has_position: true
            position_action: []
            stop_action: []
        """
    )
    assert result.ok, result.output


@pytest.mark.parametrize(
    ("device_class", "inverted_hint"),
    [("door", False), ("window", False), (None, True)],
)
def test_contact_sensor_device_class(run_config, device_class, inverted_hint):
    cls = f"device_class: {device_class}" if device_class else ""
    result = run_config(
        f"""
        matter:
          endpoints:
            1:
              contact_sensor: bs
        binary_sensor:
          - platform: template
            id: bs
            {cls}
        """
    )
    assert result.ok, result.output
    # Without a device_class the user is warned that ON means "closed".
    assert ("has no device_class" in result.output) == inverted_hint


def test_contact_sensor_wrong_device_class_warns(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              contact_sensor: bs
        binary_sensor:
          - platform: template
            id: bs
            device_class: moisture
        """
    )
    assert result.ok, result.output
    assert "doesn't fit" in result.output


def test_occupancy_and_leak_sensors(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              occupancy_sensor: pir
            2:
              water_leak_detector: leak
        binary_sensor:
          - platform: template
            id: pir
            device_class: motion
          - platform: template
            id: leak
            device_class: moisture
        """
    )
    assert result.ok, result.output


# --------------------------------------------------------------------------
# Bindings only present on some branches (skipped until merged)
# --------------------------------------------------------------------------


@pytest.mark.skipif(not bound_code("lock_id"), reason="lock binding not present")
def test_lock_id_binding(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              door_lock:
                lock_id: door
        lock:
          - platform: template
            id: door
            optimistic: true
        """
    )
    assert result.ok, result.output


@pytest.mark.skipif(not bound_code("valve_id"), reason="valve binding not present")
def test_valve_id_binding(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              water_valve:
                valve_id: v
        valve:
          - platform: template
            id: v
            optimistic: true
        """
    )
    assert result.ok, result.output


@pytest.mark.skipif(not bound_code("climate_id"), reason="climate binding not present")
def test_climate_id_binding(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              thermostat:
                climate_id: c
        sensor:
          - platform: template
            id: t
        climate:
          - platform: thermostat
            id: c
            sensor: t
            min_idle_time: 1s
            min_heating_off_time: 1s
            min_heating_run_time: 1s
            heat_action: []
            idle_action: []
        """
    )
    assert result.ok, result.output


# --------------------------------------------------------------------------
# Negative cases
# --------------------------------------------------------------------------


def test_garage_cover_rejected(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              window_covering:
                cover_id: garage
        cover:
          - platform: template
            id: garage
            device_class: garage
            optimistic: true
            open_action: []
            close_action: []
            stop_action: []
        """
    )
    assert not result.ok
    assert "Garage" in result.output or "garage" in result.output


def test_cover_end_product_type_requires_cover_id(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              window_covering:
                end_product_type: exterior_venetian_blind
        """
    )
    assert not result.ok
    assert "end_product_type requires cover_id" in result.output


def test_cover_unknown_end_product_type(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              window_covering:
                cover_id: shade
                end_product_type: teleporter
        cover:
          - platform: template
            id: shade
            optimistic: true
            open_action: []
            close_action: []
            stop_action: []
        """
    )
    assert not result.ok
    assert "teleporter" in result.output


def test_ble_commissioning_true_with_wifi_rejected(run_config):
    result = run_config(
        """
        matter:
          ble_commissioning: true
          endpoints:
            1:
              on_off_light: {}
        """,
        wifi=True,
    )
    assert not result.ok
    assert "ble_commissioning: true cannot be combined" in result.output


def test_ble_commissioning_false_without_network_rejected(run_config):
    result = run_config(
        """
        matter:
          ble_commissioning: false
          endpoints:
            1:
              on_off_light: {}
        """,
        wifi=False,
    )
    assert not result.ok
    assert "ble_commissioning: false requires" in result.output


def test_ble_commissioning_without_network_ok(run_config):
    result = run_config(
        """
        matter:
          ble_commissioning: true
          endpoints:
            1:
              on_off_light: {}
        """,
        wifi=False,
    )
    assert result.ok, result.output


def test_light_id_and_switch_id_rejected(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              on_off_light:
                light_id: lamp
                switch_id: relay
        """
        + SWITCH
        + LIGHT
    )
    assert not result.ok


def test_switch_id_on_unsupported_device_type(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              temperature_sensor:
                switch_id: relay
        """
        + SWITCH
    )
    assert not result.ok


def test_switch_id_must_reference_switch(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              on_off_light:
                switch_id: lamp
        """
        + LIGHT
    )
    assert not result.ok


def test_unknown_device_type_rejected(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              flux_capacitor: {}
        """
    )
    assert not result.ok


@pytest.mark.skipif(not bound_code("generic_switch"), reason="generic switch not present")
def test_generic_switch_bare_defaults_to_momentary(run_config):
    # The mandatory Switch choice feature is resolved to MomentarySwitch.
    result = run_config(
        """
        matter:
          endpoints:
            1:
              generic_switch: {}
        """
    )
    assert result.ok, result.output
