"""Codegen smoke tests: `esphome compile --only-generate` on every test config.

Generates main.cpp without invoking a compiler or downloading a toolchain and
checks for the expected Matter registration calls.
"""

from __future__ import annotations

from conftest import config_files, prepare_config, run_esphome
import pytest

pytestmark = pytest.mark.slow

# config stem -> substrings expected in the generated main.cpp
EXPECTED = {
    "esp32-wifi": ["register_endpoint", "register_code_driven_sensor_attribute"],
    "esp32-switch": ["register_switch"],
    "esp32-fan": ["register_fan", "add_fan_features"],
    "esp32-cover": ["register_cover", "configure_window_covering"],
    "esp32-binary-sensors": ["register_binary_sensor_attribute"],
    "esp32-all-endpoints": ["register_endpoint"],
    "esp32-ble-commissioning": ["register_endpoint"],
    "esp32-power-source": [
        "MatterPowerSourceEndpointList(0, {1, 2, 3})",
        "MatterFlagEnumMapping",
        "create_bat_voltage",
        "create_wired_present",
        "esp_matter::endpoint::add_device_type(endpoint",
        "wired_current_type",
        "MatterFlagEnumMapping(1, 47, 0, 3, -1, {{mains_present, 1}})",
        "MatterFlagEnumMapping(2, 47, 14, 0, -1,",
        "MatterFlagEnumMapping(0, 47, 0, 1, -1, {{mains_present, 2}})",
        "MatterFlagEnumMapping(0, 47, 26, 3, 0,",
    ],
    "esp32-behaviours": [
        "set_status_output",
        "set_status_led_enabled(true, true)",
        "set_pairing(3000, 10000, 300)",
        "MatterRemoveFabricAction",
        "set_fabric_index([]() -> uint8_t {",
    ],
    "esp32-behaviours-light": [
        "set_status_light",
        "set_status_led_enabled(true, false)",
        "set_pairing(2000, 0, 300)",
        "set_fabric_index",
    ],
    "esp32-power-topology": [
        "tree_topology::get_id()",
        "set_topology::get_id() | esp_matter::cluster::power_topology::feature::dynamic_power_flow",
        "power_topology_delegate({11, 12}, {11})",
        "register_endpoint(0,",
    ],
}


@pytest.mark.parametrize("config", config_files(), ids=lambda p: p.stem)
def test_generate(config, tmp_path):
    prepared = prepare_config(config, tmp_path)
    result = run_esphome("compile", "--only-generate", str(prepared), cwd=tmp_path)
    assert result.ok, result.output[-3000:]
    mains = list(tmp_path.glob(".esphome/build/*/src/main.cpp"))
    assert len(mains) == 1, mains
    code = mains[0].read_text()
    assert "matter" in code.lower()
    for needle in EXPECTED.get(config.stem, []):
        assert needle in code, f"{needle!r} missing from generated main.cpp of {config.name}"


def test_all_expected_configs_exist():
    stems = {p.stem for p in config_files()}
    assert set(EXPECTED) <= stems
