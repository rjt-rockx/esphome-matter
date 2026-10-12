"""Battery powered Thread devices: Matter Intermittently Connected Device (ICD) support.

EXPERIMENTAL and not verified on hardware. See docs/icd.md.

``matter: icd:`` turns the node into a Thread sleepy end device whose poll period
is owned by connectedhomeip's ICD manager:

* ``mode: sit`` (default): Short Idle Time, slow poll at most 15 s. Behaves like a
  normal Matter device on every controller.
* ``mode: lit``: Long Idle Time with Check-In and User Active Mode Trigger. Dynamic
  SIT/LIT switching stays on, so the device runs as SIT until a controller
  registers as an ICD client.

Everything is configured through CHIP's Kconfig options (so CHIP's static_asserts
check the values at compile time). Only the ``openthread:`` path is supported: the
sleepy link mode (``rxOnWhenIdle=false``) comes from ESPHome's own
``openthread: {device_type: MTD, poll_period: ...}`` because esp-matter does not
manage OpenThread here.
"""

from __future__ import annotations

import logging

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome import automation
from esphome.components.esp32 import add_idf_sdkconfig_option
from esphome.const import CONF_TRIGGER_ID
from esphome.core import HexInt
from esphome.types import ConfigType

from .types import MatterComponent, matter_ns

_LOGGER = logging.getLogger(__name__)

CONF_ICD = "icd"
CONF_MODE = "mode"
CONF_SLOW_POLL_INTERVAL = "slow_poll_interval"
CONF_FAST_POLL_INTERVAL = "fast_poll_interval"
CONF_IDLE_INTERVAL = "idle_interval"
CONF_ACTIVE_INTERVAL = "active_interval"
CONF_ACTIVE_THRESHOLD = "active_threshold"
CONF_REPORT_ON_ACTIVE = "report_on_active"
CONF_LIGHT_SLEEP = "light_sleep"
CONF_CLIENTS_PER_FABRIC = "clients_per_fabric"
CONF_UAT_HINT = "uat_hint"
CONF_UAT_INSTRUCTION = "uat_instruction"
CONF_ON_ACTIVE = "on_active"
CONF_ON_IDLE = "on_idle"

MODE_SIT = "sit"
MODE_LIT = "lit"

# Matter 1.4 spec 9.17.5.4 / UserActiveModeTriggerBitmap (zzz_generated IcdManagement/Enums.h).
UAT_HINTS = {
    "power_cycle": 0x1,
    "settings_menu": 0x2,
    "custom_instruction": 0x4,
    "device_manual": 0x8,
    "actuate_sensor": 0x10,
    "actuate_sensor_seconds": 0x20,
    "actuate_sensor_times": 0x40,
    "actuate_sensor_lights_blink": 0x80,
    "reset_button": 0x100,
    "reset_button_lights_blink": 0x200,
    "reset_button_seconds": 0x400,
    "reset_button_times": 0x800,
    "setup_button": 0x1000,
    "setup_button_seconds": 0x2000,
    "setup_button_lights_blink": 0x4000,
    "setup_button_times": 0x8000,
    "app_defined_button": 0x10000,
}
# Hints whose UserActiveModeTriggerInstruction is mandatory: free text for
# custom_instruction, a decimal number (seconds or times) for the others.
_INSTRUCTION_TEXT_HINTS = {"custom_instruction"}
_INSTRUCTION_NUMBER_HINTS = {
    "actuate_sensor_seconds",
    "actuate_sensor_times",
    "reset_button_seconds",
    "reset_button_times",
    "setup_button_seconds",
    "setup_button_times",
}
# UserActiveModeTriggerInstruction is a string of at most 128 bytes.
_UAT_INSTRUCTION_MAX = 128

# Values of the Espressif icd_app (examples/icd_app/sdkconfig.defaults{,.lit}), not CHIP's
# Kconfig defaults (those are development values: idle 2 s, fast poll 200 ms).
PRESETS = {
    MODE_SIT: {
        CONF_SLOW_POLL_INTERVAL: "5s",
        CONF_FAST_POLL_INTERVAL: "500ms",
        CONF_IDLE_INTERVAL: "60s",
        CONF_ACTIVE_INTERVAL: "1s",
        CONF_ACTIVE_THRESHOLD: "1s",
    },
    MODE_LIT: {
        CONF_SLOW_POLL_INTERVAL: "20s",
        CONF_FAST_POLL_INTERVAL: "500ms",
        CONF_IDLE_INTERVAL: "600s",
        CONF_ACTIVE_INTERVAL: "1s",
        CONF_ACTIVE_THRESHOLD: "5s",
    },
}

