# Matter over Ethernet

Matter can run over the ESPHome `ethernet:` component (RMII PHYs such as LAN8720 / IP101 on ESP32 and ESP32-P4,
or SPI controllers such as W5500). **Status: experimental and not yet validated on hardware in this repository.**
The design and the SDK workarounds come from the Ethernet work of
[romanstanek](https://github.com/romanstanek/esphome-matter) (branch `ethernet-support`), who validated it on an
ESP32-P4 (Waveshare ESP32-P4-Module-DEV-KIT, IP101) with Apple Home. Upstream
[issue #9](https://github.com/DavidvtWout/esphome-matter/issues/9) and
[PR #10](https://github.com/DavidvtWout/esphome-matter/pull/10) first enabled `CONFIG_ENABLE_ETHERNET_TELEMETRY`.

## Usage

The LAN8720 example below needs a classic ESP32 or an ESP32-P4 (RMII); it is rejected on the ESP32-C6. Use an SPI PHY such as `type: W5500` on chips without RMII.

```yaml
network:
  enable_ipv6: true # required by Matter

ethernet:
  type: LAN8720
  mdc_pin: GPIO23
  mdio_pin: GPIO18
  clk:
    pin: GPIO0
    mode: CLK_OUT
  phy_addr: 1
  power_pin: GPIO16

matter:
  endpoints:
    1:
      temperature_sensor:
        temperature: internal_temp
```

See [`examples/esp32-wt32-eth01-ethernet.yaml`](../examples/esp32-wt32-eth01-ethernet.yaml) and the compile fixture
[`tests/configs/esp32-ethernet.yaml`](../tests/configs/esp32-ethernet.yaml). Commission with the setup code from the
device log (on-network rendezvous, as for Wi-Fi). Ethernet-only builds do **not** include BLE, OpenThread or the CHIP
Wi-Fi stack.

Ethernet combines with other networks: with `wifi`, Matter uses the SDK's Wi-Fi driver for commissioning and DNS-SD
runs on both interfaces. With `openthread`, DNS-SD keeps its Thread-first backend selection (compile coverage only).

Requirements:

- `network.enable_ipv6: true`.
- `ethernet.enable_on_boot` must stay `true` (rejected at config time otherwise).

## How it works

| Concern | Mechanism |
| --- | --- |
| CHIP Ethernet config | `CONFIG_ENABLE_ETHERNET_TELEMETRY` (connectedhomeip's name for `CHIP_DEVICE_CONFIG_ENABLE_ETHERNET`) set when `ethernet` is loaded |
| No BLE/Thread/Wi-Fi on Ethernet-only | `ethernet` now counts as connectivity: `CONFIG_ENABLE_CHIPOBLE`, `CONFIG_ENABLE_MATTER_OVER_THREAD` and `CONFIG_ENABLE_WIFI_STATION` are off, the QR code advertises on-network rendezvous |
| Second MAC/netif | The SDK's `ESPEthernetDriver::Init` creates its own IP101/RMII driver and netif. `esphome_matter_ethernet/CMakeLists.txt` replaces that source with `ethernet_driver.cpp`, which only checks that ESPHome's `ETH_DEF` netif exists (a `--wrap` cannot work since the vtable and `Init` share a translation unit). This also permits SPI-only targets |
| Network Commissioning cluster | The SDK's inline `GetNetworks()` reports an empty NetworkID. A derived `ESPHomeEthernetDriver` reports `ETH_DEF` and its link state, selected via a build-local copy of the SDK's commissioning integration |
| IPv4 event payload | The SDK copies `IP_EVENT_STA_GOT_IP` but not `IP_EVENT_ETH_GOT_IP` data. `matter_ethernet.cpp` wraps `PlatformManagerImpl::HandleESPSystemEvent` so `ConnectivityManager` sees the address and triggers a DNS-SD republish |
| DNS-SD | `matter_dnssd.cpp` routes Wi-Fi and Ethernet builds to CHIP's ESP32 mDNS backend. esp_matter excludes `ESP32DnssdImpl.cpp` when both Wi-Fi modes are off. The CMake adapter adds it to the SDK target for Ethernet-only builds; it then picks the `ETH_DEF` netif (`kDefaultEthernetNetifKey`) |
| IPv6 | `CONFIG_LWIP_IPV6_NUM_ADDRESSES=6`, route-info option enabled for Thread border-router routes |

All workarounds are specific to esp_matter `1.6.0~2` and are guarded by CMake checks that fail loudly if the SDK
layout changes.

## Validation checklist

Record board, PHY, ESPHome version and controller. Verify: IPv6 address and DNS-SD (`_matterc._udp`) on the Ethernet
interface, commissioning, reboot with fabric retained, cable unplug and replug, boot without a cable, factory reset.
Known log noise from the P4 tests: early saved-peer DNS-SD lookups or subscription resumption may fail before IPv6 is
routable and recover afterwards.

Compile fixtures for Ethernet-only, Wi-Fi plus Ethernet and OpenThread plus Ethernet (ESP32-H2) are good CI
candidates; the bundled SDK's Ethernet Kconfig has no ESP32-C6 GPIO range.
