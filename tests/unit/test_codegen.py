"""Codegen smoke tests: `esphome compile --only-generate` on every test config.

Generates main.cpp without invoking a compiler or downloading a toolchain and
checks for the expected Matter registration calls.
"""

from __future__ import annotations

from conftest import CONFIGS, prepare_config, run_esphome
import pytest

pytestmark = pytest.mark.slow

# config stem -> substrings expected in the generated main.cpp
EXPECTED = {
    "esp32-wifi": ["register_endpoint", "register_code_driven_sensor_attribute"],
    "esp32-switch": ["register_switch"],
    "esp32-fan": ["register_fan", "add_fan_features"],
    "esp32-fan-hvac": [
        "register_fan",
        "register_climate_fan",
        "FanFeatureOptions",
        "MatterFilterMapping",
        "hepa_filter_monitoring::feature::condition::add",
        "esp_matter::cluster::thermostat::create",
    ],
    "esp32-cover": ["register_cover", "configure_window_covering"],
    "esp32-binary-sensors": ["register_binary_sensor_attribute"],
    "esp32-all-endpoints": ["register_endpoint"],
    "esp32-ble-commissioning": ["register_endpoint"],
}


@pytest.mark.parametrize("config", sorted(CONFIGS.glob("*.yaml")), ids=lambda p: p.stem)
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
    stems = {p.stem for p in CONFIGS.glob("*.yaml")}
    assert set(EXPECTED) <= stems
