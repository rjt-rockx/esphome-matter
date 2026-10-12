# Status

What works, what is waiting to be merged, and how well each piece has been checked. Last updated 2026-10-12.

**Read this first.** esphome-matter is experimental. **No feature in this fork has been tested at runtime on real hardware against a real controller**, and there is no automated runtime test yet. The Wi-Fi/Thread/commissioning core and the light, sensor and binary-sensor bindings come from the upstream project, where users reported them working; this fork did not re-verify them. Treat every row below as "should work, please report".

## How to read the columns

**Status**
- *Merged*: on the integration branch (`claude/tender-rubin-39rgtk`) as of this writing.
- *Merging*: finished on a local branch, not yet on the integration branch (`wave-lighting`).
- *Pending*: on a separate wave branch that is not merged yet (`wave-fan-hvac`, `wave-delegates`, `wave-sensors`).
- *Not started*: no code.

**Verification** (strongest level reached; none has runtime testing)
- *Unit + conformance*: schema, code generation and snapshot unit tests (`tests/unit`), and the Matter conformance checker ([conformance.md](conformance.md)) on test configs.
- *CI compiled*: the firmware is also built in CI. Only these configs are compiled: Wi-Fi (a dimmer switch client and a temperature sensor), Thread, power source and closure. The C++ of the other bindings (lights, switch, fan, climate, cover, lock, valve, generic switch) is only compiled when someone builds locally.
- *Runtime: none yet*: applies to everything.

**Controller notes** come from a desk-research survey in the maintainer's research notes (`controller-support-matrix.md`, dated 2026-10-12). Most Google rows rest on a 2024 source, Hubitat is unverified, and nothing was tested by us. "Unverified" means no source was found, not "unsupported".

## Feature matrix

