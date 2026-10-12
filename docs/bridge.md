# Bridge (entities that stand for other devices)

A Matter bridge is a node that publishes *other* devices as Matter devices of their own. An ESP32 running ESPHome is a good bridge for devices that ESPHome already talks to but Matter does not: Bluetooth thermometers, ESP-NOW sensor nodes, 433 MHz or infrared gear, Modbus and serial equipment. The entities for those devices are ordinary ESPHome entities; the bridge adds the Matter structure around them.

The structure is an **Aggregator** endpoint with one **Bridged Node** endpoint per bridged device below it. Every Bridged Node carries *Bridged Device Basic Information* (BDBI): its own name, identity and a *Reachable* flag. Controllers show each Bridged Node as an accessory of its own, with its own name, room and "not responding" state.

```yaml
matter:
  bridge:
    devices:
      bedroom_thermometer:
        name: Bedroom Thermometer
        vendor_name: Xiaomi
        product_name: LYWSD03MMC
        reachable: bedroom_seen       # binary_sensor id
        battery: bedroom_battery      # sensor id: Power Source on the Bridged Node
        temperature_sensor: bedroom_temperature
        humidity_sensor: bedroom_humidity
```

The example configs are complete recipes: [bridge-ble-thermometers.yaml](../examples/bridge-ble-thermometers.yaml), [bridge-espnow-sensors.yaml](../examples/bridge-espnow-sensors.yaml) and [bridge-ir-ac.yaml](../examples/bridge-ir-ac.yaml). They are validated in CI (config and code generation) but **not run on hardware yet**; the controller behaviour below comes from research notes, not from tests.

## When to use a bridge

Use it when the entities are other devices. For the ESP32's own relay, light or sensor use the native form ([expose.md](expose.md), [endpoints.md](endpoints.md)): one ESP32 is one accessory and one room in Apple Home, and Home Assistant prefers native nodes over bridges for ESPHome devices.

| Use a bridge when | Use native endpoints when |
|---|---|
| Each entity group is a separate physical device (a thermometer in each room, a pump behind an ESP-NOW relay) | The entities are parts of the ESP32's own product (a plug with its button) |
| You want a name and a room per device | One accessory with one room is fine |
| A device can go offline on its own (battery, radio range) and the controller should say so | The ESP32 is the device |

Controller notes (from the controller research; none verified with this component):

- **Apple Home** lists bridges as a supported accessory type and shows each Bridged Node as an accessory, so rooms can differ per device. Bridge-child handling had bugs in iOS 18.5 for third-party bridges; test before relying on it. Home allows 150 accessories per bridge.
- **Alexa** supports non-Matter devices behind a Matter bridge and reportedly lists only about 50 devices per bridge. A warning is logged above that.
- **SmartThings** supports third-party Matter bridges and imports all bridged devices.
- **Google Home** has a "bridges" category; the device type is not in the published list, so treat it as partial.
- **Home Assistant** creates a device per Bridged Node (it can control bridges, and cannot act as one). Bridging ESPHome devices is discouraged when the same device could be native.
- A bridge is still **one** Matter node: one QR code, one commissioning, one set of fabrics.

## The `bridge:` block

```yaml
matter:
  bridge:
    endpoint: 1             # optional: pin the Aggregator endpoint (default: hashed)
    devices:
      <key>:                # 1 to 48 letters, digits, "_" or "-"
        name: Garage Sensor # NodeLabel, up to 32 bytes (default: the key, title-cased)
        ...
```

It is lowered into `endpoints:` while the configuration is validated, so everything that works on an endpoint works here. The lowering is logged:

```
INFO matter.bridge: 2 device(s)
  endpoint 60585: Aggregator
  endpoint 30736: Bridged Node 'Bedroom Thermometer' (bedroom_thermometer); temperature_sensor on endpoint 50033; humidity_sensor on endpoint 61119
```

`bridge:` can be combined with hand-written `endpoints:` and with `expose:`. The numbers of the other endpoints are reserved; entities that `expose:` publishes stay on the root node (outside the bridge).

### Device options