SIT_SLOW_POLL_MAX_MS = 15_000
MIN_ACTIVE_MS = 300
LIT_ACTIVE_THRESHOLD_MIN_MS = 5_000
ACTIVE_THRESHOLD_MAX_MS = 65_535  # CHIP stores it as Milliseconds16
IDLE_MIN_S = 1
IDLE_MAX_S = 64_800  # 18 h, spec limit
# CHIP_CONFIG_ICD_SHORT_IDLE_MODE_DURATION_SEC must not exceed the idle interval; CHIP's default is 60 s.
SHORT_IDLE_DEFAULT_S = 60

MatterIcdActiveTrigger = matter_ns.class_(
    "MatterIcdActiveTrigger", automation.Trigger.template()
)
MatterIcdIdleTrigger = matter_ns.class_(
    "MatterIcdIdleTrigger", automation.Trigger.template()
)
MatterIcdTriggerActiveAction = matter_ns.class_(
    "MatterIcdTriggerActiveAction", automation.Action
)

ACTION_TRIGGER_ACTIVE = "matter.icd.trigger_active"
ACTION_SET_POLL_PERIOD = "openthread.set_poll_period"


ICD_SCHEMA = cv.Schema(
    {
        cv.Optional(CONF_MODE, default=MODE_SIT): cv.one_of(
            MODE_SIT, MODE_LIT, lower=True
        ),
        # Defaults depend on `mode` and are filled in by _apply_presets.
        cv.Optional(CONF_SLOW_POLL_INTERVAL): cv.positive_time_period_milliseconds,
        cv.Optional(CONF_FAST_POLL_INTERVAL): cv.positive_time_period_milliseconds,
        cv.Optional(CONF_IDLE_INTERVAL): cv.positive_time_period_seconds,
        cv.Optional(CONF_ACTIVE_INTERVAL): cv.positive_time_period_milliseconds,
        cv.Optional(CONF_ACTIVE_THRESHOLD): cv.positive_time_period_milliseconds,
        cv.Optional(CONF_REPORT_ON_ACTIVE, default=False): cv.boolean,
        cv.Optional(CONF_LIGHT_SLEEP, default=True): cv.boolean,
        # LIT only
        cv.Optional(CONF_CLIENTS_PER_FABRIC): cv.int_range(min=1, max=255),
        cv.Optional(CONF_UAT_HINT): cv.ensure_list(
            cv.one_of(*UAT_HINTS, lower=True)
        ),
        cv.Optional(CONF_UAT_INSTRUCTION): cv.All(
            cv.string_strict, cv.Length(max=_UAT_INSTRUCTION_MAX)
        ),
        cv.Optional(CONF_ON_ACTIVE): automation.validate_automation(
            {cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(MatterIcdActiveTrigger)}
        ),
        cv.Optional(CONF_ON_IDLE): automation.validate_automation(
            {cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(MatterIcdIdleTrigger)}
        ),
    }
)


def _ms(value) -> int:
    return int(value.total_milliseconds)


def _fmt_ms(ms: int) -> str:
    return f"{ms / 1000:g}s" if ms % 1000 == 0 or ms > 1000 else f"{ms}ms"


def _apply_presets(config: ConfigType) -> ConfigType:
    config = dict(config)
    mode = config[CONF_MODE]
    for key, default in PRESETS[mode].items():
        if key not in config:
            config[key] = (
                cv.positive_time_period_seconds(default)
                if key == CONF_IDLE_INTERVAL
                else cv.positive_time_period_milliseconds(default)
            )
    if mode == MODE_LIT:
        config.setdefault(CONF_CLIENTS_PER_FABRIC, 2)
    return config


