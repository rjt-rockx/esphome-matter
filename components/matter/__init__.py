"""ESPHome Matter component.

Drop ``matter:`` into an ESPHome YAML and opted-in entities auto-expose as
Matter device types. ESP-IDF only; ESP32-C6 / S3 supported in v0.

See plan in repo README for the full v0 scope and roadmap.
"""

import logging
from typing import Any

from esphome import automation
import esphome.codegen as cg
from esphome.components.esp32 import (
    add_idf_component,
    add_idf_sdkconfig_option,
    only_on_variant,
)
from esphome.components.esp32.const import (
    KEY_ESP32,
    KEY_EXTRA_BUILD_FILES,
    VARIANT_ESP32C6,
    VARIANT_ESP32S3,
)
import esphome.config_validation as cv
from esphome.const import CONF_ID, CONF_INTERNAL
from esphome.core import CORE, HexInt
from esphome.types import ConfigType

from .const import (
    CONF_DEVICE_TYPE,
    CONF_DISCRIMINATOR,
    CONF_ENDPOINT,
    CONF_MATTER,
    CONF_MATTER_ID,
    CONF_ON_COMMISSION,
    CONF_PASSCODE,
    CONF_PRODUCT_ID,
    CONF_VENDOR_ID,
    DEFAULT_DISCRIMINATOR,
    DEFAULT_PASSCODE,
    DEFAULT_PRODUCT_ID,
    DEFAULT_VENDOR_ID,
    DEVICE_TYPE_NAMES,
    KEY_ENDPOINTS,
    KEY_MATTER,
    MatterComponent,
    matter_ns,
)

_LOGGER = logging.getLogger(__name__)

CODEOWNERS = ["@rjt-rockx"]
AUTO_LOAD = []
DEPENDENCIES = ["esp32"]


def _validate_passcode(value: int) -> int:
    # Matter spec rejects a small list of trivially-guessable passcodes.
    invalid = {
        0,
        11111111,
        22222222,
        33333333,
        44444444,
        55555555,
        66666666,
        77777777,
        88888888,
        99999999,
        12345678,
        87654321,
    }
    value = cv.int_range(min=1, max=99999998)(value)
    if value in invalid:
        raise cv.Invalid(f"{value} is a Matter-reserved invalid passcode")
    return value


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(CONF_ID): cv.declare_id(MatterComponent),
            cv.Optional(CONF_VENDOR_ID, default=DEFAULT_VENDOR_ID): cv.hex_uint16_t,
            cv.Optional(CONF_PRODUCT_ID, default=DEFAULT_PRODUCT_ID): cv.hex_uint16_t,
            cv.Optional(CONF_DISCRIMINATOR, default=DEFAULT_DISCRIMINATOR): cv.int_range(
                min=0, max=0xFFF
            ),
            cv.Optional(CONF_PASSCODE, default=DEFAULT_PASSCODE): _validate_passcode,
            cv.Optional(CONF_ON_COMMISSION): automation.validate_automation(
                single=False
            ),
        }
    ).extend(cv.COMPONENT_SCHEMA),
    cv.only_on_esp32,
    cv.only_with_framework("esp-idf"),
    only_on_variant(supported=[VARIANT_ESP32C6, VARIANT_ESP32S3]),
)


# Per-entity opt-in schema. Entity platforms `.extend(MATTER_*_SCHEMA)`.
def _entity_schema(default_type: str | None = None) -> cv.Schema:
    return cv.Schema(
        {
            cv.Optional(CONF_MATTER): cv.Any(
                cv.boolean,
                cv.Schema(
                    {
                        cv.Optional(CONF_ENDPOINT): cv.int_range(min=1, max=65534),
                        cv.Optional(CONF_DEVICE_TYPE, default=default_type): cv.enum(
                            DEVICE_TYPE_NAMES, lower=True
                        )
                        if default_type
                        else cv.enum(DEVICE_TYPE_NAMES, lower=True),
                    }
                ),
            ),
        }
    )


MATTER_SWITCH_SCHEMA = _entity_schema(default_type="on_off_plug_in_unit")
MATTER_LIGHT_SCHEMA = _entity_schema()
MATTER_BINARY_SENSOR_SCHEMA = _entity_schema()
MATTER_SENSOR_SCHEMA = _entity_schema()


def _normalize_matter_block(config: ConfigType) -> ConfigType | None:
    """Convert `matter: true` to a normalized dict; return None when opted out."""
    block = config.get(CONF_MATTER)
    if block is None or block is False:
        return None
    if block is True:
        return {}
    return block


def consume_endpoint(
    config: ConfigType, *, domain: str, default_type: str | None
) -> ConfigType:
    """Allocate an endpoint id for an opted-in entity at validate time."""
    if "matter" not in CORE.loaded_integrations or config.get(CONF_INTERNAL):
        return config
    block = _normalize_matter_block(config)
    if block is None:
        return config

    device_type = block.get(CONF_DEVICE_TYPE) or default_type
    if device_type is None:
        raise cv.Invalid(
            f"{domain}: matter type cannot be inferred; specify "
            f"`matter: {{type: ...}}` (one of {sorted(DEVICE_TYPE_NAMES)})"
        )

    data: dict[str, Any] = CORE.data.setdefault(KEY_MATTER, {})
    endpoints: list[dict[str, Any]] = data.setdefault(KEY_ENDPOINTS, [])

    explicit = block.get(CONF_ENDPOINT)
    if explicit is None:
        # Endpoint 0 is the Matter Root Node. Entities start at 1.
        used = {ep["endpoint"] for ep in endpoints}
        nxt = 1
        while nxt in used:
            nxt += 1
        explicit = nxt

    endpoints.append(
        {
            "endpoint": explicit,
            "domain": domain,
            "device_type": device_type,
            "config_id": config[CONF_ID],
        }
    )
    config[CONF_MATTER_ID] = explicit
    return config


