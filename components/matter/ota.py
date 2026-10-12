"""Matter OTA Requestor support for esphome-matter.

Opt-in: ``matter: ota: true`` (or an ``ota:`` / ``ota_requestor:`` block) compiles
connectedhomeip's OTA Requestor into the firmware so Matter controllers (Home
Assistant's matter server with ``--ota-provider-dir``, Apple/Google via the DCL)
can update the device. The Matter OTA writes into the same ota_0/ota_1
partitions as ESPHome's own OTA, so both update paths coexist. See
docs/matter-ota.md.
"""

import logging

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome import automation
from esphome.components.esp32 import add_idf_sdkconfig_option
from esphome.const import (
    CONF_ESPHOME,
    CONF_ID,
    CONF_PROJECT,
    CONF_TRIGGER_ID,
    CONF_VERSION,
)
from esphome.core import CORE
from esphome.types import ConfigType

from .types import matter_ns

_LOGGER = logging.getLogger(__name__)

CONF_OTA = "ota"
CONF_OTA_REQUESTOR = "ota_requestor"
CONF_SOFTWARE_VERSION = "software_version"
CONF_QUERY_INTERVAL = "query_interval"
CONF_WATCHDOG_TIMEOUT = "watchdog_timeout"
CONF_ON_STATE = "on_state"

MatterOta = matter_ns.class_("MatterOta", cg.Component)
MatterOtaStateTrigger = matter_ns.class_(
    "MatterOtaStateTrigger", automation.Trigger.template(cg.std_string)
)
MatterOtaEventTrigger = matter_ns.class_("MatterOtaEventTrigger", automation.Trigger)
MatterOtaEvent = matter_ns.enum("MatterOtaEvent", is_class=True)

# config key -> MatterOtaEvent value
_EVENT_TRIGGERS = {
    "on_download_started": "DOWNLOAD_STARTED",
    "on_download_complete": "DOWNLOAD_COMPLETE",
    "on_download_failed": "DOWNLOAD_FAILED",
    "on_apply_started": "APPLY_STARTED",
    "on_apply_failed": "APPLY_FAILED",
}

_OTA_BLOCK_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(MatterOta),
        # How often the device asks its default OTA provider for an update.
        cv.Optional(CONF_QUERY_INTERVAL, default="24h"): cv.All(
            cv.positive_time_period_seconds,
            cv.Range(min=cv.TimePeriod(seconds=60)),
        ),
        # Aborts a stuck (non-idle) OTA state machine after this long.
        cv.Optional(CONF_WATCHDOG_TIMEOUT, default="300s"): cv.All(
            cv.positive_time_period_seconds,
            cv.Range(min=cv.TimePeriod(seconds=30)),
        ),
        # State names: space_available, download_in_progress, download_complete,
        # download_failed, download_aborted, apply_in_progress, apply_complete,
        # apply_failed.
        cv.Optional(CONF_ON_STATE): automation.validate_automation(
            {cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(MatterOtaStateTrigger)}
        ),
        **{
            cv.Optional(key): automation.validate_automation(
                {cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(MatterOtaEventTrigger)}
            )
            for key in _EVENT_TRIGGERS
        },
    }
)


def _ota_block(value):
    """`ota: true` -> defaults, `ota: false` -> disabled, mapping -> block."""
    if value is None or value is True:
        return _OTA_BLOCK_SCHEMA({})
    if value is False:
        return None
    return _OTA_BLOCK_SCHEMA(value)


# Merged into matter's CONFIG_SCHEMA (a single `**OTA_SCHEMA_KEYS`).
OTA_SCHEMA_KEYS = {
    # `ota: true` or an `ota:` block.
    cv.Optional(CONF_OTA): _ota_block,
    # Alias: `ota_requestor:` block.
    cv.Optional(CONF_OTA_REQUESTOR): _ota_block,
    # uint32 Matter SoftwareVersion; must increase for every OTA update.
    cv.Optional(CONF_SOFTWARE_VERSION): cv.int_range(min=1, max=0xFFFFFFFF),
}