def _validate_icd(config: ConfigType) -> ConfigType:
    mode = config[CONF_MODE]

    # LIT-only options with SIT. In the esp-matter data model the CIP and UAT features exist only
    # together with LIT, so a SIT node has neither a hint nor an instruction.
    if mode == MODE_SIT:
        for key in (CONF_CLIENTS_PER_FABRIC, CONF_UAT_HINT, CONF_UAT_INSTRUCTION):
            if key in config:
                raise cv.Invalid(
                    f"`{key}` is only available with `mode: lit` (Check-In and User Active Mode "
                    "Trigger exist only on Long Idle Time devices)",
                    path=[key],
                )

    config = _apply_presets(config)

    slow = _ms(config[CONF_SLOW_POLL_INTERVAL])
    fast = _ms(config[CONF_FAST_POLL_INTERVAL])
    active = _ms(config[CONF_ACTIVE_INTERVAL])
    threshold = _ms(config[CONF_ACTIVE_THRESHOLD])
    idle_s = int(config[CONF_IDLE_INTERVAL].total_seconds)

    if not IDLE_MIN_S <= idle_s <= IDLE_MAX_S:
        raise cv.Invalid(
            f"`{CONF_IDLE_INTERVAL}` must be between {IDLE_MIN_S}s and {IDLE_MAX_S}s (18 h), got {idle_s}s",
            path=[CONF_IDLE_INTERVAL],
        )
    if slow < 1:
        raise cv.Invalid(
            f"`{CONF_SLOW_POLL_INTERVAL}` must be greater than 0", path=[CONF_SLOW_POLL_INTERVAL]
        )
    if fast < 1:
        raise cv.Invalid(
            f"`{CONF_FAST_POLL_INTERVAL}` must be greater than 0", path=[CONF_FAST_POLL_INTERVAL]
        )
    if mode == MODE_SIT and slow > SIT_SLOW_POLL_MAX_MS:
        raise cv.Invalid(
            f"`{CONF_SLOW_POLL_INTERVAL}` is {_fmt_ms(slow)} but a Short Idle Time (SIT) device must "
            f"poll at least every {SIT_SLOW_POLL_MAX_MS // 1000}s (Matter spec 9.16.1.5). Lower it, "
            "or use `mode: lit` (only worth it with Home Assistant or SmartThings, see docs/icd.md)",
            path=[CONF_SLOW_POLL_INTERVAL],
        )
    if fast >= active:
        raise cv.Invalid(
            f"`{CONF_FAST_POLL_INTERVAL}` ({_fmt_ms(fast)}) must be smaller than "
            f"`{CONF_ACTIVE_INTERVAL}` ({_fmt_ms(active)})",
            path=[CONF_FAST_POLL_INTERVAL],
        )
    if idle_s * 1000 < slow:
        raise cv.Invalid(
            f"`{CONF_IDLE_INTERVAL}` ({idle_s}s) must not be shorter than "
            f"`{CONF_SLOW_POLL_INTERVAL}` ({_fmt_ms(slow)})",
            path=[CONF_IDLE_INTERVAL],
        )
    if active < MIN_ACTIVE_MS:
        raise cv.Invalid(
            f"`{CONF_ACTIVE_INTERVAL}` must be at least {MIN_ACTIVE_MS}ms (Matter spec), got {_fmt_ms(active)}",
            path=[CONF_ACTIVE_INTERVAL],
        )
    if threshold < MIN_ACTIVE_MS:
        raise cv.Invalid(
            f"`{CONF_ACTIVE_THRESHOLD}` must be at least {MIN_ACTIVE_MS}ms (Matter spec), got {_fmt_ms(threshold)}",
            path=[CONF_ACTIVE_THRESHOLD],
        )
    if threshold > ACTIVE_THRESHOLD_MAX_MS:
        raise cv.Invalid(
            f"`{CONF_ACTIVE_THRESHOLD}` can be at most {ACTIVE_THRESHOLD_MAX_MS}ms",
            path=[CONF_ACTIVE_THRESHOLD],
        )
    if mode == MODE_LIT and threshold < LIT_ACTIVE_THRESHOLD_MIN_MS:
        raise cv.Invalid(
            f"`{CONF_ACTIVE_THRESHOLD}` must be at least {LIT_ACTIVE_THRESHOLD_MIN_MS // 1000}s on a "
            f"Long Idle Time (LIT) device, got {_fmt_ms(threshold)}",
            path=[CONF_ACTIVE_THRESHOLD],
        )
    if active > idle_s * 1000:
        raise cv.Invalid(
            f"`{CONF_ACTIVE_INTERVAL}` ({_fmt_ms(active)}) must not be longer than "
            f"`{CONF_IDLE_INTERVAL}` ({idle_s}s)",
            path=[CONF_ACTIVE_INTERVAL],
        )

    if mode == MODE_LIT:
        _validate_uat(config)
    return config


