"""Matter ICD (`matter: icd:`): schema, presets, sdkconfig mapping and the final validation.

EXPERIMENTAL feature, see docs/icd.md. The schema/sdkconfig tests call the validators directly;
the final-validation tests run `esphome config` on small generated YAML files.
"""

from __future__ import annotations

import textwrap

from conftest import run_esphome, toolchain_line
import esphome.config_validation as cv
import pytest

from components.matter import icd

THREAD = """
openthread:
  device_type: MTD
  poll_period: 15s
  network_key: 0xdfd34f0f05cad978ec4e32b0413038ff
  network_name: icd-test
  pan_id: 0x1234
  channel: 15
"""

# Quiet baseline: no api (warns), no logger output (warns), loop_interval and a long watchdog.
BASE = """
esphome:
  name: matter-icd-unit
{loop}
esp32:
  variant: ESP32H2
{toolchain}  watchdog_timeout: 60s
  framework:
    type: esp-idf

external_components:
  - source: {components}

logger:
  baud_rate: 0

network:
  enable_ipv6: true
"""


def make_config(body: str, *, thread: str | None = THREAD, base: str | None = None) -> str:
    from conftest import COMPONENTS

    loop = "  loop_interval: 1s\n" if icd.core_option_supported("loop_interval") else ""
    text = (base or BASE).format(toolchain=toolchain_line(), components=COMPONENTS, loop=loop)
    text += thread or ""
    text += textwrap.dedent(body)
    return text


@pytest.fixture
def run_icd(tmp_path):
    def run(body: str, **kwargs):
        path = tmp_path / "icd.yaml"
        path.write_text(make_config(body, **kwargs))
        return run_esphome("config", str(path), cwd=tmp_path)

    return run


def validated(**icd_config):
    return icd.validate_icd_schema(icd_config)


LIT_OK = {
    "mode": "lit",
    "uat_hint": ["custom_instruction"],
    "uat_instruction": "Press the button",
}


# --------------------------------------------------------------------------
# Presets and sdkconfig mapping (no ESPHome run)
# --------------------------------------------------------------------------


def ms(value) -> int:
    return int(value.total_milliseconds)


def test_sit_preset_is_the_icd_app_sit_values():
    config = validated()
    assert config["mode"] == "sit"
    assert ms(config["slow_poll_interval"]) == 5000
    assert ms(config["fast_poll_interval"]) == 500
    assert ms(config["active_interval"]) == 1000
    assert ms(config["active_threshold"]) == 1000
    assert int(config["idle_interval"].total_seconds) == 60
    assert config["light_sleep"] is True
    assert config["report_on_active"] is False


def test_lit_preset_is_the_icd_app_lit_values():
    config = validated(**LIT_OK)
    assert ms(config["slow_poll_interval"]) == 20000
    assert ms(config["fast_poll_interval"]) == 500
    assert ms(config["active_threshold"]) == 5000
    assert int(config["idle_interval"].total_seconds) == 600
    assert config["clients_per_fabric"] == 2


def test_explicit_values_override_the_preset():
    config = validated(slow_poll_interval="2s", idle_interval="30s")
    assert ms(config["slow_poll_interval"]) == 2000
    assert int(config["idle_interval"].total_seconds) == 30


def test_sit_sdkconfig():
    options = icd.icd_sdkconfig(validated())
    assert options["CONFIG_ENABLE_ICD_SERVER"] is True
    assert options["CONFIG_ENABLE_ICD_LIT"] is False
    # In the esp-matter data model CIP and UAT exist only with LIT.
    assert options["CONFIG_ENABLE_ICD_CIP"] is False
    assert options["CONFIG_ENABLE_ICD_USER_ACTIVE_MODE_TRIGGER"] is False
    assert options["CONFIG_ICD_SLOW_POLL_INTERVAL_MS"] == 5000
    assert options["CONFIG_ICD_FAST_POLL_INTERVAL_MS"] == 500
    assert options["CONFIG_ICD_IDLE_MODE_INTERVAL_SEC"] == 60
    assert options["CONFIG_ICD_ACTIVE_MODE_INTERVAL_MS"] == 1000
    assert options["CONFIG_ICD_ACTIVE_MODE_THRESHOLD_MS"] == 1000
    assert options["CONFIG_ICD_REPORT_ON_ACTIVE_MODE"] is False
    assert "CONFIG_ENABLE_ICD_DSLS" not in options
    assert "CONFIG_ICD_CLIENTS_SUPPORTED_PER_FABRIC" not in options