| Option | Meaning |
|---|---|
| `name` | BDBI NodeLabel (1 to 32 bytes). The name controllers show. |
| `endpoint` | Pin the Bridged Node endpoint instead of hashing it from the key. |
| `parts` | Pin child endpoints: `{humidity_sensor: 1234}`. |
| `primary` | The device type that shares the Bridged Node endpoint; the other device types get child endpoints. |
| `nested` | `true`: every device type gets a child endpoint, even a single one. `false`: one endpoint (only valid for one device type). |
| `reachable` | A `binary_sensor` id driving BDBI Reachable (see below). |
| `unique_id` | BDBI UniqueID (up to 32 bytes). Default: `<esphome name>-<key>`, hashed to 32 characters when longer. |
| `vendor_name`, `vendor_id`, `product_name`, `product_id`, `hardware_version`, `hardware_version_string`, `software_version`, `software_version_string`, `manufacturing_date`, `part_number`, `product_url`, `product_label`, `serial_number` | Static BDBI identity attributes. Only the ones you set are created. |
| `battery`, `wired` | A Power Source on the Bridged Node endpoint, as described in [power.md](power.md). `powers:` defaults to the Bridged Node endpoint and its child endpoints. |
| `label`, `tags`, `clusters`, `on_attribute` | As on any endpoint. `clusters` and `on_attribute` go on the Bridged Node endpoint and need a `primary` (or a single device type on it). |
| any device type key | `temperature_sensor`, `humidity_sensor`, `on_off_plug_in_unit`, `room_air_conditioner`, `thermostat`, ... bound exactly as in [endpoints.md](endpoints.md). |

`bridged_node`, `aggregator` and `parent` are not allowed in a device: the structure is created for you.

### Endpoint layout of a device

| Device has | Layout |
|---|---|
| one device type | One endpoint: Bridged Node and the device type together. |
| several device types | The Bridged Node endpoint (BDBI, Power Source) with one child endpoint per device type. |
| several, with `primary: X` | X shares the Bridged Node endpoint; the rest are children. |
| one, with `nested: true` | The device type in a child endpoint. Use it for many identical devices, see below. |

This follows the Matter device library: the Bridged Node is a utility device type on the endpoint, application device types sit on the same endpoint (one) or on parts of a composed device (several). The Descriptor `PartsList` is computed by connectedhomeip from the parent links: the Aggregator lists all of its descendants (full family), a Bridged Node lists its children. The Bridged Node device type is first in the endpoint's DeviceTypeList.

**Identical bridged devices and tags.** Sibling endpoints with an overlapping application device type need a Descriptor `TagList` (the Duplicate condition, see [composition.md](composition.md)); one is added automatically as a Common Number tag, and there are only 31 of them. Twenty identical one-endpoint thermometers share the Aggregator as parent, so they get tags One..Twenty. More than 31 identical devices fail with an error that asks for explicit `tags:`. With `nested: true` the Bridged Node endpoints have only the utility device type, so they are never duplicates and need no tags, at the price of one extra endpoint per device.

### Endpoint numbers

