"""Fan Control / filter monitoring / climate bindings (docs/fan.md, docs/hvac.md).

Fast tests call the validators directly; the `run_config` tests go through
`esphome config`, and the generation tests (slow) read the generated main.cpp.
"""

from __future__ import annotations

import textwrap

from conftest import COMPONENTS, HEADER, WIFI, run_esphome, toolchain_line
import esphome.config_validation as cv
import pytest

from components.matter import fan_binding, filter_monitoring
from components.matter.data_model.device_types import DEVICE_TYPES_BY_CONF_KEY

# --------------------------------------------------------------------------
# Shared YAML snippets
# --------------------------------------------------------------------------

ENTITIES = textwrap.dedent(
    """
        output:
          - platform: template
            id: out
            type: float
            write_action: []
        fan:
          - platform: speed
            id: f
            output: out
            speed_count: 4
            restore_mode: ALWAYS_OFF
            preset_modes: [Natural, sleep]
          - platform: speed
            id: plain_fan
            output: out
            speed_count: 3
            restore_mode: ALWAYS_OFF
          - platform: binary
            id: relay_fan
            output: out
            restore_mode: ALWAYS_OFF
        sensor:
          - platform: template
            id: temp
          - platform: template
            id: life
        binary_sensor:
          - platform: template
            id: replace
        button:
          - platform: template
            id: reset
        climate:
          - platform: thermostat
            id: ac
            sensor: temp
            min_idle_time: 1s
            min_cooling_off_time: 1s
            min_cooling_run_time: 1s
            cool_action: []
            idle_action: []
            fan_mode_auto_action: []
            fan_mode_low_action: []
            fan_mode_high_action: []
            min_fan_mode_switching_time: 1s
            swing_both_action: []
            swing_off_action: []
          - platform: thermostat
            id: heater
            sensor: temp
            min_idle_time: 1s
            min_heating_off_time: 1s
            min_heating_run_time: 1s
            heat_action: []
            idle_action: []
        """
)


def endpoint(body: str) -> str:
    """The matter: section for endpoint YAML (`1: ...`)."""
    return "\nmatter:\n  endpoints:\n" + textwrap.indent(textwrap.dedent(body).strip(), "    ") + "\n"


@pytest.fixture
def check(run_config):
    """check(endpoint_yaml) -> Result of `esphome config` with the entities above."""

    def run(body: str):
        return run_config(endpoint(body) + ENTITIES)

    return run


# --------------------------------------------------------------------------
# fan_id and climate_id binding of Fan Control (pure validators)
# --------------------------------------------------------------------------


def normalize(config: dict) -> dict:
    return fan_binding.normalize_endpoint(config, DEVICE_TYPES_BY_CONF_KEY)


def test_fan_id_accepted_on_every_device_type_with_fan_control():
    names = {
        name
        for name, device_type in DEVICE_TYPES_BY_CONF_KEY.items()
        if device_type.has_fan_control_server()
    }
    assert {"fan", "air_purifier", "extractor_hood", "room_air_conditioner"} <= names
    # The Thermostat device type only has Fan Control as a client cluster.
    assert "thermostat" not in names


def test_optional_fan_control_cluster_is_added_for_fan_id():
    config = {"clusters": {}, "room_air_conditioner": {"fan_id": "f", "with_features": []}}
    result = normalize(config)
    assert "fan_control" in result["clusters"]
    assert "fan_control" not in config["clusters"]  # copied, not mutated


def test_required_fan_control_cluster_is_not_duplicated():
    result = normalize({"clusters": {}, "air_purifier": {"fan_id": "f"}})
    assert "fan_control" not in result["clusters"]


def test_two_bindings_rejected():
    with pytest.raises(cv.Invalid, match="Only one Fan Control binding"):
        normalize(
            {
                "clusters": {"fan_control": {"fan_id": "a", "with_features": []}},
                "air_purifier": {"fan_id": "b"},
            }
        )


def test_device_level_climate_id_is_not_a_fan_binding():
    config = {"clusters": {}, "room_air_conditioner": {"climate_id": "c"}}
    assert fan_binding.find_binding(normalize(config), DEVICE_TYPES_BY_CONF_KEY) is None


def test_climate_binding_found():
    config = {"clusters": {"fan_control": {"climate_id": "c", "with_features": ["step"]}}}
    binding = fan_binding.find_binding(normalize(config), DEVICE_TYPES_BY_CONF_KEY)
    assert binding.kind == "climate"
    assert binding.step and not binding.wind


@pytest.mark.parametrize("feature", ["wind", "step"])
def test_wind_and_step_need_a_binding(feature):
    with pytest.raises(cv.Invalid, match="need fan_id"):
        normalize({"clusters": {}, "air_purifier": {"with_features": [feature.capitalize()]}})
    with pytest.raises(cv.Invalid, match="need fan_id"):
        normalize({"clusters": {"fan_control": {"with_features": [feature]}}})


def test_wind_presets_without_fan_id_rejected():
    with pytest.raises(cv.Invalid, match="needs fan_id"):
        normalize({"clusters": {"fan_control": {"wind_presets": {"sleep": "x"}, "with_features": []}}})


