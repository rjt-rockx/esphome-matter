"""Optional sensor cluster features (components/matter/sensor_extras.py).

Occupancy hold time, Smoke CO Alarm extras, Boolean State Configuration, the Air Quality source and the refrigerator
mode and alarm clusters: schema validation through `esphome config`, the data model changes they rely on, and the
pure Python helpers.
"""

from __future__ import annotations

import textwrap

import pytest
from conftest import (
    CONFIGS,
    prepare_config,
    run_esphome,
)

from components.matter import sensor_extras
from components.matter.data_model.clusters import CLUSTERS_BY_NAME
from components.matter.data_model.conformance import resolve_feature_requirements
from components.matter.data_model.device_types import DEVICE_TYPES_BY_CONF_KEY

SENSORS = textwrap.indent(
    """
binary_sensor:
  - platform: template
    id: motion
    device_class: motion
  - platform: template
    id: leak
    device_class: moisture
  - platform: template
    id: door
    device_class: door
  - platform: template
    id: smoke
    device_class: smoke
  - platform: template
    id: co
    device_class: carbon_monoxide
  - platform: template
    id: fault
  - platform: template
    id: expired
  - platform: template
    id: muted
  - platform: template
    id: testing
  - platform: template
    id: fridge_door
number:
  - platform: template
    id: hold
    optimistic: true
    min_value: 1
    max_value: 300
    step: 1
  - platform: template
    id: level
    optimistic: true
    min_value: 0
    max_value: 2
    step: 1
sensor:
  - platform: template
    id: index
  - platform: template
    id: pm25
    device_class: pm25
    unit_of_measurement: "µg/m³"
  - platform: template
    id: pm25_ppm
    device_class: pm25
    unit_of_measurement: ppm
  - platform: template
    id: co2
    device_class: carbon_dioxide
    unit_of_measurement: ppm
  - platform: template
    id: voc
    device_class: volatile_organic_compounds_parts
    unit_of_measurement: ppb
select:
  - platform: template
    id: mode
    optimistic: true
    options: [Normal, Rapid cool]
""",
    " " * 8,
)


def check(run_config, endpoints: str, *, ok: bool = True, message: str | None = None):
    body = (
        "        matter:\n          endpoints:\n"
        + textwrap.indent(textwrap.dedent(endpoints), " " * 12)
        + SENSORS
    )
    result = run_config(body)
    if ok:
        assert result.ok, result.output
    else:
        assert not result.ok, result.output
    if message is not None:
        assert message in result.output, result.output
    return result


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


def test_cabinet_mode_cluster_is_available():
    cluster = CLUSTERS_BY_NAME["RefrigeratorAndTemperatureControlledCabinetMode"]
    assert cluster.id == 0x52
    assert cluster.espm_namespace == "refrigerator_and_tcc_mode"
    assert cluster.get_attribute("CurrentMode") is not None
    assert cluster.get_command("ChangeToMode") is not None
    for name in ("refrigerator", "temperature_controlled_cabinet"):
        device_type = DEVICE_TYPES_BY_CONF_KEY[name]
        assert any(c.id == 0x52 for c in device_type.server_clusters)
        assert not any(c.id == 0x52 and c.required for c in device_type.server_clusters)


@pytest.mark.parametrize(
    "name",
    [
        "contact_sensor",
        "occupancy_sensor",
        "water_leak_detector",
        "water_freeze_detector",
        "rain_sensor",
    ],
)
def test_boolean_state_configuration_is_optional(name):
    device_type = DEVICE_TYPES_BY_CONF_KEY[name]
    cluster = next(c for c in device_type.server_clusters if c.id == 0x80)
    assert not cluster.required