def _validate_uat(config: ConfigType) -> None:
    hints = config.get(CONF_UAT_HINT)
    if not hints:
        # A LIT device must tell users how to wake it (TC-ICDM, Matter spec 9.17.6); a hint
        # of 0 is not a valid answer.
        raise cv.Invalid(
            f"`{CONF_UAT_HINT}` is required with `mode: lit`: tell the user how to wake the device, "
            "for example `uat_hint: [custom_instruction]` with `uat_instruction: Press the button`",
            path=[CONF_UAT_HINT],
        )
    instruction = config.get(CONF_UAT_INSTRUCTION)
    text_hints = [h for h in hints if h in _INSTRUCTION_TEXT_HINTS]
    number_hints = [h for h in hints if h in _INSTRUCTION_NUMBER_HINTS]
    if (text_hints or number_hints) and not instruction:
        raise cv.Invalid(
            f"`{CONF_UAT_INSTRUCTION}` is required for the hint(s) {', '.join(text_hints + number_hints)}",
            path=[CONF_UAT_INSTRUCTION],
        )
    if number_hints and not text_hints and not instruction.isdecimal():
        raise cv.Invalid(
            f"`{CONF_UAT_INSTRUCTION}` must be a decimal number (seconds or times) for the hint(s) "
            f"{', '.join(number_hints)}, got '{instruction}'",
            path=[CONF_UAT_INSTRUCTION],
        )


def validate_icd_schema(value):
    """Schema of the ``icd`` key: validates, fills the mode presets, checks the intervals."""
    return _validate_icd(ICD_SCHEMA(value))


# ----------------------------------------------------------------------------------------------
# Final validation (needs the full config)
# ----------------------------------------------------------------------------------------------


def _find_keys(node, name: str) -> bool:
    """True if a mapping key equal to `name` occurs anywhere below `node` (an automation action)."""
    if isinstance(node, dict):
        return any(k == name or _find_keys(v, name) for k, v in node.items())
    if isinstance(node, (list, tuple)):
        return any(_find_keys(v, name) for v in node)
    return False


def _platforms(full_config, domain: str) -> list[dict]:
    value = full_config.get(domain)
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def final_validate_icd(config: ConfigType, full_config) -> None:
    """Checks that need the full configuration; `config` is the validated `matter:` block."""
    icd = config.get(CONF_ICD)
    if icd is None:
        if _find_keys(dict(full_config), ACTION_TRIGGER_ACTIVE):
            raise cv.Invalid(
                f"`{ACTION_TRIGGER_ACTIVE}` needs `matter: icd:` (it keeps the sleepy device awake)"
            )
        return

    if "wifi" in full_config or "ethernet" in full_config:
        raise cv.Invalid(
            "`matter: icd:` is Thread only: remove `wifi:` / `ethernet:`. ICD power saving does not exist "
            "on Wi-Fi or Ethernet in esp-matter 1.6",
            path=[CONF_ICD],
        )
    if "openthread" not in full_config:
        raise cv.Invalid(
            "`matter: icd:` requires an `openthread:` block with `device_type: MTD` and a `poll_period`. "
            "ICD over BLE commissioning (no `openthread:`) is not supported yet",
            path=[CONF_ICD],
        )
    ot = full_config["openthread"]
    if str(ot.get("device_type", "FTD")).upper() != "MTD":
        raise cv.Invalid(
            "`matter: icd:` requires `openthread: device_type: MTD` (the ICD server is only built for a "
            "minimal Thread device; the default is FTD)",
            path=[CONF_ICD],
        )
    poll = ot.get("poll_period")
    if poll is None or poll.total_milliseconds == 0:
        raise cv.Invalid(
            "`matter: icd:` requires `openthread: poll_period` greater than 0, for example `poll_period: 15s`. "
            "That is what makes ESPHome configure the radio as a sleepy end device (rxOnWhenIdle=false); "
            "the ICD manager then overrides the period at run time",
            path=[CONF_ICD],
        )
    if _find_keys(dict(full_config), ACTION_SET_POLL_PERIOD):
        raise cv.Invalid(
            f"`{ACTION_SET_POLL_PERIOD}` cannot be used with `matter: icd:`: the ICD manager owns the poll "
            "period and would fight the action. Tune `slow_poll_interval` / `fast_poll_interval` instead",
            path=[CONF_ICD],
        )

    _warn(icd, full_config)