def validate_switch(config: ConfigType) -> ConfigType:
    return consume_endpoint(
        config, domain="switch", default_type="on_off_plug_in_unit"
    )


def validate_light(config: ConfigType) -> ConfigType:
    return consume_endpoint(config, domain="light", default_type=None)


def validate_binary_sensor(config: ConfigType) -> ConfigType:
    return consume_endpoint(config, domain="binary_sensor", default_type=None)


def validate_sensor(config: ConfigType) -> ConfigType:
    return consume_endpoint(config, domain="sensor", default_type=None)


# Final validation: hard-error when BLE coexistence would brick the build.
def _final_validate_no_ble(_config: ConfigType) -> None:
    forbidden = {
        "esp32_ble",
        "esp32_ble_beacon",
        "esp32_ble_server",
        "esp32_ble_tracker",
        "ble_client",
        "esp32_improv",
    }
    conflicts = forbidden & set(CORE.loaded_integrations)
    if conflicts:
        raise cv.Invalid(
            "matter cannot coexist with BLE components in v0: "
            + ", ".join(sorted(conflicts))
            + ". esp-matter tears down BLE after commissioning and the two stacks "
            "fight over the controller. Remove the BLE component or wait for v1."
        )


FINAL_VALIDATE_SCHEMA = _final_validate_no_ble


def _add_partition_table() -> None:
    from pathlib import Path

    csv = Path(__file__).parent / "partitions.csv"
    CORE.data.setdefault(KEY_ESP32, {}).setdefault(KEY_EXTRA_BUILD_FILES, {})[
        "partitions.csv"
    ] = {"path": str(csv)}


def _add_sdkconfig() -> None:
    add_idf_sdkconfig_option("CONFIG_PARTITION_TABLE_CUSTOM", True)
    add_idf_sdkconfig_option("CONFIG_PARTITION_TABLE_FILENAME", "partitions.csv")
    add_idf_sdkconfig_option("CONFIG_PARTITION_TABLE_OFFSET", 0xC000)
    add_idf_sdkconfig_option("CONFIG_ESPTOOLPY_FLASHSIZE_4MB", True)

    add_idf_sdkconfig_option("CONFIG_BT_ENABLED", True)
    add_idf_sdkconfig_option("CONFIG_BT_NIMBLE_ENABLED", True)
    add_idf_sdkconfig_option("CONFIG_BT_NIMBLE_ENABLE_CONN_REATTEMPT", False)

    add_idf_sdkconfig_option("CONFIG_LWIP_IPV6_AUTOCONFIG", True)
    add_idf_sdkconfig_option("CONFIG_LWIP_IPV6_NUM_ADDRESSES", 6)
    add_idf_sdkconfig_option("CONFIG_LWIP_HOOK_IP6_ROUTE_DEFAULT", True)
    add_idf_sdkconfig_option("CONFIG_LWIP_HOOK_ND6_GET_GW_DEFAULT", True)

    add_idf_sdkconfig_option("CONFIG_MBEDTLS_HKDF_C", True)
    add_idf_sdkconfig_option("CONFIG_ENABLE_OTA_REQUESTOR", True)
    add_idf_sdkconfig_option("CONFIG_ESP_WIFI_SOFTAP_SUPPORT", False)
    add_idf_sdkconfig_option("CONFIG_ESP_MATTER_MAX_DYNAMIC_ENDPOINT_COUNT", 16)


async def to_code(config: ConfigType) -> None:
    cg.add_define("USE_MATTER")
    _add_partition_table()
    _add_sdkconfig()
    add_idf_component(
        name="espressif/esp_matter",
        ref="1.5.0",
    )

    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    cg.add(var.set_vendor_id(config[CONF_VENDOR_ID]))
    cg.add(var.set_product_id(config[CONF_PRODUCT_ID]))
    cg.add(var.set_discriminator(config[CONF_DISCRIMINATOR]))
    cg.add(var.set_passcode(config[CONF_PASSCODE]))

    for conf in config.get(CONF_ON_COMMISSION, []):
        await automation.build_automation(
            var.get_commission_trigger(), [(cg.bool_, "x")], conf
        )


ACTION_SCHEMA = automation.maybe_simple_id(
    {cv.GenerateID(): cv.use_id(MatterComponent)}
)

FactoryResetAction = matter_ns.class_(
    "FactoryResetAction", automation.Action, cg.Parented.template(MatterComponent)
)
OpenCommissioningWindowAction = matter_ns.class_(
    "OpenCommissioningWindowAction",
    automation.Action,
    cg.Parented.template(MatterComponent),
)


@automation.register_action("matter.factory_reset", FactoryResetAction, ACTION_SCHEMA)
async def factory_reset_action(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    return var


@automation.register_action(
    "matter.open_commissioning_window",
    OpenCommissioningWindowAction,
    ACTION_SCHEMA,
)
async def open_commissioning_window_action(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    return var
