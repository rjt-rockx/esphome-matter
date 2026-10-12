# Power sources, topology and energy

How a device tells controllers where its power comes from and what its electrical measurements cover. Three pieces work together:

* **Power Source** cluster: the battery or mains supply (`battery:` / `wired:` on an endpoint, on the root node or an application endpoint).
* **Electrical Sensor** device type: power and energy measurements (`electrical_sensor`, see [sensors.md](sensors.md#electrical-measurements) for the sensor options).
* **Power Topology** cluster of the Electrical Sensor: which endpoints the measurements cover (`topology:`).

None of this is verified on controllers yet. Which of the placements below a controller shows best is not known; the sections say what the specification allows.

## Power Source placement

A Power Source cluster is configured with `battery:` (a battery) or `wired:` (a mains or DC supply) on an endpoint. Pick the endpoint that fits the device:

```yaml
matter:
  endpoints:
    # Node level battery on the root node (endpoint 0): the Power Source device type is added to the
    # root node and its EndpointList names the endpoints it powers (default: every other endpoint).
    0:
      battery:
        percent: battery_percent
    1:
      temperature_sensor: temperature
    # Battery that belongs to one endpoint only: next to the sensor clusters.
    2:
      contact_sensor:
        boolean_state: door
      battery: door_battery_percent
```

* **Root node (endpoint 0).** The root node exists already; a configured endpoint 0 only adds a Power Source (`battery:` / `wired:`) to it. Nothing else can be configured there (no application device types, no `electrical_sensor`, no `parent`, `label`, `tags` or `clusters`). The `EndpointList` attribute defaults to every other configured endpoint; `powers: [1, 2]` lists specific endpoints. Use it when the whole node runs on one source, like a battery sensor with several sensor endpoints.
* **Application endpoint.** The cluster is created next to the device type clusters, without the Power Source device type, and the `EndpointList` stays empty unless `powers:` is set. This is the shape of many real battery devices. Use it when a controller should associate the battery with that endpoint.
* **Dedicated endpoint.** The `power_source` device type (`power_source: {battery: sensor_id}`) creates an endpoint of its own.

An endpoint has one Power Source cluster, so `battery` and `wired` cannot be on the same endpoint and cannot be combined with the `power_source` device type or cluster on that endpoint. A device with mains and a backup battery needs the second source on another endpoint (for example the `wired` source on endpoint 0 and the `battery` on a sensor endpoint, which then lists `powers`).

`EndpointList` is set after Matter started (the attribute is served by the connectedhomeip `PowerSourceServer`).

## Battery options

`battery: sensor_id` is the shorthand for `battery: {percent: sensor_id}`. All options:

| Option | Binds to | Notes |
| --- | --- | --- |
| `percent` | `BatPercentRemaining` | A sensor in `%`, converted to Matter's half percent steps. Also drives `BatChargeLevel` (see below). |
| `voltage` | `BatVoltage` | A voltage sensor (`V`, `mV`), reported in mV. |
| `low`, `critical` | `BatChargeLevel` | Binary sensors. `critical` ON gives `Critical`, else `low` ON gives `Warning`, else `OK`. Cannot be combined with `percent`. |
| `warning_below`, `critical_below` | `BatChargeLevel` | Percent thresholds for a `percent` sensor (defaults 20 and 10; a single one is checked against the other's default). The charge level follows the percent sensor. A reading that is not a number does not update it, and without any reading it stays `OK` (the enum has no unknown value). |
| `replacement_needed` | `BatReplacementNeeded` | Binary sensor, ON = the battery needs replacing. |
| `replaceability` | `BatReplaceability` | `unspecified` (default), `not_replaceable`, `user_replaceable`, `factory_replaceable`. |
| `replacement_description`, `quantity` | `BatReplacementDescription`, `BatQuantity` | `user_replaceable` enables the **Replaceable** (REPLC) feature, which makes both mandatory: give a description such as `CR2032` (a description alone implies `user_replaceable`); `quantity` defaults to 1. |
| `charging`, `charged` | `BatChargeState` | Binary sensors. Either one enables the **Rechargeable** (RECHG) feature (or set `rechargeable: true`). `charging` ON gives `IsCharging`, else `charged` ON gives `IsAtFullCharge`, else `IsNotCharging`. The state is `Unknown` until a sensor has a state. |
| `functional_while_charging` | `BatFunctionalWhileCharging` | Rechargeable batteries only, default `true`. |
| `order`, `description` | `Order`, `Description` | Preference order of the source (the lowest is used first) and a description (default `Battery`, at most 60 bytes). `order` is also available on the `power_source` device type. Sources that share an order give a build warning: set it, for example 0 for mains and 1 for the battery backup. |
| `powers` | `EndpointList` | Endpoints that run on this battery (no length limit; the 1.6 XML sets none). |

`Status` is `Active`, except that a battery with a higher `order` than a wired source that has a `present` sensor reports `Standby` while that source is present. Not supported yet: `BatTimeRemaining`, `BatPresent`, `BatCapacity`, `BatChargingCurrent`, the fault lists and battery chemistry/designation attributes. Matter has no battery level from a voltage; use a template sensor to convert a voltage to a percentage.

```yaml
matter:
  endpoints:
    0:
      battery:
        percent: battery_percent
        voltage: battery_voltage
        replacement_needed: battery_replace
        charging: battery_charging
        replaceability: user_replaceable
        replacement_description: CR123A
        quantity: 2
        warning_below: 25
        critical_below: 8
```

## Wired options

```yaml
matter:
  endpoints:
    0:
      wired:
        current_type: ac          # ac (default) or dc
        present: mains_present    # binary_sensor -> WiredPresent
        voltage: mains_voltage    # sensor -> WiredAssessedInputVoltage (mV)
        frequency: mains_frequency  # sensor -> WiredAssessedInputFrequency (Hz)
        current: mains_current    # sensor -> WiredAssessedCurrent (mA)
```

`description` defaults to `Mains`; `order` and `powers` work as for the battery. With `present` bound, `Status` is `Active` while present and `Unavailable` otherwise (it stays `Active` until the sensor has a state).

## Power Topology

The Electrical Sensor needs one of three topologies, chosen with `topology` (the default is `node`, as before):

| `topology` | Meaning | Use for |
| --- | --- | --- |
| `node` | The sensor measures the entire node. | A single meter. |
| `tree` | The sensor measures its own endpoint and all endpoints below it in the Descriptor composition (`parent:`). A sensor on an endpoint without children measures just that endpoint. | The total of a power strip with the outlets as child endpoints. |
| `set` | The sensor measures the endpoints in `available_endpoints`. `active_endpoints` (a subset) additionally enables the Dynamic Power Flow feature and is reported as `ActiveEndpoints`. | A meter that can be switched between circuits. |

```yaml
matter:
  endpoints:
    10:                         # strip total
      electrical_sensor:
        topology: tree
        active_power: strip_power
    11:
      parent: 10                # outlet 1, part of the strip total
      on_off_plug_in_unit:
        switch_id: outlet_1
      electrical_sensor:
        topology: tree          # measures this outlet only
        active_power: outlet_1_power
    30:
      electrical_sensor:
        topology: set
        available_endpoints: [11, 12]
        active_endpoints: [11]
        active_power: set_power
```

The two lists of the `set` topology are static: fixed at build time (at most 20 entries) and never updated at runtime, even with Dynamic Power Flow. `active_endpoints: []` is allowed (nothing is measured right now). `with_features: [tree_topology]` and the other topology features used to be accepted and produced a node with both NODE and TREE, which is not conformant; they are now rejected with a hint to use `topology` (`tree_topology` becomes `topology: tree`).

A node topology means the whole node. The Power Topology XML does not say that only one endpoint may use it, and real multi-meter devices do advertise several, so several endpoints with `topology: node` only give a warning at build time; use `tree` with `parent:` for sub-meters instead.

## Electrical sensors on other endpoints

The Electrical Sensor device type has endpoint scope in the device library, so it cannot be put on the root node (endpoint 0); a whole-node measurement uses its own endpoint with `topology: node`. `electrical_sensor` is a regular device type key, so it can be combined with an application device type on the same endpoint, or sit on its own endpoint:

```yaml
matter:
  endpoints:
    10:                          # whole-node measurement on a dedicated endpoint
      electrical_sensor:
        topology: node
        active_power: total_power
    1:                           # a cover with a motor meter
      window_covering:
        cover_id: shade
      electrical_sensor:
        topology: tree
        active_power: motor_power
    2:                           # a plug with a meter on the same endpoint
      on_off_plug_in_unit:
        switch_id: relay
      electrical_sensor:
        topology: tree
        active_power: relay_power
```

There is no separate `meters:` option. The root node's Power Source device type counts against esp-matter's device type limit; the build sets `CONFIG_ESP_MATTER_MAX_DEVICE_TYPE_COUNT` accordingly.

## Energy attributes

The Electrical Energy Measurement cluster is created when `energy_imported` or `energy_exported` is mapped, with the matching `ImportedEnergy` / `ExportedEnergy` feature and the `CumulativeEnergy` feature. esp-matter creates `CumulativeEnergyImported` only with `ImportedEnergy` and `CumulativeEnergyExported` only with `ExportedEnergy`, so mapping only one direction gives only that attribute. No periodic energy attributes are created, because there is no way to bind them. (Older versions of `python -m tools.conformance` reported `CumulativeEnergyExported` on a meter with only `energy_imported`; that was the checker's model of the SDK, not the device.)