def _warn(icd: ConfigType, full_config) -> None:
    from esphome.const import CONF_PLATFORM

    if "api" in full_config:
        _LOGGER.warning(
            "matter icd: `api:` keeps the radio busy (keepalive traffic the sleepy device has to poll for "
            "and the main loop wakes for). Remove it from battery builds"
        )
    if any(
        p.get(CONF_PLATFORM) == "esphome" for p in _platforms(full_config, "ota")
    ):
        _LOGGER.warning(
            "matter icd: `ota: platform: esphome` does not work while `openthread: poll_period` is set. "
            "Use Matter OTA (`matter: ota:`) for updates"
        )
    if "deep_sleep" in full_config:
        _LOGGER.warning(
            "matter icd: `deep_sleep:` reboots the chip on every wake (Thread re-attach, CASE and "
            "re-subscribe each time) and defeats ICD. Light sleep via `matter: icd:` is the supported model"
        )

    if icd[CONF_LIGHT_SLEEP]:
        _warn_light_sleep(full_config)

    idle_ms = int(icd[CONF_IDLE_INTERVAL].total_milliseconds)
    _warn_update_intervals(full_config, idle_ms)

    from esphome.components.esp32 import get_esp32_variant

    variant = get_esp32_variant()
    if variant not in ("ESP32C6", "ESP32H2"):
        _LOGGER.warning(
            "matter icd: only ESP32-C6 and ESP32-H2 are expected to work (variant %s is untested)",
            variant,
        )


def _warn_light_sleep(full_config) -> None:
    from esphome.const import CONF_WATCHDOG_TIMEOUT

    esphome_cfg = full_config.get("esphome", {})
    suspend = esphome_cfg.get("suspend_loop", False)
    loop_interval = esphome_cfg.get("loop_interval")
    if not suspend and (
        loop_interval is None or loop_interval.total_milliseconds < 1000
    ):
        _LOGGER.warning(
            "matter icd: the ESPHome main loop wakes every 16 ms by default, which keeps the chip out of "
            "light sleep. Set `esphome: suspend_loop: true` (or a `loop_interval` of 1s or more)"
        )
    esp32 = full_config.get("esp32", {})
    wdt = esp32.get(CONF_WATCHDOG_TIMEOUT)
    if wdt is not None and wdt.total_seconds <= 5:
        _LOGGER.warning(
            "matter icd: with the default `esp32: watchdog_timeout: 5s` the main loop still wakes every "
            "2 s. Raise it (up to 60s) to let the loop sleep up to 24 s"
        )
    logger = full_config.get("logger")
    if isinstance(logger, dict) and logger.get("baud_rate", 115200) != 0:
        _LOGGER.warning(
            "matter icd: the logger on %s keeps the chip awake (USB-Serial-JTAG does not survive light "
            "sleep). Use `logger: baud_rate: 0` in battery builds",
            logger.get("hardware_uart", "the serial port"),
        )


def _warn_update_intervals(full_config, idle_ms: int) -> None:
    """Warn about polled sensors mapped into Matter that update faster than the idle interval."""
    from .sensor_mapping import _bindings

    seen = set()
    matter_cfg = full_config.get("matter", {})
    for endpoint_config in matter_cfg.get("endpoints", {}).values():
        for _path, _attr, sensor_id, _overrides in _bindings(endpoint_config):
            if sensor_id in seen:
                continue
            seen.add(sensor_id)
            try:
                path = full_config.get_path_for_id(sensor_id)[:-1]
                sensor_cfg = full_config.get_config_for_path(path)
            except (KeyError, IndexError):
                continue
            interval = sensor_cfg.get("update_interval") if isinstance(sensor_cfg, dict) else None
            if interval is None or not hasattr(interval, "total_milliseconds"):
                continue
            ms = int(interval.total_milliseconds)
            # `never` is 4294967295 ms
            if ms < idle_ms:
                _LOGGER.warning(
                    "matter icd: sensor '%s' updates every %s, faster than `idle_interval` (%ss). Every "
                    "report costs about 1 mC; prefer `update_interval: never` with "
                    "`matter: icd: on_active: component.update: %s`",
                    sensor_id,
                    interval,
                    idle_ms // 1000,
                    sensor_id,
                )


# ----------------------------------------------------------------------------------------------
# Code generation
# ----------------------------------------------------------------------------------------------


