# esphome-matter

![GitHub stars](https://img.shields.io/github/stars/rjt-rockx/esphome-matter)
![GitHub forks](https://img.shields.io/github/forks/rjt-rockx/esphome-matter)
![GitHub watchers](https://img.shields.io/github/watchers/rjt-rockx/esphome-matter)

ESPHome external component adding native Matter support (Matter 1.6, via Espressif's esp-matter) to ESP32 devices. Add a `matter:` block, map ESPHome entities to Matter device types, and pair the device with Apple Home, Google Home, Alexa, SmartThings, IKEA or Home Assistant. No bridge required.

Derived from [DavidvtWout/esphome-matter](https://github.com/DavidvtWout/esphome-matter) (Apache-2.0), with modifications. See [NOTICE](NOTICE). Credit for the original component, the Wi-Fi and Thread support, the ZAP-generated data model and the lights implementation goes to [DavidvtWout](https://github.com/DavidvtWout) and the upstream contributors.

> **Status: early development.** Matter over Wi-Fi and over Thread work, devices can be commissioned, and lights, sensors and binary sensors are working. Many other device types can be created and driven with `on_attribute` triggers and `set_attribute` actions. Known issues: reconnection after a restart can take up to a minute, and the YAML schema may still change and break existing configurations. Anything marked "untested" in the docs has not been verified on hardware.

# Quickstart

Minimal config: a Wi-Fi dimmable light on an ESP32-C6.

```yaml
esphome:
  name: matter-light
esp32:
  variant: ESP32C6
  framework:
    type: esp-idf
external_components:
  - source: github://rjt-rockx/esphome-matter@main
logger:
network:
  enable_ipv6: true
wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password
output:
  - platform: ledc
    id: led_out
    pin: GPIO15
light:
  - platform: monochromatic
    id: my_light
    name: "Light"
    output: led_out
    restore_mode: ALWAYS_OFF  # Matter restores the power-on state
matter:
  endpoints:
    1:
      dimmable_light:
        light_id: my_light
```

1. Flash it (`esphome run matter-light.yaml`). On ESPHome versions before 2026.9.0, add `toolchain: platformio` under `esp32:`.
2. Watch the log. On every boot the device prints a setup code:

   ```
   [C][matter]:   SetupQRCode: MT:Y.K904QI14-O992WI00
   [C][matter]:   QR URL: https://project-chip.github.io/connectedhomeip/qrcode.html?data=MT:Y.K904QI14-O992WI00
   [C][matter]:   Manual pairing code: 32552014321
   [C][matter]:   Commissioning window: open
   ```

3. Open the QR URL and scan the code in your controller, or enter the manual pairing code. The commissioning window is open for 15 minutes after boot, and only while the device is not part of a fabric.
4. Check the [ecosystem guide](docs/ecosystems.md) for the extra step your controller needs (the device uses a test vendor ID, so it shows up as uncertified).

For Thread, replace `wifi:` with `openthread:` (needs ESPHome 2026.6.0 or newer and a Thread border router).

# Feature matrix

How each ESPHome domain maps to a Matter device type. Status: **Supported** (bound to an ESPHome entity and working), **Partial** (works with known gaps or untested corners), **In progress** (being implemented, a documentation page exists or is being written), **Planned**, **Not planned** (no good Matter equivalent). Anything other than Supported should be treated as untested on real controllers.

| ESPHome domain | Matter device type(s) | Status | Docs |
|---|---|---|---|
| `light` | On/Off, Dimmable, Color Temperature, Extended Color Light | Supported (untested on hardware) | [lights.md](docs/lights.md) |
| `sensor`: temperature, humidity, illuminance | Temperature, Humidity, Light Sensor | Supported | [device-types.md](docs/device-types.md#sensors) |
| `sensor`: pressure, flow | Pressure, Flow Sensor | Partial (unit conversion implemented, untested on controllers) | [sensors.md](docs/sensors.md#units) |
| `sensor`: CO, CO2, NO2, ozone, PM, VOC, formaldehyde, radon | Air Quality Sensor (incl. the AirQuality level) | Partial (MeasurementUnit follows the sensor unit, untested on controllers) | [sensors.md](docs/sensors.md#air-quality-concentration) |
| `sensor`: soil moisture | Soil Sensor | Partial (untested) | [device-types.md](docs/device-types.md#sensors) |
| `sensor`: power, energy, voltage, current | Electrical Sensor | Partial (untested on controllers) | [sensors.md](docs/sensors.md#electrical-measurements) |
| `sensor`: battery | Power Source | Partial (battery level only, untested on controllers) | [sensors.md](docs/sensors.md#battery) |
| `binary_sensor`: contact, rain | Contact Sensor, Rain Sensor | Supported | [binary-sensors.md](docs/binary-sensors.md) |
| `binary_sensor`: occupancy | Occupancy Sensor (HoldTime) | Supported (hold time untested on controllers) | [binary-sensors.md](docs/binary-sensors.md#hold-time) |
| `binary_sensor`: water leak, freeze | Water Leak / Freeze Detector (alarms, sensitivity) | Partial (untested) | [binary-sensors.md](docs/binary-sensors.md#alarms-and-sensitivity-boolean-state-configuration) |
| `binary_sensor`: smoke, carbon monoxide | Smoke CO Alarm (fault, end of service, mute, self test) | Partial (untested; ON = Critical alarm) | [binary-sensors.md](docs/binary-sensors.md#smoke-and-co-alarm) |
| `switch` | On/Off Plug-in Unit | Supported (untested on hardware) | [switch.md](docs/switch.md) |
| `cover` | Window Covering | Supported (untested on hardware) | [cover.md](docs/cover.md) |
| `fan` | Fan | Supported (untested on hardware) | [fan.md](docs/fan.md) |
| `lock` | Door Lock | Supported (untested on hardware) | [lock-valve.md](docs/lock-valve.md) |
| `valve` | Water Valve | Supported (untested on hardware) | [lock-valve.md](docs/lock-valve.md) |
| `button`, `event` | Generic Switch | Supported (untested on hardware) | [generic-switch.md](docs/generic-switch.md) |
| `climate` | Thermostat / Room Air Conditioner | Supported (untested on hardware) | [climate.md](docs/climate.md) |
| Physical buttons controlling other Matter devices | On/Off Light Switch, Dimmer Switch, Color Dimmer Switch (bindings) | Supported (a command can take a few seconds) | [binding.md](docs/binding.md), [actions.md](docs/actions.md) |
| `select` | Mode Select, and the mode clusters of appliances (washer, dishwasher, RVC, fridge, water heater, ...) | Supported (untested on hardware) | [delegates.md](docs/delegates.md) |
| `select`, `text_sensor`, actions | Operational State, RVC Operational State (washer, dishwasher, robot vacuum) | Supported (untested on hardware) | [delegates.md](docs/delegates.md) |
| `number`, `text_sensor`, `alarm_control_panel` | none | Not planned (no good mapping) | [ROADMAP.md](docs/ROADMAP.md) |

Other Matter device types can be created under an endpoint but have no entity binding. See [device-types.md](docs/device-types.md#untested--unsupported).

# Supported hardware

Only `ESP32` targets built with the **ESP-IDF** framework are supported. The Arduino framework is not.

- **Matter-over-Wi-Fi**: configure `wifi`. The device announces itself over mDNS and is commissioned on the network.
- **Matter-over-Thread**: configure `openthread`. Needs ESPHome **2026.6.0** or newer and a Thread border router.
- **Matter-over-Ethernet**: experimental. If `ethernet` is configured, Matter uses the ESPHome Ethernet interface, see [docs/ethernet.md](docs/ethernet.md). Based on work by [romanstanek](https://github.com/romanstanek/esphome-matter). Untested on hardware.
- **BLE commissioning**: automatic when no `wifi`, `openthread` or `ethernet` component is configured (Matter then owns the radio; ESPHome `api`/`ota` are unreachable). Untested on hardware. See [docs/ble-commissioning.md](docs/ble-commissioning.md).

`ESP32-C3`, `ESP32-C5`, `ESP32-C6`, `ESP32-S3` and `ESP32-H2` have been reported to work. Classic `ESP32` is Wi-Fi only and not validated. Flash is tight on 4 MB boards, see [troubleshooting](docs/troubleshooting.md#flash-size-especially-on-4-mb-boards).

# [Commissioning](docs/commissioning.md)

Once a device has joined a fabric, the commissioning window is not opened on restarts anymore. Use "Share" / "pairing mode" in your first controller to get a temporary code for a second one (multi-admin, up to 5 fabrics by default). The `matter.factory_reset` action wipes all fabrics and reopens the window.

Ecosystem specific steps, in short:

- **Home Assistant**: enable test-net DCL (`matterjs-server --enable-test-net-dcl=true`, or the "Enable test-net DCL usage" box in the Matter Server app) and use "Commission existing device".
- **Apple Home**: "Uncertified Accessory" warning, tap "Add Anyway".
- **Google Home**: needs a Google Developer Console project for the test vendor ID (follow the [Tasmota guide](https://tasmota.github.io/docs/Matter-with-Google/); untested here).
- **Alexa**, **IKEA Home smart**: no special steps reported. IKEA only supports a few device types.

Details and known quirks: [docs/ecosystems.md](docs/ecosystems.md) and [docs/commissioning.md](docs/commissioning.md). Please report compatibility results in the [upstream discussion](https://github.com/DavidvtWout/esphome-matter/discussions/44) or on this repository.

<details>
<summary>Larger example config (dimmer buttons, temperature sensor, light, factory reset)</summary>

```yaml
esphome:
  name: matter-device

esp32:
  # The default toolchain in ESPHome is `esp-idf`. esphome-matter supports this
  # toolchain starting at ESPHome version 2026.9.0. If you're still using an older
  # version you need to set toolchain to platformio:
  # toolchain: platformio
  variant: ESP32C6 # Set to your variant
  framework:
    type: esp-idf

external_components:
  - source: github://rjt-rockx/esphome-matter@main

logger:

api:

network:
  enable_ipv6: true

# Either:
wifi: ...
# Or:
openthread: ...

matter:
  # vendor_name: defaults to ESPHome
  # vendor_id: defaults to 0xFFF1 (test vendor)
  # product_name: defaults to esphome.name (truncated to 32 characters)
  # product_id: defaults to 0x8000 (test product)
  # hardware_version: defaults to 0
  # hardware_version_string: defaults to TEST_VERSION

  endpoints:
    1:
      id: dimmer_endpoint # The id is optional. Actions can also refer to the numerical endpoint id directly.
      dimmer_switch:
    2:
      temperature_sensor:
        temperature: internal_temp
    3:
      on_off_light:
        light_id: user_led

# The two buttons are configured to be triggered when the GPIO pin is pulled down to GND.
binary_sensor:
  - name: "Button up"
    platform: gpio
    pin:
      number: GPIO0
      mode:
        pullup: true
        input: true
      inverted: true
    on_click:
      matter.send_command:
        path: dimmer_endpoint.on_off.on
    on_press:
      # Pressing up can turn on a light. If you don't want this, remove "_with_on_off".
      matter.send_command:
        path: dimmer_endpoint.level_control.move_with_on_off
        arguments:
          move_mode: up
          rate: 20%/s
    on_release:
      matter.send_command:
        path: dimmer_endpoint.level_control.stop_with_on_off
  - name: "Button down"
    id: button_down
    platform: gpio
    pin:
      number: GPIO1
      mode:
        pullup: true
        input: true
      inverted: true
    on_click:
      matter.send_command:
        path: dimmer_endpoint.on_off.off
    on_press:
      # Pressing down dims to lowest brightness but doesn't turn off
      matter.send_command:
        path: dimmer_endpoint.level_control.move
        arguments:
          move_mode: down
          rate: 20%/s
    on_release:
      matter.send_command:
        path: dimmer_endpoint.level_control.stop

sensor:
  - platform: internal_temperature
    name: "Internal Temperature"
    id: internal_temp

output:
  # On a Seeed Studio XIAO ESP32-C6, the GPIO15 pin is wired to the user LED. Pick
  # the correct pin for your board or remove the `output` and `light` sections.
  - platform: gpio
    pin:
      number: GPIO15
      inverted: true
    id: user_led_pin

light:
  - platform: binary
    name: "User LED"
    output: user_led_pin
    id: user_led
    internal: true

# A Matter factory reset wipes all fabrics and re-opens the commissioning window.
button:
  - platform: template
    name: "Matter Factory Reset"
    on_press:
      - matter.factory_reset:
    disabled_by_default: True
```

More information about endpoints and a full list of supported device types can be found in [docs/endpoints.md](./docs/endpoints.md)

</details>

# Documentation

- [docs/ecosystems.md](docs/ecosystems.md): pairing guide per ecosystem, multi-admin, quirks
- [docs/commissioning.md](docs/commissioning.md): commissioning, persistence, fabrics
- [docs/device-types.md](docs/device-types.md): device types and entity mapping
- [docs/endpoints.md](docs/endpoints.md): endpoints and clusters
- [docs/lights.md](docs/lights.md): lights
- [docs/matter-ota.md](docs/matter-ota.md): Matter OTA requestor and `.ota` image tool
- Per domain (being written): [switch](docs/switch.md), [cover](docs/cover.md), [fan](docs/fan.md), [lock and valve](docs/lock-valve.md), [binary sensors](docs/binary-sensors.md), [sensors](docs/sensors.md)
- [docs/delegates.md](docs/delegates.md): delegate-driven clusters: mode clusters, Mode Select, Operational State (appliances)
- [docs/actions.md](docs/actions.md), [docs/binding.md](docs/binding.md), [docs/clusters.md](docs/clusters.md), [docs/generated/commands.md](docs/generated/commands.md): actions, binding, cluster and command reference
- [docs/troubleshooting.md](docs/troubleshooting.md): IPv6/mDNS, reconnect, build size, Thread
- [docs/faq.md](docs/faq.md): frequently asked questions
- [docs/ROADMAP.md](docs/ROADMAP.md): community demand, maintainer direction, upstreaming, priorities
- [docs/dev/thread-debugging.md](docs/dev/thread-debugging.md): debugging Thread
- Examples: [examples/](examples/), for example [extended-color-light.yaml](examples/extended-color-light.yaml) and [all-sensors.yaml](examples/all-sensors.yaml)

# Current limitations

- Based on [Espressif's SDK for Matter (esp-matter)](https://components.espressif.com/components/espressif/esp_matter/): features not supported there cannot be supported here.
- BLE commissioning is currently broken, and could not be combined with the `api` component anyway because of limitations in ESPHome's `network` component.
- The device uses test credentials by default (vendor ID `0xFFF1`, product ID `0x8000`). It is not Matter-certified; production devices need a CSA vendor ID and their own attestation certificates.
- The YAML schema may still change.

# See also

- [Espressif's SDK for Matter (esp-matter) GitHub repo](https://github.com/espressif/esp-matter)
  - [esp-matter on the ESP Component Registry](https://components.espressif.com/components/espressif/esp_matter/)
  - [esp-matter programming guide](https://docs.espressif.com/projects/esp-matter/en/latest/esp32/)
- [connectedhomeip](https://github.com/DavidvtWout/connectedhomeip)
- [Matter specification (CSA)](https://csa-iot.org/developer-resource/specifications-download-request/)
- [CSA source code implementations for the Matter project](https://github.com/project-chip)

# License

MIT for original content, Apache-2.0 for the code derived from DavidvtWout/esphome-matter. See [LICENSE](LICENSE), [LICENSE-APACHE](LICENSE-APACHE) and [NOTICE](NOTICE).
