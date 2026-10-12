"""BLE commissioning support for esphome-matter.

BLE commissioning is selected automatically: when none of ``wifi``, ``openthread``
or ``ethernet`` is configured, the device has no network to be commissioned on, so
CHIP's CHIPoBLE rendezvous is compiled in (NimBLE) and the device advertises over
BLE. The optional ``ble_commissioning`` key only *asserts* that choice so a
forgotten ``wifi:`` block is a config error instead of a silently different
build. See docs/ble-commissioning.md.
"""

import logging

import esphome.config_validation as cv
from esphome.components.esp32 import add_idf_sdkconfig_option
from esphome.core import CORE
from esphome.types import ConfigType

_LOGGER = logging.getLogger(__name__)

CONF_BLE_COMMISSIONING = "ble_commissioning"

# esp32_ble (and everything built on it) is Bluedroid-only, while the BLE
# commissioning build uses NimBLE. All of the components below load esp32_ble,
# so any of them in the config means a Bluedroid build.
_BLE_COMPONENTS = (
    "esp32_ble",
    "esp32_ble_beacon",
    "esp32_ble_client",
    "esp32_ble_server",
    "esp32_ble_tracker",
    "ble_client",
    "bluetooth_proxy",
    "esp32_improv",
    "improv_ble",
)

# Components that reboot the device when no client/broker is connected. In the
# BLE commissioning build ESPHome has no network interface, so
# network::is_connected() is always false and these would reboot the device in a
# loop (api default: every 15 min).
_REBOOT_TIMEOUT_COMPONENTS = ("api", "mqtt")
_CONF_REBOOT_TIMEOUT = "reboot_timeout"


def has_ip_network(loaded_integrations=None) -> bool:
    """True if ESPHome owns an IP interface that Matter can run on."""
    loaded = (
        CORE.loaded_integrations
        if loaded_integrations is None
        else loaded_integrations
    )
    return bool({"wifi", "openthread", "ethernet"} & set(loaded))


def validate_ble_commissioning(config: ConfigType, full_config: ConfigType) -> None:
    """Final-validate checks and fix-ups; called from matter's final validation."""
    requested = config.get(CONF_BLE_COMMISSIONING)
    ip_network = has_ip_network(set(full_config))

    if requested is True and ip_network:
        raise cv.Invalid(
            "ble_commissioning: true cannot be combined with `wifi`, `openthread` or "
            "`ethernet`. With any of them configured the device is commissioned "
            "on-network (the QR code advertises on-network rendezvous) and the BLE "
            "stack is not built. Remove them to commission over BLE, or remove "
            "`ble_commissioning`."
        )
    if requested is False and not ip_network:
        raise cv.Invalid(
            "ble_commissioning: false requires `wifi`, `openthread` or `ethernet`: "
            "without one of them, BLE is the only way to commission the device."
        )
    if ip_network:
        return

    conflicts = sorted(set(_BLE_COMPONENTS) & set(full_config))
    if conflicts:
        raise cv.Invalid(
            "BLE commissioning (no `wifi`/`openthread`/`ethernet` configured) cannot be "
            "combined with ESPHome BLE components: "
            + ", ".join(conflicts)
            + ". Matter's BLE commissioning uses the NimBLE host stack while "
            "ESPHome's BLE components (esp32_ble, bluetooth_proxy, improv_ble, "
            "...) only support Bluedroid; the two cannot be built into one "
            "firmware. Add `wifi:` (or `openthread:`) so Matter commissions "
            "on-network, or remove the BLE components."
        )

    for domain in _REBOOT_TIMEOUT_COMPONENTS:
        conf = full_config.get(domain)
        if not isinstance(conf, dict):
            continue
        current = conf.get(_CONF_REBOOT_TIMEOUT)
        if current is not None and current.total_milliseconds == 0:
            continue
        _LOGGER.info(
            "matter: BLE commissioning has no ESPHome network interface; setting "
            "`%s: reboot_timeout: 0s` (it was %s, which would reboot the device "
            "in a loop).",
            domain,
            current if current is not None else "15min",
        )
        conf[_CONF_REBOOT_TIMEOUT] = cv.positive_time_period_milliseconds("0s")

    unreachable = [d for d in ("api", "ota", "web_server") if d in full_config]
    if unreachable:
        _LOGGER.warning(
            "matter: this is a BLE commissioning build (no wifi/openthread), so "
            "ESPHome has no network interface and %s will not be reachable. Use "
            "`wifi:` or `openthread:` if you need them.",
            ", ".join(unreachable),
        )


def add_ble_sdkconfig() -> None:
    # CHIP picks the BLE host stack from these; ESPHome's esp32_ble is Bluedroid.
    add_idf_sdkconfig_option("CONFIG_BT_ENABLED", True)
    add_idf_sdkconfig_option("CONFIG_BT_BLUEDROID_ENABLED", False)
    add_idf_sdkconfig_option("CONFIG_BT_NIMBLE_ENABLED", True)
    add_idf_sdkconfig_option("CONFIG_BT_NIMBLE_ENABLE_CONN_REATTEMPT", False)
    # Deinitialize BLE after a successful commissioning, reclaim its memory
    # (esp_bt_mem_release, so BLE cannot restart until the next reboot) and do not
    # start it on boot once Wi-Fi/Thread credentials are provisioned. Default y in
    # CHIP's Kconfig; pinned so a changed default cannot silently keep the BLE
    # stack's RAM allocated.
    add_idf_sdkconfig_option("CONFIG_USE_BLE_ONLY_FOR_COMMISSIONING", True)