| Domain | YAML surface | Status | Verification | Controller notes |
|---|---|---|---|---|
| Lights (on/off, dimmable, color temperature, extended color) | `light` with `expose:`, or `on_off_light`, `dimmable_light`, `color_temperature_light`, `extended_color_light`; [lights.md](lights.md) | Merged; lighting update *merging* (`wave-lighting`) | Unit + conformance; the CI-compiled Wi-Fi config has no light | Supported by Apple, Google, Alexa, SmartThings and Home Assistant. Color loop is not portable. |
| Switches and plugs | `switch` -> `on_off_plug_in_unit` (or `on_off_light`); [switch.md](switch.md) | Merged | Unit + conformance | Plug-in Unit accepted by Apple, Google, Alexa, SmartThings, Home Assistant. Also used as a stand-in for valves. |
| Sensors (temperature, humidity, light, pressure, flow) | `sensor` with `expose:`, or `temperature_sensor`, `humidity_sensor`, `light_sensor`, `pressure_sensor`, `flow_sensor`; [sensors.md](sensors.md) | Merged | Unit + conformance; temperature sensor is in the CI-compiled Wi-Fi config | Temperature, humidity, light: broadly supported. Pressure and flow: Google and SmartThings only in the survey; Apple does not list them. |
| Air quality and soil sensors | `air_quality_sensor` (CO, CO2, NO2, ozone, PM, VOC, formaldehyde, radon), `soil_sensor`; [sensors.md](sensors.md#air-quality-concentration) | Merged; air quality extras *pending* (`wave-sensors`) | Unit + conformance | Air quality: Google, Alexa, SmartThings yes; Apple and Home Assistant unverified. Apple rejects Soil Sensor accessories. |
| Binary sensors: contact, occupancy | `binary_sensor` -> `contact_sensor`, `occupancy_sensor`; [binary-sensors.md](binary-sensors.md) | Merged; occupancy hold and boolean state config *pending* (`wave-sensors`) | Unit + conformance | Supported by all surveyed controllers. |
| Binary sensors: water leak, freeze, rain | `water_leak_detector`, `water_freeze_detector`, `rain_sensor` | Merged | Unit + conformance | Apple and Google do not list leak detectors. SmartThings and Home Assistant do. |
| Binary sensors: smoke, carbon monoxide | `smoke_co_alarm` (smoke and CO share one endpoint) | Merged; extras *pending* (`wave-sensors`) | Unit + conformance | Google, Alexa, SmartThings, Home Assistant yes. Apple: not listed. ON means a critical alarm. |
| Buttons and events (generic switch) | `generic_switch` with `matter.send_switch_event`; `button` by id; [generic-switch.md](generic-switch.md) | Merged | Unit + conformance | Apple: momentary only. Alexa: single event source. Home Assistant: multi-press. The `event` entity to Generic Switch binding is a known gap. |
| Fans | `fan` -> `fan`; [fan.md](fan.md) | Merged; wind, rock and climate-fan updates *pending* (`wave-fan-hvac`) | Unit + conformance | Google, Alexa, SmartThings, Home Assistant yes. Apple support for fans is unverified. |
| Climate: thermostat and room air conditioner | `climate` -> `thermostat` / `room_air_conditioner`; [climate.md](climate.md) | Merged; HVAC updates *pending* (`wave-fan-hvac`) | Unit + conformance | Thermostat and room AC supported by Apple, Alexa, SmartThings, Home Assistant. Thermostat presets (Matter 1.4) have no confirmed controller support. |
| Covers (window covering) | `cover` -> `window_covering`; [cover.md](cover.md) | Merged | Unit + conformance | Apple, Google, Alexa, SmartThings, Home Assistant yes. |
| Closures (garage door, gate) | `closure` device type, or `closure_compat: true` on a window covering; [cover.md](cover.md) | Merged | Unit + conformance; CI compiled (closure config) | No surveyed controller documents the Closure device type. Window covering is the practical fallback. |
| Locks | `lock` -> `door_lock`; [lock-valve.md](lock-valve.md) | Merged | Unit + conformance | Supported by Apple, Google, Alexa, SmartThings, Home Assistant. Credentials and PIN users: not implemented. |
| Valves | `valve` -> `water_valve`; [lock-valve.md](lock-valve.md) | Merged | Unit + conformance | **Apple Home rejects Water Valve accessories.** Home Assistant and SmartThings accept them. |
| Power source (battery, wired) | `battery:` / `wired:` on an endpoint; [power.md](power.md) | Merged | Unit + conformance; CI compiled (power-source config) | Battery shown by Home Assistant; other controllers unverified. |
| Metering (electrical sensor) | power, energy, voltage, current sensors attach to a load or stand alone; topology node, tree or set; [power.md](power.md) | Merged | Unit + conformance; CI compiled | Google lists electric meters; Apple and Alexa do not; Home Assistant cannot create these. Use with care. |
| Appliances: modes and operational state | Mode Select and Operational State delegates | **Pending** (`wave-delegates`) | Not on the integration branch | Google, Alexa and SmartThings cover some appliances; Home Assistant partial. |
| Refrigerator and other appliance types | `refrigerator` | **Pending** (`wave-sensors`); other appliance types *not started* | Not on the integration branch | Google: not officially supported. SmartThings lists it. |
| Bindings (a button controlling another Matter device) | `dimmer_switch`, `on_off_light_switch`, `color_dimmer_switch`, `matter.send_command`; [binding.md](binding.md), [actions.md](actions.md) | Merged; shared binding framework in progress (`wave3-binding-core`) | Unit + conformance; CI compiled (dimmer switch in the Wi-Fi config) | Apple requires On/Off client and server for switch types. A command can take a few seconds. |
| Bridges (many devices behind one bridge) | `aggregator` / `bridged_node` device types exist; no entity-driven bridge | **Not started** (bridge tasks T26, T27) | Generated device type list only | Apple, Alexa, SmartThings and Home Assistant handle bridges as a category. |
| Commissioning, passcode persistence, factory reset | `matter:` options, `matter.factory_reset`, `matter.open_commissioning_window`; [commissioning.md](commissioning.md) | Merged | Unit tests (passcode persistence); CI compiled | See [ecosystems.md](ecosystems.md). Upstream users reported pairing with Home Assistant, Apple Home, an Alexa Echo and IKEA. |
| BLE commissioning | automatic without wifi/openthread/ethernet; [ble-commissioning.md](ble-commissioning.md) | Merged | Unit + conformance | Upstream reported it broken; untested here. |
| Ethernet transport | `ethernet:` plus `matter:`; [ethernet.md](ethernet.md) | Merged | Validated config only | Reported working on ESP32-P4 by a contributor; not verified here. |
| Matter OTA | `matter: ota:`; [matter-ota.md](matter-ota.md) | Merged | Unit + config | Untested. |

Where a row says both "Merged" and "pending", the base feature is on the integration branch and the named wave adds more on top of it.

## Known gaps

Open work as of the latest plan and repro reports:

- No runtime testing: the test harness scenarios (`tools/harness/scenarios/`) have never run against real firmware, and no QEMU boot run exists. Only the harness self-test (simulated device) runs in CI.
- Most binding C++ is not compiled in CI (see Verification above).
- A config can validate and still fail at boot: if an endpoint cannot be built, the log shows "Failed to build endpoint" and the Matter component is marked failed, without saying which cluster or feature caused it.
- Echo suppression (a controller write being pushed back to the controller) is implemented in several different ways across domains; wave merges must keep each guard.
- Sensor updates use one scheduled work item per reading with no coalescing; if the Matter event queue is full an update is dropped and the final value can be lost.
- Not implemented yet: the `event` entity to Generic Switch binding, `as: doorbell` in `expose:`, the `status_led` and `pairing` behaviours, `matter.remove_fabric` and a controllers sensor, entity-addressed actions, a bridge, and footprint measurement.
- Tags and labels: the duplicate-endpoint check ignores tag labels, the tag error does not mention esp-matter's limit of 3 tags, the FixedLabel key is fixed to `name`, and there is no warning for a "structure only, no delegate" endpoint.
- Deferred: EVSE and device energy management, Service Area, thermostat remote sensing and presets, ICD (low-power) devices.
- Reconnect after a restart can take up to a minute ([troubleshooting-network.md](troubleshooting-network.md)).
- The default test vendor ID means every controller treats the device as uncertified; production use needs a CSA vendor ID and your own attestation certificates.
- Mapping gaps: `select`, `number`, `text_sensor` and `alarm_control_panel` have no Matter mapping.

Planned direction is in [ROADMAP.md](ROADMAP.md) and [design/DIRECTION.md](design/DIRECTION.md).