def test_conformance_or_term_with_both_alternatives_enabled():
    cluster = CLUSTERS_BY_NAME["BooleanStateConfiguration"]
    conformance = {f.code: f.conformance for f in cluster.features}
    # AlarmSuppress needs "VIS or AUD". With both enabled nothing has to be added or chosen.
    assert (
        resolve_feature_requirements([], conformance, {"VIS", "AUD", "SPRS"}) == set()
    )
    assert resolve_feature_requirements([], conformance, {"AUD", "SPRS"}) == set()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def test_hold_time_defaults():
    hold = sensor_extras.HOLD_TIME_SCHEMA("20s")
    assert (hold["min"], hold["default"], hold["max"]) == (1, 20, 300)
    assert hold["legacy_attributes"] is True
    long = sensor_extras.HOLD_TIME_SCHEMA("10min")
    assert (long["default"], long["max"]) == (600, 600)


def test_hold_time_maximum_follows_a_larger_default():
    assert sensor_extras.HOLD_TIME_SCHEMA({"default": "400s"})["max"] == 400


@pytest.mark.parametrize(
    "config",
    [
        {"default": "10s", "min": "20s"},
        {"default": "20s", "max": "15s"},
        {"default": "2s", "max": "5s"},
        {"default": "0s"},
    ],
)
def test_hold_time_rejects_inconsistent_limits(config):
    import esphome.config_validation as cv

    with pytest.raises(cv.Invalid):
        sensor_extras.HOLD_TIME_SCHEMA(config)


def test_air_quality_presets_are_strict_upper_bounds():
    # A level applies strictly above its threshold, the top of the previous category.
    unit, pm25 = sensor_extras.AIR_QUALITY_PRESETS["pm25"]
    assert unit == "ug/m3"
    assert list(pm25.values()) == [9.0, 35.4, 55.4, 125.4, 225.4]


def test_air_quality_thresholds_must_increase():
    import esphome.config_validation as cv

    with pytest.raises(cv.Invalid, match="must increase"):
        sensor_extras._validate_thresholds({"fair": 20, "moderate": 10})
    assert sensor_extras._validate_thresholds({"fair": 10, "poor": 20})


def test_air_quality_presets_cover_every_level():
    for _, thresholds in sensor_extras.AIR_QUALITY_PRESETS.values():
        assert list(thresholds) == list(sensor_extras.AIR_QUALITY_LEVELS)
        values = list(thresholds.values())
        assert values == sorted(values)


# --------------------------------------------------------------------------
# Occupancy hold time
# --------------------------------------------------------------------------


def test_hold_time_long_form(run_config):
    check(
        run_config,
        """
        1:
          occupancy_sensor:
            occupancy: motion
            hold_time:
              default: 45s
              min: 5s
              max: 10min
              number: hold
        """,
    )


def test_hold_time_short_form(run_config):
    check(
        run_config,
        """
        1:
          occupancy_sensor:
            occupancy: motion
            hold_time: 30s
        """,
    )


def test_hold_time_invalid_limits(run_config):
    check(
        run_config,
        """
        1:
          occupancy_sensor:
            occupancy: motion
            hold_time:
              default: 5s
              min: 10s
        """,
        ok=False,
        message="min <= default <= max",
    )


def test_hold_time_max_too_small(run_config):
    check(
        run_config,
        """
        1:
          occupancy_sensor:
            occupancy: motion
            hold_time:
              default: 2s
              max: 5s
        """,
        ok=False,
        message="HoldTimeMax >= 10 seconds",
    )


def test_hold_time_number_range_warning(run_config):
    result = check(
        run_config,
        """
        1:
          occupancy_sensor:
            occupancy: motion
            hold_time:
              default: 30s
              max: 10min
              number: hold
        """,
    )
    assert "max_value 300" in result.output


def test_hold_time_only_on_occupancy_sensor(run_config):
    check(
        run_config,
        """
        1:
          contact_sensor:
            boolean_state: door
            hold_time: 30s
        """,
        ok=False,
        message="hold_time",
    )


# --------------------------------------------------------------------------
# Smoke CO Alarm
# --------------------------------------------------------------------------


def test_smoke_co_bindings(run_config):
    check(
        run_config,
        """
        1:
          smoke_co_alarm:
            smoke_state: smoke
            co_state: co
            hardware_fault: fault
            end_of_service: expired
            device_muted: muted
            test_in_progress: testing
            on_self_test:
              - logger.log: self test
        """,
    )