def ota_config(config: ConfigType) -> ConfigType | None:
    """Returns the normalized OTA requestor block, or None when disabled."""
    return config.get(CONF_OTA_REQUESTOR) or config.get(CONF_OTA) or None


def _ota_key(config: ConfigType) -> str:
    return CONF_OTA_REQUESTOR if config.get(CONF_OTA_REQUESTOR) else CONF_OTA


def validate_ota_schema(config: ConfigType) -> ConfigType:
    """Cross-key validation, run after the schema."""
    if config.get(CONF_OTA_REQUESTOR) and config.get(CONF_OTA):
        raise cv.Invalid(f"Use either `{CONF_OTA}` or `{CONF_OTA_REQUESTOR}`, not both.")
    if ota_config(config) and CONF_SOFTWARE_VERSION not in config:
        raise cv.Invalid(
            f"`{CONF_SOFTWARE_VERSION}` is required when Matter OTA is enabled. It is "
            "the uint32 Matter software version of this firmware and must be higher "
            "for every image offered as an update.",
            path=[_ota_key(config)],
        )
    return config


def final_validate_ota(config: ConfigType, full_config: ConfigType) -> None:
    if not ota_config(config):
        return
    if not any(k in full_config for k in ("wifi", "ethernet", "openthread")):
        raise cv.Invalid(
            "Matter OTA needs an IP network (`wifi`, `ethernet` or `openthread`): the "
            "image is downloaded over BDX on the Matter network, which BLE-only "
            "(commissioning) builds do not have.",
            path=[_ota_key(config)],
        )
    if "ota" not in full_config:
        _LOGGER.info(
            "matter: OTA requestor enabled without ESPHome `ota:`; the device can "
            "then only be updated through Matter (or serial flashing)."
        )


def _project_version() -> str | None:
    return CORE.config.get(CONF_ESPHOME, {}).get(CONF_PROJECT, {}).get(CONF_VERSION)


def add_software_version_sdkconfig(config: ConfigType) -> None:
    """Numeric Matter SoftwareVersion (+ the app descriptor version string)."""
    version = config.get(CONF_SOFTWARE_VERSION)
    if version is None:
        return
    # connectedhomeip reads the number from CONFIG_DEVICE_SOFTWARE_VERSION_NUMBER only
    # when CONFIG_APP_PROJECT_VER_FROM_CONFIG is set (it is 0 otherwise). The
    # SoftwareVersionString is esp_app_desc_t.version, i.e. CONFIG_APP_PROJECT_VER;
    # ESPHome's `esphome: project: version` wins so both stay consistent.
    add_idf_sdkconfig_option("CONFIG_APP_PROJECT_VER_FROM_CONFIG", True)
    add_idf_sdkconfig_option("CONFIG_DEVICE_SOFTWARE_VERSION_NUMBER", version)
    add_idf_sdkconfig_option(
        "CONFIG_APP_PROJECT_VER", _project_version() or str(version)
    )


async def to_code_ota(config: ConfigType) -> None:
    add_software_version_sdkconfig(config)
    ota = ota_config(config)
    if not ota:
        return

    # connectedhomeip compiles the OTA Requestor (and esp_matter wires it into
    # esp_matter::start()) only with this option.
    add_idf_sdkconfig_option("CONFIG_ENABLE_OTA_REQUESTOR", True)
    add_idf_sdkconfig_option("CONFIG_OTA_AUTO_REBOOT_ON_APPLY", True)
    cg.add_define("USE_MATTER_OTA")

    var = cg.new_Pvariable(ota[CONF_ID])
    await cg.register_component(var, {})
    cg.add(var.set_query_interval(ota[CONF_QUERY_INTERVAL].total_seconds))
    cg.add(var.set_watchdog_timeout(ota[CONF_WATCHDOG_TIMEOUT].total_seconds))

    for conf in ota.get(CONF_ON_STATE, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var)
        await automation.build_automation(trigger, [(cg.std_string, "state")], conf)
    for key, event in _EVENT_TRIGGERS.items():
        for conf in ota.get(key, []):
            trigger = cg.new_Pvariable(
                conf[CONF_TRIGGER_ID], var, getattr(MatterOtaEvent, event)
            )
            await automation.build_automation(trigger, [], conf)