def test_lit_sdkconfig_enables_lit_cip_uat_and_dsls():
    options = icd.icd_sdkconfig(validated(**LIT_OK, clients_per_fabric=3))
    for key in (
        "CONFIG_ENABLE_ICD_SERVER",
        "CONFIG_ENABLE_ICD_LIT",
        "CONFIG_ENABLE_ICD_CIP",
        "CONFIG_ENABLE_ICD_USER_ACTIVE_MODE_TRIGGER",
        "CONFIG_ENABLE_ICD_DSLS",
    ):
        assert options[key] is True, key
    assert options["CONFIG_ICD_SLOW_POLL_INTERVAL_MS"] == 20000
    assert options["CONFIG_ICD_IDLE_MODE_INTERVAL_SEC"] == 600
    assert options["CONFIG_ICD_ACTIVE_MODE_THRESHOLD_MS"] == 5000
    assert options["CONFIG_ICD_CLIENTS_SUPPORTED_PER_FABRIC"] == 3
    assert options["CONFIG_ICD_MAX_NOTIFICATION_SUBSCRIBERS"] == 2
    assert options["CONFIG_ICD_SHORT_IDLE_MODE_DURATION_SEC"] == 60


def test_lit_short_idle_duration_never_exceeds_the_idle_interval():
    # CHIP static_asserts SHORT_IDLE <= IDLE; its Kconfig default is 60 s.
    options = icd.icd_sdkconfig(validated(**{**LIT_OK, "idle_interval": "30s", "slow_poll_interval": "20s"}))
    assert options["CONFIG_ICD_SHORT_IDLE_MODE_DURATION_SEC"] == 30


def test_light_sleep_sdkconfig_and_opt_out():
    on = icd.icd_sdkconfig(validated())
    for key in (
        "CONFIG_PM_ENABLE",
        "CONFIG_FREERTOS_USE_TICKLESS_IDLE",
        "CONFIG_IEEE802154_SLEEP_ENABLE",
        "CONFIG_ESP_PHY_MAC_BB_PD",
    ):
        assert on[key] is True, key
    assert on["CONFIG_FREERTOS_HZ"] == 1000
    # Experimental options that break ESPHome I2C/SPI/ADC drivers stay off.
    assert not any("POWER_DOWN" in key for key in on)
    off = icd.icd_sdkconfig(validated(light_sleep=False))
    assert "CONFIG_PM_ENABLE" not in off


def test_uat_hint_bitmap():
    config = validated(**{**LIT_OK, "uat_hint": ["custom_instruction", "device_manual"]})
    assert icd.uat_hint_value(config) == 0x4 | 0x8
    assert icd.uat_hint_value(validated(mode="sit")) == 0


