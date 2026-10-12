# FAQ

Short answers. Details are in [commissioning.md](commissioning.md), [ecosystems.md](ecosystems.md), [troubleshooting.md](troubleshooting.md) and [ROADMAP.md](ROADMAP.md).

### Do I need Home Assistant?

No. Matter devices work with any Matter controller (Apple Home, Google Home, Alexa, SmartThings, IKEA, Home Assistant). Home Assistant is simply the controller that exposes the most device types and gives you the most control. See [ecosystems.md](ecosystems.md).

### Can I use Matter and the ESPHome native API at the same time?

Yes, `api:` can stay in your YAML and the two use different ports. They are independent connections: the same entity could appear in Home Assistant twice (once through the ESPHome integration, once through Matter). Pick one path per entity unless you want both. Memory use with both enabled has not been measured.

### Which chips and transports are supported?

ESP32 targets built with the ESP-IDF framework (Arduino is not supported). ESP32-C3, C5, C6, S3 and H2 have been reported to work. ESP32 classic is Wi-Fi only and is not validated in CI. Matter over Wi-Fi needs `wifi:`; Matter over Thread needs `openthread:` and a Thread-capable chip (C5, C6, H2) plus ESPHome 2026.6.0 or newer. Ethernet is not supported yet.

### Why does my app say "uncertified accessory"?

The device uses the Matter test vendor ID `0xFFF1`, product ID `0x8000` and a development certificate. Certified devices need a vendor ID from the Connectivity Standards Alliance (CSA) and their own certificate chain, which is not practical for hobby projects. Each ecosystem has a workaround; see [ecosystems.md](ecosystems.md).

### Is it legal / allowed to use the test IDs?

The test IDs are intended for development. Do not sell devices using them. For a commercial product you need your own CSA vendor ID, certification and a device attestation certificate chain.

### Home Assistant says pairing failed.

Enable test-net DCL in the Matter Server app (or start matterjs-server with `--enable-test-net-dcl=true`), and use the "Commission existing device" option. See [ecosystems.md](ecosystems.md#home-assistant).

### Why is BLE commissioning not used?

ESPHome devices already know their Wi-Fi or Thread credentials from the YAML, so the device is commissioned over the network. BLE commissioning is currently broken and cannot be combined with `api`. Do not add `esp32_ble*` components to the same YAML.

### How do I add the device to a second ecosystem?

Use "Share" / "Turn on pairing mode" in the first ecosystem to get a temporary code. See [multi-admin](ecosystems.md#multi-admin-sharing-a-device-with-more-than-one-ecosystem).

### How do I factory reset the Matter part?

Add a button with the `matter.factory_reset` action (see the README example config). It removes all fabrics and reopens the commissioning window after the reboot. The device shows a new setup code (see [commissioning](commissioning.md#passcode-storage)), unless `persist_passcode` or `passcode:` is set.

### How do I update the firmware?

Use ESPHome's normal OTA (`ota:` platform `esphome`). Matter OTA is not implemented. Fabrics survive OTA updates.

### The device takes a long time to come back after a reboot.

This is a known issue; it can take up to about a minute. See [troubleshooting.md](troubleshooting.md#slow-reconnect-after-restart).

### The firmware doesn't fit / the build is huge.

See [flash size](troubleshooting.md#flash-size-especially-on-4-mb-boards) and [compile time](troubleshooting.md#compile-time-and-disk-space).

### Which ESPHome entities are supported?

See the [feature matrix](../README.md#feature-matrix) and [device-types.md](device-types.md). Lights and sensors work; others are being added. Anything not listed there is untested.

### Do I need an `external_components` entry?

Yes, until (if ever) this lands in ESPHome itself; see the [roadmap](ROADMAP.md#3-upstreaming-into-esphome-the-zigbee-precedent). Point it at this repository:

```yaml
external_components:
  - source: github://rjt-rockx/esphome-matter@main
```

### What is the relationship to DavidvtWout/esphome-matter?

This project is derived from [DavidvtWout/esphome-matter](https://github.com/DavidvtWout/esphome-matter) (Apache-2.0) with modifications, and aims to contribute improvements back. See `NOTICE`.

### Where do I report problems or compatibility results?

On this repository's issue tracker, or in the upstream [ecosystem compatibility discussion](https://github.com/DavidvtWout/esphome-matter/discussions/44). See [what to report](ecosystems.md#what-to-report).