def test_smoke_co_self_test_duration(run_config):
    check(
        run_config,
        """
        1:
          smoke_co_alarm:
            smoke_state: smoke
            self_test_duration: 12s
        """,
    )


def test_smoke_co_needs_an_alarm_type(run_config):
    check(
        run_config,
        """
        1:
          smoke_co_alarm:
            hardware_fault: fault
        """,
        ok=False,
        message="needs a smoke_state or co_state",
    )


def test_smoke_co_inverted_per_attribute(run_config):
    check(
        run_config,
        """
        1:
          smoke_co_alarm:
            smoke_state: smoke
            hardware_fault: fault
            hardware_fault_inverted: true
        """,
    )


# --------------------------------------------------------------------------
# Boolean State Configuration
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "device",
    ["water_leak_detector", "water_freeze_detector", "rain_sensor", "contact_sensor"],
)
def test_alarms_on_boolean_state_devices(run_config, device):
    check(
        run_config,
        f"""
        1:
          {device}:
            boolean_state: leak
            alarms:
              visual: true
              audible: true
              suppress: true
        """,
    )


def test_alarms_and_sensitivity_on_occupancy_sensor(run_config):
    check(
        run_config,
        """
        1:
          occupancy_sensor:
            occupancy: motion
            alarms:
              audible: true
            sensitivity:
              levels: 4
              default: 2
              number: level
        """,
    )


def test_sensitivity_only(run_config):
    check(
        run_config,
        """
        1:
          rain_sensor:
            boolean_state: leak
            sensitivity:
              levels: 3
        """,
    )


def test_alarms_active_override_and_trigger(run_config):
    check(
        run_config,
        """
        1:
          water_leak_detector:
            boolean_state: leak
            alarms:
              visual: true
              active: fault
              on_active:
                - logger.log: changed
        """,
    )


def test_alarms_need_visual_or_audible(run_config):
    check(
        run_config,
        """
        1:
          water_leak_detector:
            boolean_state: leak
            alarms:
              suppress: true
        """,
        ok=False,
        message="at least one of 'visual' or 'audible'",
    )


def test_sensitivity_default_out_of_range(run_config):
    check(
        run_config,
        """
        1:
          water_leak_detector:
            boolean_state: leak
            sensitivity:
              levels: 3
              default: 3
        """,
        ok=False,
        message="default must be a level index below levels",
    )


def test_sensitivity_levels_range(run_config):
    check(
        run_config,
        """
        1:
          water_leak_detector:
            boolean_state: leak
            sensitivity:
              levels: 1
        """,
        ok=False,
    )


def test_sensitivity_number_range_warning(run_config):
    result = check(
        run_config,
        """
        1:
          water_leak_detector:
            boolean_state: leak
            sensitivity:
              levels: 5
              number: level
        """,
    )
    assert "max_value 2" in result.output


def test_alarms_rejected_without_the_cluster(run_config):
    check(
        run_config,
        """
        1:
          smoke_co_alarm:
            smoke_state: smoke
            alarms:
              visual: true
        """,
        ok=False,
        message="alarms",
    )


def test_with_features_alone_points_to_the_alarms_options(run_config):
    # with_features of an optional cluster the device type does not require is an error with the way out.
    check(
        run_config,
        """
        1:
          water_leak_detector:
            boolean_state: leak
            with_features: [Visual, Audible, SensitivityLevel]
        """,
        ok=False,
        message="clusters:",
    )


# --------------------------------------------------------------------------
# Air Quality
# --------------------------------------------------------------------------


def test_air_quality_index_sensor(run_config):
    check(
        run_config,
        """
        1:
          air_quality_sensor:
            air_quality: index
        """,
    )


def test_air_quality_derived_default_thresholds(run_config):
    check(
        run_config,
        """
        1:
          air_quality_sensor:
            air_quality:
              sensor: pm25
            pm_2_5: pm25
        """,
    )


def test_air_quality_derived_explicit_thresholds(run_config):
    check(
        run_config,
        """
        1:
          air_quality_sensor:
            air_quality:
              sensor: voc
              thresholds:
                fair: 220
                poor: 660
        """,
    )


