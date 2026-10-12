# Examples

The [examples/](../examples/) directory holds complete ESPHome configs. They are validated with `esphome config` in CI (ESPHome 2026.6.0, 2026.7.4 and dev), so they parse and generate code, but **none has been run on hardware yet**. Adjust pins and board settings, add a `secrets.yaml`, and see [getting-started.md](getting-started.md) for flashing and pairing. Examples use `source: github://rjt-rockx/esphome-matter@main`; to try a local checkout use `source: /path/to/esphome-matter/components`.

| Example | What it shows | Read more |
|---|---|---|
| [smart-plug.yaml](../examples/smart-plug.yaml) | A relay plug with a physical button | [switch.md](switch.md) |
| [multi-relay.yaml](../examples/multi-relay.yaml) | Several relays with labels | [composition.md](composition.md) |
| [power-strip-meters.yaml](../examples/power-strip-meters.yaml) | Outlets with power meters and a mains voltage reading | [power.md](power.md) |
| [dimmer-light-package.yaml](../examples/dimmer-light-package.yaml) | How to build a dimmable light from a remote package with substitutions (the built-in `light_id` mapping is preferred) | [lights.md](lights.md) |
| [extended-color-light.yaml](../examples/extended-color-light.yaml) | RGB / color temperature light | [lights.md](lights.md) |
| [ceiling-fan.yaml](../examples/ceiling-fan.yaml) | Fan plus a light on one device | [fan.md](fan.md) |
| [blinds.yaml](../examples/blinds.yaml) | Window covering | [cover.md](cover.md) |
| [garage-door.yaml](../examples/garage-door.yaml) | Garage door as a covering with a contact sensor | [cover.md](cover.md) |
| [thermostat.yaml](../examples/thermostat.yaml) | Thermostat | [climate.md](climate.md) |
| [lock-valve.yaml](../examples/lock-valve.yaml) | Door lock and water valve | [lock-valve.md](lock-valve.md) |
| [door-and-motion.yaml](../examples/door-and-motion.yaml) | Contact and occupancy sensors | [binary-sensors.md](binary-sensors.md) |
| [all-sensors.yaml](../examples/all-sensors.yaml) | Environmental, air quality and electrical sensors with a battery | [sensors.md](sensors.md) |
| [wireless-button.yaml](../examples/wireless-button.yaml) | Stateless button (generic switch) | [generic-switch.md](generic-switch.md) |
| [esp32-dimmer-button.yaml](../examples/esp32-dimmer-button.yaml) | A dimmer switch (over Thread) that controls another Matter light through a binding | [binding.md](binding.md) |
| [commissioning.yaml](../examples/commissioning.yaml) | Pairing text sensors and commissioning triggers | [commissioning.md](commissioning.md) |
| [identify-status-led.yaml](../examples/identify-status-led.yaml) | Identify action driving a status LED | [device-info.md](device-info.md) |
| [matter-ota.yaml](../examples/matter-ota.yaml) | Matter OTA requestor | [matter-ota.md](matter-ota.md) |
| [ble-commissioning.yaml](../examples/ble-commissioning.yaml) | BLE commissioning without Wi-Fi | [ble-commissioning.md](ble-commissioning.md) |
| [esp32-wt32-eth01-ethernet.yaml](../examples/esp32-wt32-eth01-ethernet.yaml) | Ethernet transport on a WT32-ETH01 | [ethernet.md](ethernet.md) |

More configs used by CI live in `tests/configs/`; they are less polished but cover more of the YAML surface (for example `esp32-expose.yaml` shows globs, pins, `exclude:` and mixing with hand-written endpoints).

## Tools to check a config

- `esphome config your.yaml` prints the lowered endpoint tree.
- `python -m tools.conformance your.yaml` checks the generated data model against the Matter spec ([conformance.md](conformance.md)).
- `tools/harness` is a scriptable Matter controller for commissioning and poking a real device ([README](../tools/harness/README.md)).