def icd_sdkconfig(icd: ConfigType) -> dict[str, object]:
    """The sdkconfig options of an ICD configuration (also used by the unit tests)."""
    lit = icd[CONF_MODE] == MODE_LIT
    idle_s = int(icd[CONF_IDLE_INTERVAL].total_seconds)
    options: dict[str, object] = {
        "CONFIG_ENABLE_ICD_SERVER": True,
        "CONFIG_ICD_SLOW_POLL_INTERVAL_MS": _ms(icd[CONF_SLOW_POLL_INTERVAL]),
        "CONFIG_ICD_FAST_POLL_INTERVAL_MS": _ms(icd[CONF_FAST_POLL_INTERVAL]),
        "CONFIG_ICD_IDLE_MODE_INTERVAL_SEC": idle_s,
        "CONFIG_ICD_ACTIVE_MODE_INTERVAL_MS": _ms(icd[CONF_ACTIVE_INTERVAL]),
        "CONFIG_ICD_ACTIVE_MODE_THRESHOLD_MS": _ms(icd[CONF_ACTIVE_THRESHOLD]),
        "CONFIG_ICD_REPORT_ON_ACTIVE_MODE": icd[CONF_REPORT_ON_ACTIVE],
        "CONFIG_ENABLE_ICD_LIT": lit,
        # CIP and UAT are mandatory with LIT (Kconfig selects them) and, in the esp-matter data model,
        # only exist together with LIT.
        "CONFIG_ENABLE_ICD_CIP": lit,
        "CONFIG_ENABLE_ICD_USER_ACTIVE_MODE_TRIGGER": lit,
    }
    if lit:
        options.update(
            {
                # Dynamic SIT/LIT: run as SIT (short idle interval) until a client registers.
                "CONFIG_ENABLE_ICD_DSLS": True,
                "CONFIG_ICD_SHORT_IDLE_MODE_DURATION_SEC": min(
                    SHORT_IDLE_DEFAULT_S, idle_s
                ),
                "CONFIG_ICD_CLIENTS_SUPPORTED_PER_FABRIC": icd[CONF_CLIENTS_PER_FABRIC],
                # As the icd_app: leaves a slot for application listeners.
                "CONFIG_ICD_MAX_NOTIFICATION_SUBSCRIBERS": 2,
                "CONFIG_ENABLE_EXTENDED_DISCOVERY": True,
            }
        )
    if icd[CONF_LIGHT_SLEEP]:
        options.update(
            {
                "CONFIG_PM_ENABLE": True,
                "CONFIG_FREERTOS_USE_TICKLESS_IDLE": True,
                "CONFIG_FREERTOS_HZ": 1000,
                "CONFIG_IEEE802154_SLEEP_ENABLE": True,
                "CONFIG_ESP_PHY_MAC_BB_PD": True,
            }
        )
    return options


def uat_hint_value(icd: ConfigType) -> int:
    value = 0
    for hint in icd.get(CONF_UAT_HINT, []):
        value |= UAT_HINTS[hint]
    return value


async def icd_to_code(config: ConfigType) -> None:
    icd = config.get(CONF_ICD)
    if icd is None:
        return
    cg.add_define("USE_MATTER_ICD")
    for key, value in icd_sdkconfig(icd).items():
        add_idf_sdkconfig_option(key, value)
    if icd[CONF_LIGHT_SLEEP]:
        cg.add_define("USE_MATTER_ICD_LIGHT_SLEEP")
    if icd[CONF_MODE] == MODE_LIT:
        cg.add_define("USE_MATTER_ICD_LIT")
        cg.add_define("MATTER_ICD_UAT_HINT", HexInt(uat_hint_value(icd)))
        cg.add_define("MATTER_ICD_UAT_INSTRUCTION", icd.get(CONF_UAT_INSTRUCTION, ""))
    for conf in icd.get(CONF_ON_ACTIVE, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID])
        await automation.build_automation(trigger, [], conf)
    for conf in icd.get(CONF_ON_IDLE, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID])
        await automation.build_automation(trigger, [], conf)


@automation.register_action(
    ACTION_TRIGGER_ACTIVE,
    MatterIcdTriggerActiveAction,
    cv.Schema({cv.GenerateID(): cv.use_id(MatterComponent)}),
    synchronous=True,
)
async def matter_icd_trigger_active_to_code(config, action_id, template_arg, args):
    return cg.new_Pvariable(action_id, template_arg)