def test_air_quality_needs_thresholds_without_preset(run_config):
    check(
        run_config,
        """
        1:
          air_quality_sensor:
            air_quality:
              sensor: voc
        """,
        ok=False,
        message="has no default air quality thresholds",
    )


def test_air_quality_thresholds_order(run_config):
    check(
        run_config,
        """
        1:
          air_quality_sensor:
            air_quality:
              sensor: voc
              thresholds:
                fair: 600
                moderate: 300
        """,
        ok=False,
        message="must increase",
    )


def test_air_quality_unknown_threshold_level(run_config):
    check(
        run_config,
        """
        1:
          air_quality_sensor:
            air_quality:
              sensor: voc
              thresholds:
                excellent: 10
        """,
        ok=False,
    )


def test_air_quality_index_with_unit_warns(run_config):
    result = check(
        run_config,
        """
        1:
          air_quality_sensor:
            air_quality: pm25
        """,
    )
    assert "expects a 0-6 index" in result.output


# --------------------------------------------------------------------------
# Refrigerator
# --------------------------------------------------------------------------


def test_refrigerator_door_and_delegate_modes(run_config):
    check(
        run_config,
        """
        1:
          refrigerator:
            door_open: fridge_door
          clusters:
            refrigerator_and_temperature_controlled_cabinet_mode:
              modes:
                - {label: Normal, tags: [auto]}
                - {label: Rapid cool, tags: [rapid_cool]}
              select: mode
        """,
    )


def test_door_open_only_on_refrigerator(run_config):
    check(
        run_config,
        """
        1:
          temperature_controlled_cabinet:
            door_open: fridge_door
        """,
        ok=False,
        message="door_open",
    )


# --------------------------------------------------------------------------
# The shipped config generates the expected code
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_sensor_extras_codegen(tmp_path):
    prepared = prepare_config(CONFIGS / "esp32-sensor-extras.yaml", tmp_path)
    result = run_esphome("compile", "--only-generate", str(prepared), cwd=tmp_path)
    assert result.ok, result.output[-3000:]
    mains = list(tmp_path.glob(".esphome/build/*/src/main.cpp"))
    assert len(mains) == 1
    code = mains[0].read_text()
    for needle in [
        "MatterOccupancyHoldTimeMapping(1, 5, 600, 45)",
        "MatterOccupancyHoldTimeMapping(2, 1, 300, 20)",
        "attribute::create_hold_time(",
        "attribute::create_hold_time_limits(",
        "create_pir_occupied_to_unoccupied_delay",
        "MatterBooleanStateConfigMapping(3)",
        "feature::visual::config_t",
        "alarms_supported = 3;",
        "feature::sensitivity_level::config_t",
        "supported_sensitivity_levels = 3;",
        "create_alarms_enabled",
        "create_default_sensitivity_level",
        "set_alarm_source(",
        "add_alarms_suppressed_trigger",
        "create_self_test_request",
        "create_alarm_muted",
        "MatterSmokeCoAlarmMapping(6, 8000)",
        "MatterSmokeCoAlarmMapping(7, 0)",
        "update_hardware_fault_attribute",
        "update_end_of_service_attribute",
        "update_device_muted_attribute",
        "update_test_in_progress_attribute",
        "conversion::air_quality_index",
        "update_air_quality_attribute",
        "MatterAirQualityThreshold>{{2, 9.0f}, {3, 35.4f}",
        "MatterAirQualityThreshold>{{2, 220.0f}",
        "refrigerator_alarm_config.mask = 1;",
        "refrigerator_alarm_config.supported = 1;",
        "refrigerator_alarm::create(",
        "esp_matter::event::create(refrigerator_alarm_cluster, 0)",
        "MatterRefrigeratorAlarmMapping(11, fridge_door)",
    ]:
        assert needle in code, f"{needle!r} missing from the generated main.cpp"
    # The generic extra cluster path must not be used for clusters without feature_flags.
    assert "boolean_state_configuration_config.feature_flags" not in code