def test_wind_presets_imply_the_wind_feature():
    config = {
        "clusters": {},
        "air_purifier": {"fan_id": "f", "wind_presets": {"natural": "Breeze"}, "with_features": []},
    }
    binding = fan_binding.find_binding(normalize(config), DEVICE_TYPES_BY_CONF_KEY)
    assert binding.wind_natural == "Breeze"
    assert binding.wind_sleep == "sleep"  # default name of the other mode


def test_wind_default_preset_names():
    config = {"clusters": {}, "air_purifier": {"fan_id": "f", "with_features": ["Wind"]}}
    binding = fan_binding.find_binding(normalize(config), DEVICE_TYPES_BY_CONF_KEY)
    assert (binding.wind_natural, binding.wind_sleep) == ("natural", "sleep")


def test_climate_derives_fan_features():
    for feature in ("auto", "multi_speed", "rocking", "wind"):
        with pytest.raises(cv.Invalid, match="derived"):
            fan_binding.validate_cluster_options({"climate_id": "c", "with_features": [feature]})


def test_fan_and_climate_exclusive():
    with pytest.raises(cv.Invalid, match="can't be used together"):
        fan_binding.validate_cluster_options({"fan_id": "f", "climate_id": "c"})


def test_trait_features_rejected_with_fan_id_in_cluster_form():
    with pytest.raises(cv.Invalid, match="follow the capabilities"):
        fan_binding.validate_cluster_options({"fan_id": "f", "with_features": ["multi_speed"]})


# --------------------------------------------------------------------------
# Filter monitoring (pure validator)
# --------------------------------------------------------------------------


def filter_conf(**kwargs):
    kwargs.setdefault("with_features", [])
    return filter_monitoring.validate_cluster(kwargs)


def test_filter_condition_implies_features():
    config = filter_conf(condition="s", warning_level=20, critical_level=5)
    assert set(config["with_features"]) == {"condition", "warning"}


def test_filter_unbound_cluster_is_unchanged():
    assert filter_conf() == {"with_features": []}


def test_filter_replace_needed_alone():
    config = filter_conf(replace_needed="b")
    assert config["with_features"] == []


@pytest.mark.parametrize("key", ["warning_level", "critical_level", "degradation_direction"])
def test_filter_levels_need_condition(key):
    value = "up" if key == "degradation_direction" else 10
    with pytest.raises(cv.Invalid, match="condition"):
        filter_conf(replace_needed="b", **{key: value})


def test_filter_stray_options_need_a_binding():
    with pytest.raises(cv.Invalid, match="need a `condition` sensor"):
        filter_conf(reset_button="b")


def test_filter_level_order():
    with pytest.raises(cv.Invalid, match="above"):
        filter_conf(condition="s", warning_level=5, critical_level=10)
    with pytest.raises(cv.Invalid, match="below"):
        filter_conf(condition="s", degradation_direction="up", warning_level=90, critical_level=80)
    filter_conf(condition="s", degradation_direction="up", warning_level=70, critical_level=80)


def test_filter_default_critical_level_orders_against_warning():
    # warning at 5% would be below the default critical level (10%)
    with pytest.raises(cv.Invalid):
        filter_conf(condition="s", warning_level=5)


def test_filter_feature_without_source_rejected():
    with pytest.raises(cv.Invalid, match="Warning feature needs"):
        filter_conf(condition="s", with_features=["warning"])
    with pytest.raises(cv.Invalid, match="Condition feature needs"):
        filter_conf(replace_needed="b", with_features=["condition"])


# --------------------------------------------------------------------------
# esphome config
# --------------------------------------------------------------------------


def test_air_purifier_with_wind_step_and_filters(check):
    result = check(
        """
        1:
          air_purifier:
            fan_id: f
            with_features: [wind, step]
          clusters:
            hepa_filter_monitoring:
              condition: life
              warning_level: 25%
              critical_level: 10%
              replace_needed: replace
              reset_button: reset
              on_reset:
                - logger.log: reset
            activated_carbon_filter_monitoring:
              replace_needed: replace
        """
    )
    assert result.ok, result.output


def test_wind_needs_preset_modes_on_the_fan(check):
    result = check(
        """
        1:
          air_purifier:
            fan_id: plain_fan
            with_features: [wind]
        """
    )
    assert not result.ok
    assert "preset_modes" in result.output


def test_wind_preset_names_must_exist(check):
    result = check(
        """
        1:
          air_purifier:
            fan_id: f
            wind_presets:
              natural: Gust
        """
    )
    assert not result.ok
    assert "Gust" in result.output


def test_step_needs_a_fan_with_speed(check):
    result = check(
        """
        1:
          extractor_hood:
            fan_id: relay_fan
            with_features: [step]
        """
    )
    assert not result.ok
    assert "binary fan" in result.output


def test_step_without_binding_rejected_at_config_time(check):
    result = check(
        """
        1:
          fan: {}
          clusters:
            fan_control:
              with_features: [step]
        """
    )
    assert not result.ok
    assert "fan_id" in result.output


