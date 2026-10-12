"""Node identity (Basic Information), fabric limit and Identify automations.

Kept separate from __init__.py so the main schema only needs a small, additive hook.
"""

import logging

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome import automation
from esphome.components.esp32 import add_idf_sdkconfig_option, get_esp32_variant
from esphome.const import (
    CONF_ESPHOME,
    CONF_PROJECT,
    CONF_TRIGGER_ID,
    CONF_VERSION,
)
from esphome.const import __version__ as ESPHOME_VERSION
from esphome.core import CORE

from .const import (
    CONF_ENDPOINT,
    CONF_MAX_FABRICS,
    CONF_NODE_LABEL,
    CONF_ON_IDENTIFY,
    CONF_SERIAL_NUMBER,
    CONF_SOFTWARE_VERSION_STRING,
    CONF_UNIQUE_ID,
)
from .types import MatterIdentifyTrigger, matter_ns

_LOGGER = logging.getLogger(__name__)

# Attribute length limits from the Matter specification (Basic Information cluster).
NODE_LABEL_MAX = 32
SERIAL_NUMBER_MAX = 32
UNIQUE_ID_MAX = 32
HARDWARE_VERSION_STRING_MAX = 64
# esp_app_desc_t::version is char[32] and ConfigurationManager reads it for SoftwareVersionString.
SOFTWARE_VERSION_STRING_MAX = 31

# CONFIG_MAX_FABRICS lower bound in connectedhomeip's Kconfig is 5 (the spec minimum).
MAX_FABRICS_MIN = 5
MAX_FABRICS_MAX = 254

DEVICE_INFO_SCHEMA = {
    cv.Optional(CONF_NODE_LABEL): cv.All(
        cv.string_strict, cv.Length(min=0, max=NODE_LABEL_MAX)
    ),
    cv.Optional(CONF_SERIAL_NUMBER): cv.All(
        cv.string_strict, cv.Length(min=1, max=SERIAL_NUMBER_MAX)
    ),
    cv.Optional(CONF_SOFTWARE_VERSION_STRING): cv.All(
        cv.string_strict, cv.Length(min=1, max=SOFTWARE_VERSION_STRING_MAX)
    ),
    cv.Optional(CONF_UNIQUE_ID): cv.All(
        cv.string_strict, cv.Length(min=1, max=UNIQUE_ID_MAX)
    ),
    cv.Optional(CONF_MAX_FABRICS): cv.int_range(
        min=MAX_FABRICS_MIN, max=MAX_FABRICS_MAX
    ),
    cv.Optional(CONF_ON_IDENTIFY): automation.validate_automation(
        {
            cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(MatterIdentifyTrigger),
            cv.Optional(CONF_ENDPOINT): cv.int_range(min=0, max=65534),
        }
    ),
}


def _truncate(value: str, length: int, what: str) -> str:
    if len(value) > length:
        _LOGGER.warning(
            "Matter %s '%s' truncated to %d characters", what, value, length
        )
        return value[:length]
    return value


def default_node_label() -> str:
    label = CORE.friendly_name or CORE.name
    return _truncate(label, NODE_LABEL_MAX, "node_label")


def default_software_version(config=None) -> str:
    """software_version_string default: project version, else the numeric software_version, else ESPHome's."""
    project = CORE.config.get(CONF_ESPHOME, {}).get(CONF_PROJECT)
    if project and project.get(CONF_VERSION):
        return project[CONF_VERSION]
    if config is not None and config.get("software_version") is not None:
        return str(config["software_version"])
    return ESPHOME_VERSION


def default_hardware_version_string() -> str:
    return get_esp32_variant()


async def setup_device_info(var, config) -> None:
    """Pass Basic Information values to the C++ side and set the matching sdkconfig options."""
    info = var.get_device_info()
    cg.add(info.set_node_label(config.get(CONF_NODE_LABEL, default_node_label())))
    if CONF_SERIAL_NUMBER in config:
        cg.add(info.set_serial_number(config[CONF_SERIAL_NUMBER]))
    if CONF_UNIQUE_ID in config:
        cg.add(info.set_unique_id(config[CONF_UNIQUE_ID]))

    # Hardware version string: the existing `hardware_version_string` option is validated in __init__.
    hw_string = (
        config.get("hardware_version_string") or default_hardware_version_string()
    )
    cg.add(
        info.set_hardware_version_string(
            _truncate(hw_string, 64, "hardware_version_string")
        )
    )

    # This is the only place that sets CONFIG_APP_PROJECT_VER(_FROM_CONFIG); the numeric
    # `software_version` (ota.py) only adds CONFIG_DEVICE_SOFTWARE_VERSION_NUMBER.
    # Software version string comes from the app descriptor (esp_app_desc_t::version), which is
    # what CHIP's ESP32 ConfigurationManager reports. Without this it is the ESP-IDF default
    # (git describe or "1"), not the firmware version.
    sw_string = _truncate(
        config.get(CONF_SOFTWARE_VERSION_STRING, default_software_version(config)),
        SOFTWARE_VERSION_STRING_MAX,
        "software_version_string",
    )
    add_idf_sdkconfig_option("CONFIG_APP_PROJECT_VER_FROM_CONFIG", True)
    add_idf_sdkconfig_option("CONFIG_APP_PROJECT_VER", sw_string)
    cg.add(info.set_software_version_string(sw_string))

    # The custom provider reports SerialNumber and HardwareVersionString.
    add_idf_sdkconfig_option("CONFIG_CUSTOM_DEVICE_INSTANCE_INFO_PROVIDER", True)

    if CONF_MAX_FABRICS in config:
        add_idf_sdkconfig_option("CONFIG_MAX_FABRICS", config[CONF_MAX_FABRICS])
        cg.add(info.set_max_fabrics(config[CONF_MAX_FABRICS]))


async def setup_identify_triggers(var, config) -> None:
    for conf in config.get(CONF_ON_IDENTIFY, []):
        endpoint = conf.get(CONF_ENDPOINT, 0xFFFF)
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var, endpoint)
        cg.add(matter_ns.register_identify_trigger(trigger))
        await automation.build_automation(
            trigger,
            [
                (cg.uint16, "endpoint_id"),
                (cg.bool_, "active"),
                (cg.uint16, "identify_time"),
                (cg.uint8, "effect"),
            ],
            conf,
        )
