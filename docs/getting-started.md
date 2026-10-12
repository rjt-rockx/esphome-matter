# Getting started

This walks you from an empty YAML to a device paired in your smart home app. It assumes you can already build and flash an ESP32 with ESPHome. **Nothing here has been verified on hardware in this repository yet**; if a step does not match what you see, please report it. See [status.md](status.md).

## 1. What you need

- An ESP32 board that supports Matter transports you want: ESP32-C6 (Wi-Fi or Thread), ESP32-H2 (Thread), ESP32-S3 or ESP32-C3 (Wi-Fi). Other variants are not covered by CI.
- ESPHome **2026.6.0 or newer**. From **2026.9.0** the default native ESP-IDF toolchain works. On older versions add `toolchain: platformio` under `esp32:`.
- A controller that speaks Matter (Apple Home, Google Home, Alexa, SmartThings, Home Assistant with the Matter Server app) on the same network, and for Thread a Thread border router.
- IPv6 and mDNS working on your network ([troubleshooting](troubleshooting.md)).

## 2. Write the YAML

A relay and a temperature sensor over Wi-Fi:

```yaml
esphome:
  name: matter-demo

esp32:
  variant: ESP32C6
  framework:
    type: esp-idf
  # toolchain: platformio   # only on ESPHome older than 2026.9.0

external_components:
  - source: github://rjt-rockx/esphome-matter@main

logger:
network:
  enable_ipv6: true
wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

switch:
  - platform: gpio
    id: relay
    name: Relay
    pin: GPIO4

sensor:
  - platform: internal_temperature
    id: chip_temp
    name: Chip temperature

matter:
  expose: [relay, chip_temp]
```

Key points:

- `network: enable_ipv6: true` is required.
- `matter: expose:` lists entity ids. Only entities with an explicit `id:` can be exposed. The component picks the Matter device type from the entity (see [expose.md](expose.md#what-is-inferred)) and numbers the endpoints for you.
- Use `as:` to override a guess, for example `expose: [{id: relay, as: on_off_light}]`. Use `expose: all` or a glob such as `relay_*` for many entities.
- `matter:` with neither `expose:` nor `endpoints:` is a root node only and exposes nothing.
- Run `esphome config matter-demo.yaml` to see the explicit `endpoints:` tree the shortcut produces. For full control write `endpoints:` by hand ([endpoints.md](endpoints.md)).
- For Thread, replace `wifi:` with `openthread:` ([ESPHome docs](https://esphome.io/components/openthread/)).
- Ready-made configs are in [examples.md](examples.md).

Optional identity settings under `matter:` are `vendor_name`, `vendor_id`, `product_name`, `product_id`, `hardware_version`, `hardware_version_string`, `discriminator` and `passcode`; the defaults are the Matter test vendor and product IDs ([device-info.md](device-info.md), [commissioning.md](commissioning.md)).

## 3. Build and flash

```bash
esphome run matter-demo.yaml
```

The first build downloads ESP-IDF and esp-matter and takes a while. If flash is too small (4 MB boards) see [troubleshooting](troubleshooting.md#flash-size-especially-on-4-mb-boards).

## 4. Find the setup code

While the device has no fabric it opens a commissioning window on every boot (the same code is shown again after a power cycle). Look in the log:

```
[I][matter.commissioning]: Commissioning window open. SetupQRCode: MT:Y.K904QI14-O992WI00
[I][matter.commissioning]: QR URL: https://project-chip.github.io/connectedhomeip/qrcode.html?data=MT:Y.K904QI14-O992WI00
[I][matter.commissioning]: Manual pairing code: 32552014321
```

Open the QR URL to get a scannable image, or type the manual pairing code. You can also publish the codes as text sensors with `text_sensor: - platform: matter` ([commissioning.md](commissioning.md#setup-codes-in-home-assistant)). The code is not printed again once the device is commissioned; use your first controller's "share" feature or the `matter.open_commissioning_window` action to add a second controller.

**On-network vs BLE.** With `wifi:`, `openthread:` or `ethernet:` configured, the device is commissioned over the network because ESPHome already holds the credentials. With none of them, it is built for BLE commissioning instead ([ble-commissioning.md](ble-commissioning.md)); ESPHome `api` and `ota` are then not reachable.

## 5. Add it to a controller

The device uses a test vendor ID and is not certified, so each controller shows a warning or needs a setting. Details and quirks are in [ecosystems.md](ecosystems.md).

- **Home Assistant**: enable "Enable test-net DCL usage" in the Matter Server app (or start matterjs-server with `--enable-test-net-dcl=true`), then use "Commission existing device".
- **Apple Home**: Add Accessory, scan the code, tap "Add Anyway" on the uncertified accessory warning. Apple does not show every device type ([status.md](status.md)).
- **Google Home**: needs a Google Developer Console project for the test vendor ID; untested here ([ecosystems.md](ecosystems.md)).
- **Alexa**, **SmartThings**, **IKEA Home smart**: see [ecosystems.md](ecosystems.md).

## 6. Day two

- Re-pairing: the `matter.factory_reset` action wipes all fabrics and reopens the commissioning window ([commissioning.md](commissioning.md)).
- Slow reconnect after a restart: [troubleshooting-network.md](troubleshooting-network.md).
- Updates: normal ESPHome OTA, or Matter OTA ([matter-ota.md](matter-ota.md)).
- Check your config against the Matter spec without hardware: [conformance.md](conformance.md).

## Troubleshooting pointers

| Problem | Where |
|---|---|
| Pairing fails or times out | [troubleshooting.md](troubleshooting.md), [ecosystems.md](ecosystems.md) |
| Device unreachable after reboot | [troubleshooting-network.md](troubleshooting-network.md) |
| Thread issues | [dev/thread-debugging.md](dev/thread-debugging.md) |
| Config error naming an id | [expose.md](expose.md#precedence-and-errors) |
| Questions | [faq.md](faq.md) |
