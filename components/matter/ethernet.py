"""Matter-over-Ethernet support.

ESPHome's `ethernet:` component owns the PHY/MAC driver and the `ETH_DEF` esp_netif.
Matter only needs to use that interface for on-network commissioning and DNS-SD, so
the SDK's own Ethernet driver (which would create a second MAC/netif) is replaced by
a thin adapter, see `esphome_matter_ethernet/CMakeLists.txt`.

Based on the Matter-over-Ethernet work of romanstanek
(romanstanek/esphome-matter on GitHub, branch `ethernet-support`).
"""

from pathlib import Path

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components.esp32 import add_idf_component, add_idf_sdkconfig_option


def final_validate(full_config: dict) -> None:
    ethernet_config = full_config.get("ethernet")
    if ethernet_config is None:
        return
    if not ethernet_config.get("enable_on_boot", True):
        raise cv.Invalid(
            "Matter requires `ethernet.enable_on_boot: true`. Delayed Ethernet startup "
            "is not supported because the Network Commissioning driver is initialized "
            "when Matter starts."
        )


def to_code() -> None:
    """Configure the build for Matter over the ESPHome Ethernet interface.

    Called from matter.to_code only when the `ethernet` integration is loaded.
    """
    # CONFIG_ENABLE_ETHERNET_TELEMETRY is the name connectedhomeip uses for the option that
    # enables CHIP_DEVICE_CONFIG_ENABLE_ETHERNET (ConnectivityManager Ethernet + driver).
    add_idf_sdkconfig_option("CONFIG_ENABLE_ETHERNET_TELEMETRY", True)
    add_idf_sdkconfig_option("CONFIG_LWIP_IPV6_NUM_ADDRESSES", 6)
    # Adapt the SDK's Ethernet driver and DNS-SD backend in a separate IDF target. Its
    # CMakeLists.txt follows the CONFIG_ENABLE_WIFI_* selection set by matter/__init__.py.
    add_idf_component(
        name="esphome_matter_ethernet",
        path=str(Path(__file__).parent / "esphome_matter_ethernet"),
    )
    # The SDK copies IP_EVENT_STA_GOT_IP data but not IP_EVENT_ETH_GOT_IP data;
    # matter_ethernet.cpp preserves the payload so the Ethernet IPv4 address reaches
    # CHIP's ConnectivityManager (OnEthernetIPv4AddressAvailable -> DNS-SD republish).
    cg.add_build_flag(
        "-Wl,--wrap=_ZN4chip11DeviceLayer19PlatformManagerImpl20HandleESPSystemEventEPvPKclS2_"
    )
