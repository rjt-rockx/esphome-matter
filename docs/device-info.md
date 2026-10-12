# Device info, Identify and fabric limit

This page covers the options that control how a controller (Home Assistant, Apple Home, Google Home, chip-tool) sees the node itself, plus the Identify automation and the fabric limit.

```yaml
matter:
  node_label: Living room plug      # default: friendly_name, else the ESPHome name
  serial_number: "PLUG-0001"        # default: device MAC address as 12 hex digits
  software_version_string: "1.4.2"  # default: esphome.project.version, else the ESPHome version
  hardware_version_string: "rev-b"  # default: chip variant, e.g. ESP32C6
  unique_id: "living-room-plug"     # default: random, generated once and stored
  max_fabrics: 8                    # default: 5
  on_identify:
    - then:
        - logger.log: "identify!"
```

## Basic Information options

| Option | Attribute | Default | Limit |
| --- | --- | --- | --- |
| `node_label` | NodeLabel | `friendly_name`, else the ESPHome `name` | 32 characters |
| `serial_number` | SerialNumber | ESPHome device MAC as uppercase hex (`AABBCCDDEEFF`), the same address the native API reports, also on chips without Wi-Fi such as the ESP32-H2 | 32 characters |
| `software_version_string` | SoftwareVersionString | `esphome.project.version`, else the ESPHome version | 31 characters |
| `hardware_version_string` | HardwareVersionString | the chip variant (`ESP32C6`) | 64 characters |
| `unique_id` | UniqueID | random, persisted in flash | 32 characters |

`vendor_name`, `product_name`, `vendor_id`, `product_id` and `hardware_version` are documented in the main README and are unchanged.

### Why there is no more `TEST_VERSION`

CHIP reports `TEST_VERSION` as the hardware version string, and a placeholder serial number, unless a provider supplies real values. esphome-matter registers a small `DeviceInstanceInfoProvider` (it extends the stock ESP32 one) that returns the configured or derived `serial_number` and `hardware_version_string`. To enable it the component sets `CONFIG_CUSTOM_DEVICE_INSTANCE_INFO_PROVIDER=y`. All other values (vendor, product, part number, ...) still come from the stock provider.

How each option reaches the controller:

- `serial_number` and `hardware_version_string`: read by the cluster from the registered `DeviceInstanceInfoProvider`.
- `software_version_string`: read from the firmware's application descriptor (`CONFIG_APP_PROJECT_VER`).
- `unique_id`: read from the CHIP configuration store, where the component writes it right after Matter starts.
- `node_label`: see the notes below.

The software version string is read by CHIP from the application descriptor, so the component sets `CONFIG_APP_PROJECT_VER` (with `CONFIG_APP_PROJECT_VER_FROM_CONFIG`) to the value above. Set `esphome: project:` with a `version` to see your firmware version in the controller's device page.

### Notes

- `node_label` is only the default. esp-matter 1.6 implements Basic Information with CHIP's code-driven cluster, which reads NodeLabel from CHIP's attribute persistence store and not from the esp-matter node config, so the component installs a thin wrapper around that store that answers "no value stored yet" with your `node_label`. When a controller renames the node the new label is stored in flash, kept across reboots, and wins over the YAML value from then on. Until a controller has set one, changing `node_label` in YAML and re-flashing changes what controllers see. A factory reset erases the controller's label and the YAML default applies again. The component never writes a label on the controller's behalf.
- `unique_id` should be stable for the lifetime of the device. Changing it makes some controllers treat the node as a different device.
- Controllers cache these values at commissioning time. Re-commission (or wait for the controller to refresh) to see changes.

## on_identify

Controllers send an Identify command (for example the "Identify" button in Home Assistant, or when you add a device to Apple Home) so you can find the physical device. `on_identify` runs an automation for these requests. Triggers run on the ESPHome main loop, so any action is safe to use.

Variables available in the automation:

| Variable | Type | Meaning |
| --- | --- | --- |
| `endpoint_id` | `uint16_t` | the endpoint that is identifying |
| `active` | `bool` | `true` when identification starts (or an effect is requested), `false` when it stops |
| `identify_time` | `uint16_t` | seconds the controller requested, 0 on stop and on effect requests |
| `effect` | `uint8_t` | Identify effect (`EffectIdentifierEnum`: 0 blink, 1 breathe, 2 okay, 11 channel change, 254 finish effect, 255 stop effect), or 255 when this is not an effect request |

The value 255 is used for both "stop effect" and "not an effect request". A stop effect request arrives with `active` true and `identify_time` 0, which is how to tell it apart from the start of an identification (`identify_time` greater than 0) and from the end (`active` false).

`on_identify` is a list. Add `endpoint:` to a list entry to run it for one endpoint only; without it the entry runs for every endpoint.

```yaml
matter:
  on_identify:
    - endpoint: 1
      then:
        - if:
            condition:
              lambda: "return active;"
            then:
              - light.turn_on:
                  id: status_led
                  effect: Identify
            else:
              - light.turn_off: status_led
```

See [identify-status-led.yaml](../examples/identify-status-led.yaml) for a complete example.

The Matter Identify cluster stops identification by itself when `identify_time` runs out, which triggers the `active: false` call. You do not need a timer in your automation.

Identify is only available on endpoints whose device type includes the Identify cluster, which is the case for most of them.

## Fabric limit

Matter lets a device join several fabrics (ecosystems) at once, for example Home Assistant and Apple Home together. `max_fabrics` sets the maximum, from 5 (the Matter minimum and the default) up to 254. It maps to `CONFIG_MAX_FABRICS`.

Each fabric needs RAM and flash for its credentials, sessions and access control entries. Raising the limit above what you need makes the firmware larger and uses more memory on small chips, so leave it at the default unless you have a reason. The number is fixed when the firmware is built; changing it needs a re-flash.

Note that Google Home, Apple Home, Alexa and Home Assistant each use one fabric, so the default of 5 is enough for most households. A fabric is also used briefly while an ecosystem commissions the device.