Endpoint numbers are a pure function of the key, with the same FNV-1a hashing as `expose:` ([expose.md](expose.md#endpoint-numbers)): `bridge` for the Aggregator, `bridge:<key>` for a Bridged Node and `bridge:<key>/<device type>` for a child. Adding, removing or reordering devices never moves the others, and renaming `name:` does not either. Do not rename a key once the node is commissioned. A hash collision is a build error that names both and suggests a pin; with 150 endpoints the chance of one is around 15%, so large bridges should expect to pin a few.

## Bridged Device Basic Information

| Attribute | Source |
|---|---|
| NodeLabel | `name`. Writable by controllers and stored in flash when a controller writes it: a later `name:` change in YAML then does not take effect (unverified). |
| UniqueID | `unique_id`, or `<esphome name>-<key>`. Mandatory in BDBI revision 4 and never allowed to change: set it explicitly if you may rename the ESPHome node. Duplicates are a build error. |
| VendorName, VendorID, ProductName, ProductID, HardwareVersion(String), SoftwareVersion(String), ManufacturingDate, PartNumber, ProductURL, ProductLabel, SerialNumber | Static values from the options above. Unset ones are not created; controllers fall back to the NodeLabel or the device type. Lengths are checked in bytes. |
| Reachable | `reachable`, a binary sensor (ON = reachable). Without it the attribute is always true. |

There are no bound text attributes (a text sensor providing a serial number or version) yet; values are static.

### Reachable

ESPHome entities have no universal "available" flag, so Reachable is an explicit binary sensor. While the sensor has no state the node is not reachable. A change is published as the BDBI attribute and as the `ReachableChanged` event, which makes controllers show "No Response" for just that device. Typical sources:

| Device | Reachable sensor |
|---|---|
| BLE advertiser (Xiaomi, ATC, pvvx, BTHome, Ruuvi, ...) | `binary_sensor: platform: ble_presence` with a `timeout` of a few advertising intervals |
| GATT device read by `ble_client` | A template binary sensor on the connection state: `lambda: return id(my_ble_client).connected();` |
| ESP-NOW, UDP, 433 MHz or other radio sensors | A template binary sensor on the time of the last message |
| Modbus, UART, I2C | A template binary sensor on the last successful read, or the component's own status sensor |
| IR (one way) | None: leave Reachable at true; the state is assumed |

Mark the sensors `internal: true` when only Matter uses them. Reachable changes are not rate limited; a flapping sensor produces events, so give the sensor a hysteresis such as the `timeout` above.

## Hand-written bridges

`aggregator` and `bridged_node` are device types of `endpoints:`:

```yaml
matter:
  endpoints:
    1:
      aggregator: {}
    2:
      bridged_node: Relay 1               # or {name: Relay 1, serial_number: ..., reachable: ...}
      on_off_plug_in_unit:
        switch_id: relay1
    3:
      bridged_node: {name: Garage Sensor}
      battery: garage_battery
    4:
      parent: 3                           # child endpoint of the Bridged Node
      temperature_sensor: garage_temperature
```

With exactly one aggregator a Bridged Node needs no `parent:`; with several, `parent:` names the Aggregator. The default UniqueID is `<esphome name>-<endpoint>`.

## Rules that are checked at build time

- An Aggregator is a direct child of the root node, has no application device type and is not a Bridged Node.
- A Bridged Node is a direct child of an Aggregator (not of the root node, not of another Bridged Node).
- UniqueIDs are unique; NodeLabel and UniqueID fit 32 bytes; a duplicate NodeLabel is a warning.
- Warnings: an Aggregator without Bridged Nodes, a Bridged Node without device type or children, a non-Bridged-Node endpoint below an Aggregator, more than 50 bridged nodes.

`python -m tools.conformance` checks the generated layout the same way, and runtime dumps too (`BR-*` rules in [conformance.md](conformance.md)): including that the Aggregator `PartsList` lists all of its descendants.

## Recipes

### BLE thermometers

[examples/bridge-ble-thermometers.yaml](../examples/bridge-ble-thermometers.yaml): two Xiaomi LYWSD03MMC read with `esp32_ble_tracker`, each a Bridged Node with temperature and humidity child endpoints, a battery Power Source and `ble_presence` as Reachable. Any ESPHome BLE sensor platform works the same (`xiaomi_ble`, `atc_mithermometer`, `pvvx_mithermometer`, `bthome`, `ruuvitag`, `inkbird_ibsth1_mini`, ...). Notes:

- The ESP32 must be commissioned over Wi-Fi (or Ethernet), not BLE: BLE commissioning and ESPHome's BLE tracker are mutually exclusive in this component ([ble-commissioning.md](ble-commissioning.md)). Running the BLE tracker next to Matter on Wi-Fi is not tested on hardware; budget RAM for it (classic ESP32 and C3 are tight).
- The topology is static: new thermometers need a YAML entry and a new firmware. Runtime discovery of new devices is not possible with this component.
- For a `ble_client` device with a connection (lock, light, valve) use the same layout and the template binary sensor on `id(my_ble_client).connected()` for Reachable.

### ESP-NOW sensors and relays

[examples/bridge-espnow-sensors.yaml](../examples/bridge-espnow-sensors.yaml): two battery sensor nodes and one relay behind an ESP-NOW gateway. `espnow: on_receive` publishes into template sensors; the relay is a template switch whose actions call `espnow.send`; Reachable is a template binary sensor on the last message time. ESP-NOW shares the radio and channel with Wi-Fi, so this works on Wi-Fi builds only, not on Thread.

### Infrared air conditioners

[examples/bridge-ir-ac.yaml](../examples/bridge-ir-ac.yaml): each `climate_ir` platform (`coolix`, `daikin`, `mitsubishi`, ...) is a `room_air_conditioner` bound with `climate_id` ([climate.md](climate.md)). The first AC uses `primary:` to keep the air conditioner on the Bridged Node endpoint and a child endpoint for the room temperature sensor. IR is one way, so the state is what was last sent; there is no Reachable. Fan control and swing are not exposed ([climate.md](climate.md#not-supported-yet)).

Other sources follow the same pattern: 433 MHz sockets and door contacts (`remote_receiver` into a `binary_sensor`, `remote_transmitter` from a template switch), Modbus meters and heat pumps (`modbus_controller` sensors into `temperature_sensor` / `electrical_sensor`).

## Limits

- **Static only.** All bridged devices are in the YAML. A hub that learns devices at runtime (Zigbee, a Hue-style bridge, BTHome auto discovery) needs endpoints created while running, which this component does not do.
- **`expose:` cannot bridge** ([expose.md](expose.md#not-supported-yet)). Use `bridge:` or `endpoints:`.
- **RAM and endpoints.** Each endpoint costs RAM; `CONFIG_ESP_MATTER_MAX_DYNAMIC_ENDPOINT_COUNT` is set to the exact number. A bridge with many nested devices needs a chip with room (ESP32-S3 or C6; measure on C3).
- **Not verified on hardware or with controllers.** Check the result with `python -m tools.conformance` and the controller you use before relying on it.
- **One commissioning.** Bridged devices cannot be commissioned on their own, and the structure change of an already commissioned bridge (new devices, renamed keys) may need a controller refresh or, for renamed keys, re-adding the accessory.
