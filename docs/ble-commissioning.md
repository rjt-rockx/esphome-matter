# BLE commissioning

Most Matter devices are commissioned over Bluetooth LE: the phone finds the device by BLE, sets up an encrypted
session (PASE) with the QR/manual code, and hands over Wi-Fi or Thread credentials. esphome-matter supports this as a
build mode for devices that have **no `wifi:` and no `openthread:` block**.

## Usage

There is nothing to enable. If neither `wifi` nor `openthread` is configured, the firmware is built for BLE
commissioning and the QR code advertises BLE rendezvous:

```yaml
network:
  enable_ipv6: true

matter:
  ble_commissioning: true # optional
  endpoints:
    1:
      temperature_sensor: temperature_id
```

`ble_commissioning` is optional and only *asserts* the automatic choice, so a forgotten `wifi:` block fails the config
instead of silently building a different firmware:

| YAML | Result |
| --- | --- |
| no wifi/openthread, key omitted or `true` | BLE commissioning build |
| no wifi/openthread, `ble_commissioning: false` | config error (there would be no way to commission) |
| wifi or openthread, key omitted or `false` | on-network commissioning (see [commissioning.md](commissioning.md)) |
| wifi or openthread, `ble_commissioning: true` | config error |

Commission with the SetupQRCode printed in the log (the same code is shown in [commissioning.md](commissioning.md)),
using any Matter commissioner. The commissioner provisions Wi-Fi or Thread credentials; CHIP's NetworkCommissioning
cluster applies them and the device then continues on-network.

## What is different in this mode

- **Matter owns the radio.** ESPHome has no network interface (`wifi`/`openthread` absent), so ESPHome's
  `network::is_connected()` is always false. `api`, `ota`, `web_server` and mDNS-based ESPHome features are **not
  reachable** even after the device has joined a network through Matter. The config validator warns about this. If you
  need them, configure `wifi:` or `openthread:` and commission on-network instead.
- **No reboot loop.** `api` (and `mqtt`) reboot the device when no client is connected for `reboot_timeout` (15 min by
  default), which would restart a BLE-mode device forever. The validator sets `reboot_timeout: 0s` for these
  components automatically and logs an INFO line when it does.
- **BLE is released after commissioning.** `CONFIG_USE_BLE_ONLY_FOR_COMMISSIONING=y` is set: once the device holds a
  fabric, CHIP shuts down the BLE stack, and on later boots it does not start BLE while the device already has a
  fabric. After a factory reset (`matter.factory_reset`) BLE advertising comes back.
- **Diagnostics.** `[matter.ble]` log lines show BLE advertising start/stop and connection events.

## Coexistence with ESPHome BLE components (not supported)

`esp32_ble`, `esp32_ble_tracker`, `esp32_ble_server`, `esp32_ble_beacon`, `esp32_ble_client`, `ble_client`,
`bluetooth_proxy`, `esp32_improv` and `improv_ble` together with a BLE commissioning build are rejected at config time.

Why: CHIP in this mode uses the **NimBLE** host stack, while ESPHome's BLE components are written against **Bluedroid**
and cannot compile against NimBLE; the two host stacks are mutually exclusive in one firmware. CHIP also supports
Bluedroid, but its Bluedroid backend registers global GAP/GATTS callbacks
(`esp_ble_gap_register_callback`, `esp_ble_gatts_register_callback`) that would replace the ones `esp32_ble` registers.
A shim that forwards these into `esp32_ble`'s `add_gap_event_handler`/`add_gatts_event_handler` (via the linker
`--wrap`, as `matter_wifi_stack.cpp` does for Wi-Fi) is feasible on paper but was judged too risky without hardware
testing, so it is not implemented.

With `wifi:` or `openthread:` configured, the CHIP BLE stack is not built (`CONFIG_ENABLE_CHIPOBLE=n`) and ESPHome BLE
components are not blocked by this component. That combination has not been build-tested here.

## Matter-provisioned Wi-Fi in ESPHome

Wi-Fi credentials delivered over BLE are consumed by CHIP's own Wi-Fi station driver; ESPHome's `wifi` component is not
involved and cannot be fed with them in this version. Consequently the "BLE for PASE only, then continue on the
`wifi:` network from YAML" hybrid is also **unsupported** (`ble_commissioning: true` together with `wifi:` is a config
error). Commission such devices on-network.

## Verification status

Verified: `esphome config` / `esphome compile --only-generate` for `tests/configs/esp32-ble-commissioning.yaml`
(generated `sdkconfig`: NimBLE on, Bluedroid off, `CONFIG_ENABLE_CHIPOBLE=y`, `CONFIG_USE_BLE_ONLY_FOR_COMMISSIONING=y`;
generated `api::set_reboot_timeout(0)`); the conflict and `ble_commissioning` mismatch errors; wifi/thread test configs
still validate. Not verified (no firmware build or hardware run): that the firmware compiles and links, that BLE
advertising and commissioning succeed with a real controller, that BLE memory is actually freed, and the on-network
handover after Matter-provisioned Wi-Fi/Thread.