# --------------------------------------------------------------------------
# Schema errors
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"slow_poll_interval": "16s"}, "Short Idle Time"),
        ({"fast_poll_interval": "1s"}, "smaller than"),
        ({"fast_poll_interval": "2s"}, "smaller than"),
        ({"idle_interval": "3s", "slow_poll_interval": "5s"}, "must not be shorter"),
        ({"idle_interval": "64801s"}, "between 1s and 64800s"),
        ({"active_interval": "200ms", "fast_poll_interval": "100ms"}, "at least 300ms"),
        ({"active_threshold": "200ms"}, "at least 300ms"),
        ({"active_threshold": "70s"}, "at most"),
        ({"active_interval": "2s", "idle_interval": "1s", "slow_poll_interval": "1s"}, "longer than"),
        ({"clients_per_fabric": 2}, "only available with `mode: lit`"),
        ({"uat_hint": ["power_cycle"]}, "only available with `mode: lit`"),
        ({"uat_instruction": "Press"}, "only available with `mode: lit`"),
    ],
)
def test_sit_schema_errors(kwargs, message):
    with pytest.raises(cv.Invalid, match=message):
        validated(**kwargs)


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"active_threshold": "4s"}, "at least 5s"),
        ({"uat_hint": None}, "`uat_hint` is required"),
        ({"uat_hint": ["custom_instruction"], "uat_instruction": None}, "`uat_instruction` is required"),
        ({"uat_hint": ["actuate_sensor_seconds"], "uat_instruction": "ten"}, "decimal number"),
        ({"uat_instruction": "x" * 129}, "128"),
    ],
)
def test_lit_schema_errors(kwargs, message):
    config = {**LIT_OK}
    for key, value in kwargs.items():
        if value is None:
            config.pop(key, None)
        else:
            config[key] = value
    with pytest.raises(cv.Invalid, match=message):
        validated(**config)


def test_lit_hint_without_instruction_is_fine_for_generic_hints():
    validated(mode="lit", uat_hint=["power_cycle", "device_manual"])


def test_lit_numeric_instruction_accepted():
    validated(**{**LIT_OK, "uat_hint": ["actuate_sensor_seconds"], "uat_instruction": "10"})


def test_lit_slow_poll_above_15s_allowed():
    config = validated(**{**LIT_OK, "slow_poll_interval": "60s"})
    assert ms(config["slow_poll_interval"]) == 60000


def test_unknown_hint_rejected():
    with pytest.raises(cv.Invalid):
        validated(**{**LIT_OK, "uat_hint": ["nope"]})


# --------------------------------------------------------------------------
# Final validation through `esphome config`
# --------------------------------------------------------------------------

SENSOR_MATTER = """
matter:
  icd: {}
  endpoints:
    1:
      temperature_sensor: temp
sensor:
  - platform: internal_temperature
    id: temp
    update_interval: never
"""


def test_sit_config_is_valid_and_quiet(run_icd):
    result = run_icd(SENSOR_MATTER)
    assert result.ok, result.output[-2000:]
    assert "matter icd:" not in result.output, result.output[-2000:]


def test_lit_config_is_valid(run_icd):
    result = run_icd(
        """
        matter:
          icd:
            mode: lit
            uat_hint: [custom_instruction]
            uat_instruction: Press the button
        """
    )
    assert result.ok, result.output[-2000:]


def test_requires_openthread(run_icd):
    result = run_icd("matter:\n  icd: {}\n", thread="")
    assert not result.ok
    assert "requires an `openthread:` block" in result.output


def test_rejects_wifi(run_icd):
    result = run_icd(
        "matter:\n  icd: {}\nwifi:\n  ssid: unit\n  password: unit-test-password\n",
        thread="",
    )
    assert not result.ok
    assert "Thread only" in result.output


def test_requires_mtd(run_icd):
    result = run_icd("matter:\n  icd: {}\n", thread=THREAD.replace("MTD", "FTD").replace("  poll_period: 15s\n", ""))
    assert not result.ok
    assert "device_type: MTD" in result.output


def test_default_device_type_is_ftd_and_rejected(run_icd):
    result = run_icd("matter:\n  icd: {}\n", thread=THREAD.replace("  device_type: MTD\n", ""))
    assert not result.ok
    assert "device_type: MTD" in result.output


def test_requires_poll_period(run_icd):
    result = run_icd("matter:\n  icd: {}\n", thread=THREAD.replace("  poll_period: 15s\n", ""))
    assert not result.ok
    assert "poll_period" in result.output