def test_room_ac_fan_modes_from_climate(check):
    result = check(
        """
        1:
          room_air_conditioner:
            climate_id: ac
          clusters:
            fan_control:
              climate_id: ac
              with_features: [step]
        """
    )
    assert result.ok, result.output


def test_room_ac_with_separate_fan(check):
    result = check(
        """
        1:
          room_air_conditioner:
            climate_id: ac
            fan_id: f
        """
    )
    assert result.ok, result.output


def test_thermostat_endpoint_with_fan_control_cluster(check):
    result = check(
        """
        1:
          thermostat:
            climate_id: heater
          clusters:
            fan_control:
              fan_id: plain_fan
        """
    )
    assert result.ok, result.output


def test_thermostat_device_level_fan_id_rejected(check):
    result = check(
        """
        1:
          thermostat:
            climate_id: heater
            fan_id: plain_fan
        """
    )
    assert not result.ok


def test_water_heater_climate_id(check):
    result = check(
        """
        1:
          water_heater:
            climate_id: heater
        """
    )
    assert result.ok, result.output


@pytest.mark.parametrize("feature", ["presets", "setback"])
def test_thermostat_presets_and_setback_rejected_with_climate_id(check, feature):
    result = check(
        f"""
        1:
          thermostat:
            climate_id: heater
            with_features: [{feature}]
        """
    )
    assert not result.ok
    assert "G7" in result.output


def test_heat_pump_climate_id(check):
    result = check(
        """
        1:
          heat_pump:
            climate_id: heater
        """
    )
    assert result.ok, result.output
    assert "Thermostat server on the Heat Pump" in result.output


def test_heat_pump_climate_id_conflicts_with_explicit_thermostat(check):
    result = check(
        """
        1:
          heat_pump:
            climate_id: heater
          clusters:
            thermostat:
              with_features: [heating]
        """
    )
    assert not result.ok
    assert "remove" in result.output


# --------------------------------------------------------------------------
# Generated code
# --------------------------------------------------------------------------


@pytest.fixture
def generate(tmp_path):
    def run(body: str) -> str:
        text = HEADER.format(toolchain=toolchain_line(), components=COMPONENTS) + WIFI
        text += endpoint(body) + ENTITIES
        path = tmp_path / "gen.yaml"
        path.write_text(text)
        result = run_esphome("compile", "--only-generate", str(path), cwd=tmp_path)
        assert result.ok, result.output[-3000:]
        (main,) = tmp_path.glob(".esphome/build/*/src/main.cpp")
        return main.read_text()

    return run


@pytest.mark.slow
def test_generated_fan_wind_step(generate):
    code = generate(
        """
        1:
          air_purifier:
            fan_id: f
            wind_presets:
              sleep: sleep
            with_features: [step]
          clusters:
            hepa_filter_monitoring:
              condition: life
              warning_level: 30%
              reset_button: reset
        """
    )
    assert 'register_fan(f, 1, "natural", "sleep")' in code
    assert 'FanFeatureOptions{true, "natural", "sleep"}' in code
    assert "fan_control.fan_mode_sequence" in code
    assert "hepa_filter_monitoring::feature::condition::add" in code
    assert "hepa_filter_monitoring::feature::warning::add" in code
    assert "hepa_filter_monitoring::command::create_reset_condition" in code
    assert "FilterLevels{0, 30, 10}" in code
    assert "set_reset_button(reset)" in code


@pytest.mark.slow
def test_generated_climate_fan(generate):
    code = generate(
        """
        1:
          room_air_conditioner:
            climate_id: ac
          clusters:
            fan_control:
              climate_id: ac
        """
    )
    assert "register_climate_fan(ac, 1)" in code
    assert "climate_fan_mode_sequence(ac)" in code
    assert "esp_matter::cluster::fan_control::create(endpoint, &fan_control_config" in code
    assert "add_climate_fan_features(fan_control_cluster, ac, false)" in code


@pytest.mark.slow
def test_generated_room_ac_with_fan_entity(generate):
    code = generate(
        """
        1:
          room_air_conditioner:
            climate_id: ac
            fan_id: f
        """
    )
    assert "register_fan(f, 1)" in code
    assert "fan_mode_sequence(f->get_traits(), false)" in code
    assert "add_fan_features(fan_control_cluster, f)" in code


@pytest.mark.slow
def test_generated_heat_pump_and_water_heater(generate):
    code = generate(
        """
        1:
          heat_pump:
            climate_id: heater
        """
    )
    assert "esp_matter::cluster::thermostat::create(endpoint, &thermostat_config" in code
    assert "configure_climate_thermostat(thermostat_config, heater, false)" in code
    assert "register_climate(heater, 1, false)" in code
    assert "extend_climate_thermostat(endpoint, heater, false)" in code


@pytest.mark.slow
def test_generated_water_heater(generate):
    code = generate(
        """
        1:
          water_heater:
            climate_id: heater
        """
    )
    assert "configure_climate_thermostat(device_config_0.thermostat, heater, false)" in code
    assert "extend_climate_thermostat(endpoint, heater, false)" in code
