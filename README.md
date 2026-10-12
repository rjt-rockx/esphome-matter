# esphome-matter

An ESPHome external component that makes ESPHome entities show up as **Matter** devices. Add a `matter:` block, name the entities to publish with `expose:`, flash an ESP32, and pair it with Apple Home, Google Home, Alexa, SmartThings or Home Assistant. No bridge or hub is needed beyond what your Matter setup already has.

It builds on Espressif's [esp-matter](https://github.com/espressif/esp-matter) 1.6 and ESP-IDF. It is for ESPHome users who want a device to speak Matter natively (lights, plugs, sensors, covers, fans, thermostats, locks, buttons and more) and who are comfortable flashing an ESP32 and reading a log.

> **Status: experimental. Nothing in this repository has been verified on real hardware or against real controllers yet.** What is checked today: YAML schema and `esphome config` on many configs, unit tests, a Matter conformance checker, and firmware that compiles in CI. The Wi-Fi/Thread/light/sensor core is inherited from the upstream project, where it was reported to work; the rest of this fork's additions are untested at runtime. The YAML schema may still change. See [docs/status.md](docs/status.md) for the per-feature picture.

This is an independent fork of [DavidvtWout/esphome-matter](https://github.com/DavidvtWout/esphome-matter) (Apache-2.0) with modifications, see [NOTICE](NOTICE). It is not affiliated with or endorsed by ESPHome, Espressif, the Connectivity Standards Alliance or any smart-home vendor. Credit for the original component, the Wi-Fi and Thread support, the ZAP-generated data model and the lights goes to DavidvtWout and the upstream contributors.

## 30-second example

A relay and a temperature sensor as two Matter devices (ESP32-C6, Wi-Fi):

```yaml
esphome:
  name: matter-demo

esp32:
  variant: ESP32C6
  framework:
    type: esp-idf

external_components:
  - source: github://rjt-rockx/esphome-matter@main

logger:
network:
  enable_ipv6: true      # required by Matter
wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

switch:
  - platform: gpio
    id: relay            # entities you expose need an explicit id
    name: Relay
    pin: GPIO4

sensor:
  - platform: internal_temperature
    id: chip_temp
    name: Chip temperature

matter:
  expose: [relay, chip_temp]   # plug-in unit + temperature sensor, endpoints numbered for you
```

Flash it, watch the log for the `SetupQRCode` / `Manual pairing code` lines, and add the device in your controller. The full walkthrough is in [docs/getting-started.md](docs/getting-started.md).

`esphome config` prints the explicit `endpoints:` tree that `expose:` lowered to, so you can copy it and take full control later ([docs/expose.md](docs/expose.md)).

## Requirements

| | |
|---|---|
| Chip | ESP32 family with the **ESP-IDF** framework (Arduino is not supported). CI compiles for ESP32-C6 (Wi-Fi, Thread, closure and power configs), ESP32-H2 (Thread), ESP32-S3 (Wi-Fi) and ESP32-C3 (Wi-Fi). Other variants, including classic ESP32 and C5, are not covered by CI; classic ESP32 is Wi-Fi only. On 4 MB flash the firmware is tight, see [troubleshooting](docs/troubleshooting.md#flash-size-especially-on-4-mb-boards). |
| Transports | **Wi-Fi** (`wifi:`), **Thread** (`openthread:`, needs a Thread border router), **Ethernet** (`ethernet:`, experimental, [docs](docs/ethernet.md)), or **BLE commissioning** when none of those is configured ([docs](docs/ble-commissioning.md)). |
| ESPHome | CI validates configs and runs unit tests against ESPHome **2026.6.0**, **2026.7.4** and **dev**, and compiles firmware on dev and a release. The native ESP-IDF toolchain (the ESPHome default) is supported from **2026.9.0**; on older versions add `toolchain: platformio` under `esp32:` (`tests/validate.sh` applies the same rule). Thread needs 2026.6.0 or newer. |
| Network | IPv6 enabled (`network: enable_ipv6: true`) and a network that passes mDNS and IPv6 multicast. |

## Install

Add the component to your ESPHome YAML, as in the example above:

```yaml
external_components:
  - source: github://rjt-rockx/esphome-matter@main
```

Pin a commit instead of `main` if you want a stable build. To use a local checkout, use `source: /path/to/esphome-matter/components` (see `tests/configs/` for examples).

## Feature matrix

Lights, switches and plugs, sensors (temperature, humidity, pressure, light, flow, air quality, electrical metering), binary sensors (contact, occupancy, leak, freeze, smoke/CO), buttons and events (generic switch), fans, climate (thermostat and room air conditioner), covers, locks, valves, and power sources, plus physical buttons that control other Matter devices through bindings. The full matrix with status and controller notes is in [docs/status.md](docs/status.md).

Controllers differ a lot in what they accept (for example Apple Home rejects Water Valve accessories, and Apple/Google do not show every sensor type). Check the controller notes in [docs/status.md](docs/status.md) and [docs/ecosystems.md](docs/ecosystems.md) before you pick a device type.

## Where to go next

- [docs/getting-started.md](docs/getting-started.md): install, flash, commission, add to Apple, Google or Home Assistant.
- [docs/index.md](docs/index.md): map of every document.
- [docs/status.md](docs/status.md): what works, what is pending, what is verified, known gaps.
- [docs/examples.md](docs/examples.md) and [examples/](examples/): ready-made configs (blinds, thermostat, smart plug, multi-relay, power strip with meters, garage door, ...).
- [docs/expose.md](docs/expose.md): the `expose:` shortcut; [docs/endpoints.md](docs/endpoints.md): hand-written endpoints.
- [docs/ecosystems.md](docs/ecosystems.md): pairing steps and quirks per controller.
- [docs/troubleshooting.md](docs/troubleshooting.md) and [docs/faq.md](docs/faq.md).

## Tools

- `tools/conformance`: checks that a config produces a spec-conformant Matter data model, without hardware ([docs/conformance.md](docs/conformance.md)).
- `tools/harness`: a scriptable Matter controller (matter.js) for commissioning and testing devices from a terminal ([README](tools/harness/README.md)).
- `tests/validate.sh` and `tools/validate-configs` run `esphome config` over the test configs and examples.

## Limitations

- Based on esp-matter: features it does not support cannot be supported here.
- The device uses test credentials by default (vendor ID `0xFFF1`, product ID `0x8000`). It is not Matter-certified, so controllers show an "uncertified" warning; Google Home needs extra setup for the test vendor ID.
- Reconnection after a restart can take up to a minute ([docs/troubleshooting-network.md](docs/troubleshooting-network.md)).
- `select`, `number`, `text_sensor` and `alarm_control_panel` have no good Matter mapping and are not exposed.

## See also

- [esp-matter](https://github.com/espressif/esp-matter) and its [programming guide](https://docs.espressif.com/projects/esp-matter/en/latest/esp32/)
- [Matter specification (CSA)](https://csa-iot.org/developer-resource/specifications-download-request/) and [project-chip](https://github.com/project-chip)

## License

MIT for original content, Apache-2.0 for code derived from DavidvtWout/esphome-matter. See [LICENSE](LICENSE), [LICENSE-APACHE](LICENSE-APACHE) and [NOTICE](NOTICE).