def test_rejects_set_poll_period_action(run_icd):
    result = run_icd(
        """
        matter:
          icd: {}
        button:
          - platform: template
            name: Fast poll
            on_press:
              - openthread.set_poll_period: 100ms
        """
    )
    assert not result.ok
    assert "openthread.set_poll_period" in result.output


def test_trigger_active_action_needs_icd(run_icd):
    result = run_icd(
        """
        matter:
        button:
          - platform: template
            name: Wake
            on_press:
              - matter.icd.trigger_active
        """
    )
    assert not result.ok
    assert "needs `matter: icd:`" in result.output


def test_trigger_active_action_with_icd(run_icd):
    result = run_icd(
        """
        matter:
          icd: {}
        button:
          - platform: template
            name: Wake
            on_press:
              - matter.icd.trigger_active
        """
    )
    assert result.ok, result.output[-2000:]


def test_on_active_and_on_idle_triggers(run_icd):
    result = run_icd(
        """
        matter:
          icd:
            on_active:
              - logger.log: active
            on_idle:
              - logger.log: idle
        """
    )
    assert result.ok, result.output[-2000:]


def test_warns_api(run_icd):
    result = run_icd("api:\nmatter:\n  icd: {}\n")
    assert result.ok, result.output[-2000:]
    assert "`api:` keeps the radio busy" in result.output


def test_warns_esphome_ota(run_icd):
    result = run_icd("ota:\n  - platform: esphome\nmatter:\n  icd: {}\n")
    assert result.ok, result.output[-2000:]
    assert "platform: esphome` does not work" in result.output


def test_warns_deep_sleep(run_icd):
    result = run_icd("deep_sleep:\nmatter:\n  icd: {}\n")
    assert result.ok, result.output[-2000:]
    assert "deep_sleep" in result.output and "defeats ICD" in result.output


def test_warns_loop_and_watchdog_and_logger(run_icd):
    base = BASE.replace("{loop}", "").replace("  watchdog_timeout: 60s\n", "").replace(
        "  baud_rate: 0\n", ""
    )
    result = run_icd("matter:\n  icd: {}\n", base=base)
    assert result.ok, result.output[-2000:]
    if icd.core_option_supported("loop_interval"):
        assert "loop_interval" in result.output
    assert "watchdog_timeout" in result.output
    assert "baud_rate: 0" in result.output


def test_light_sleep_off_skips_light_sleep_warnings(run_icd):
    base = BASE.replace("{loop}", "").replace("  watchdog_timeout: 60s\n", "").replace(
        "  baud_rate: 0\n", ""
    )
    result = run_icd("matter:\n  icd:\n    light_sleep: false\n", base=base)
    assert result.ok, result.output[-2000:]
    assert "matter icd:" not in result.output, result.output[-2000:]


def test_warns_fast_sensor_update_interval(run_icd):
    result = run_icd(
        """
        matter:
          icd:
            idle_interval: 60s
          endpoints:
            1:
              temperature_sensor: temp
        sensor:
          - platform: internal_temperature
            id: temp
            update_interval: 10s
        """
    )
    assert result.ok, result.output[-2000:]
    assert "faster than `idle_interval`" in result.output


def test_update_interval_never_does_not_warn(run_icd):
    result = run_icd(SENSOR_MATTER)
    assert "faster than" not in result.output


def test_schema_error_is_reported_with_path(run_icd):
    result = run_icd("matter:\n  icd:\n    slow_poll_interval: 30s\n")
    assert not result.ok
    assert "Short Idle Time" in result.output


@pytest.mark.parametrize("name", ["esp32-c6-icd", "esp32-h2-icd"])
def test_icd_management_cluster_sources_are_compiled(name):
    """esp_matter always compiles the ICD Management integration.cpp, which needs the cluster's
    ICDManagementCluster.cpp (only built with this option) at link time, for SIT and LIT."""
    from pathlib import Path

    snapshot = Path(__file__).parent.parent / "snapshots" / f"{name}.sdkconfig.txt"
    assert f"{icd.ICD_MANAGEMENT_SDKCONFIG}=y" in snapshot.read_text()
