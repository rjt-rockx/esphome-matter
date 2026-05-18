# esphome-matter

Native Matter support for ESPHome as an external component.

**Status:** Phase 0 spike. Not on a board yet. The build path is wired up;
end-to-end commissioning is the next milestone.

## What this is

Drop a `matter:` block into an ESPHome YAML, declare a `platform: matter`
entity, and the device commissions into Apple Home, Google Home, or Alexa
over BLE + Wi-Fi. No bridge, no Home Assistant required, no manual ZAP /
cluster authoring.

```yaml
external_components:
  - source: github://rjt-rockx/esphome-matter
    components: [matter]

esp32:
  board: esp32-c6-devkitc-1
  framework:
    type: esp-idf

matter:

switch:
  - platform: matter
    matter_id: matter1
    name: "Desk Plug"
```

## Hardware

v0 targets ESP32-C6 (Wi-Fi) and ESP32-S3. ESP32-H2 (Thread-only) and Thread
support land in v1.

## Roadmap

- **v0.1** `switch:` (On/Off Plug-in Unit)
- **v0.2** `light:` (on/off, dimmable, CT, RGB)
- **v0.3** `binary_sensor:` (contact, occupancy, water leak, generic switch)
- **v0.4** `sensor:` (temperature, humidity, illuminance, pressure)
- **v1** covers, fans, thermostats, locks, valves; ESP32-H2 + Thread; multi-endpoint

The slow incremental rollout mirrors ESPHome's Zigbee component
([PR #11553](https://github.com/esphome/esphome/pull/11553)): ship one entity
type, prove it, expand.

## Concepts (one line each)

- **Commissioning**: pairing the device into an ecosystem the first time. Done over BLE, then handed off to Wi-Fi / Thread.
- **Fabric**: a single ecosystem (Apple Home, Google Home, etc.). One device can join many fabrics simultaneously (multi-admin).
- **Discriminator**: 12-bit advertised ID used during commissioning to pick the right device.
- **Passcode**: 8-digit numeric secret entered (or scanned via QR) during commissioning.
- **DAC / PAI / PAA**: the attestation cert chain proving the device is genuine. v0 ships a CSA test DAC; production needs a Connectivity Standards Alliance VID.
- **Endpoint**: a Matter address (1..65534) under one node, hosting clusters.
- **Cluster**: a typed bundle of attributes + commands (On/Off, Level Control, etc.). What HA calls "entity types," Matter calls clusters.
- **Identify cluster**: tells the device to blink so the user can find it physically during pairing.
- **OTA Requestor**: Matter's own firmware update channel, distinct from ESPHome HTTP OTA. Both coexist.
- **NodeLabel**: per-fabric friendly name. A device can be "Desk Plug" in Apple Home and "Outlet" in Google Home simultaneously.
- **ACL**: per-fabric access control. Matter manages it; not surfaced in YAML.

## Footguns v0 deliberately rejects

- **BLE coexistence.** Any `esp32_ble_*` component in the same YAML is a compile error. esp-matter tears down BLE post-commissioning and the two stacks fight over the controller.
- **Arduino framework.** ESP-IDF only. Matter needs IDF v5.5.4.
- **NVS partition wipe.** ESPHome erases NVS on first-mount failure. We carve a separate `nvs_chip` partition for Matter so fabric data survives.
- **Sensors without `device_class`.** Codegen errors rather than silently miscategorizing the entity in Apple Home.

## Credentials

By default the device pairs with the CSA test DAC (VID `0xFFF2`, PID
`0x8001`). Apple Home shows "Uncertified Accessory" with an "Add Anyway"
button. Fine for development. For production you'll need a Connectivity
Standards Alliance VID and your own DAC chain: see `scripts/mfg-tool-wrapper.sh`.

## Building the spike

```sh
# Build:
esphome compile examples/01-onoff-plug-c6.yaml

# Flash firmware:
esphome upload examples/01-onoff-plug-c6.yaml

# Flash the factory NVS partition (one-time per device):
./scripts/mfg-tool-wrapper.sh
./scripts/flash-factory.sh out/factory/fff2_8001/<serial>-partition.bin /dev/ttyUSB0
```

`dump_config` prints the QR code URL and manual pairing code on boot.

## What works today

Nothing yet. This repo is the Phase 0 skeleton. First commissioning attempt
is the next milestone; everything below it is unverified.

## License

MIT. See `LICENSE`.
