# Exposing entities with `expose:`

`matter: expose:` is the short way to put ESPHome entities on a Matter node. You name the entities; the
component picks the device type, gives every endpoint a stable number and writes the explicit
`endpoints:` tree for you. `esphome config` prints that lowered tree, so you can copy it into your YAML
whenever you want full control.

```yaml
switch:
  - {platform: gpio, id: relay, name: Plug, pin: GPIO4}

matter:
  expose: [relay]   # endpoint 38775, on_off_plug_in_unit "Plug"
```

`matter:` with neither `expose:` nor `endpoints:` stays valid: it is a root node only, and nothing is exposed
by accident. An INFO line prints a list you can paste:

```
matter: no entities are exposed (root node only). To expose some, add one of:
  expose: [relay, desk, t, h]      # inferred from your config; edit as needed
  expose: all
  expose: []                       # root node only
```

`expose: []` is a valid node with only the root endpoint. `expose` and `endpoints:` can be mixed; the
numbers in `endpoints:` are pins that `expose` numbering respects.

## Items

| Item | Meaning |
|---|---|
| `relay` | An entity `id` (must be declared with an explicit `id:`). |
| `relay_*` | An fnmatch glob over ids (`*`, `?`, `[ab]`). Expanded in natural order (`relay_2` before `relay_10`). |
| `all` | Every eligible entity. Opt-in; warns above 16 endpoints. |
| `{id: ..., as: ..., ...}` | A map with overrides. `id` may be a glob; the overrides apply to every match. |

Map keys:

| Key | Effect |
|---|---|
| `as` | Device type key (`on_off_light`, `humidity_sensor`, ...). Wrong entity/type pairs fail with ESPHome's normal `ID ... doesn't inherit from ...` error. |
| `endpoint` | Pin the endpoint number (1..65534). |
| `features` | Extra features of the device type (same names as `features:` in `endpoints:`). |
| `options` | Merged into the device type block (e.g. `min_level`, `inverted`). |
| `label` | Endpoint label (FixedLabel, at most 16 UTF-8 bytes; longer is an error). Without it, the entity name is the label only on endpoints in a Duplicate set (see Tags and labels). |
| `attach_to` | Add this entity's clusters to another exposed entity's (or group's) endpoint. No number of its own. |
| `was` | The previous id of a renamed entity; keeps its endpoint number. |
| `meters` | On a power, energy, voltage, current, frequency or power factor sensor: the load (switch, light, cover, fan id) whose endpoint carries the Electrical Sensor. Same effect as `attach_to`. |
| `tags` | Descriptor semantic tags (same entries as the endpoint `tags:`, 1 to 3). Replaces the automatic Common Number tag. |
| `parent` | Id of an exposed entity (or endpoint key) whose endpoint becomes this endpoint's parent, or the number of a hand-written endpoint. |
| `clusters` | Extra clusters, same as the endpoint `clusters:`. Merged into the lowered endpoint. |

`exclude:` takes ids or globs at the top level of `matter:`.

### Precedence and errors

- Explicit id beats glob beats `all`. `exclude:` beats globs and `all`.
- Excluding an id that is listed explicitly in `expose:` is an error.
- An id matched twice at the same level with different overrides is an error.
- Globs and `all` skip `internal: true` entities, entities without a `name:` (ESPHome makes them internal),
  `entity_category: config|diagnostic`, and entities that have no mapping. The reason of every skip is
  printed in the plan. An explicit id may expose an internal or diagnostic entity (with a warning).
- A glob that matches nothing, an unknown id (with "did you mean" suggestions) and an entity whose domain
  has no Matter device type (number, select, text, text_sensor, ...) are errors with the reason.

## What is inferred

Every row can be overridden with `as:`. Nothing is guessed from a missing `device_class`.

| Entity | Device type |
|---|---|
| `switch` | `on_off_plug_in_unit` (`as: on_off_light` for a lamp) |
| `light` | by platform: `binary` on_off_light, `monochromatic` dimmable_light, `cwww`/`color_temperature` color_temperature_light, `rgb`/`rgbw`/`rgbww`/`rgbct` extended_color_light. Other platforms need `as:`. |
| `cover` | `window_covering`. `garage`, `gate` and `door` classes also get `closure_compat: true` (only when this build has `closure_compat`; otherwise a glob skips the cover and an explicit id exposes it as a plain Window Covering with a warning); the native `closure` type is only ever chosen with `as:`. |
| `fan` | `fan` |
| `lock` | `door_lock` |
| `valve` | `water_valve` (Apple Home does not show it; the plan says so) |
| `climate` | `thermostat`; `room_air_conditioner` for the air conditioner platforms (`climate_ir_lg`, `coolix`, `daikin`, `mitsubishi`, `midea`, `haier`, ...) |
| `binary_sensor` `door`, `window`, `opening`, `garage_door` | `contact_sensor` (polarity handled by the device type) |
| `binary_sensor` `motion`, `occupancy`, `presence` | `occupancy_sensor` |
| `binary_sensor` `moisture` / `cold` | `water_leak_detector` / `water_freeze_detector` |
| `binary_sensor` `smoke`, `carbon_monoxide` | `smoke_co_alarm`; both share the endpoint `group:smoke_co_alarm` |
| `binary_sensor` with no or another class | not inferred. The error lists the classes and the `as:` choices |
| `sensor` temperature (class, unit, or a `temperature:` sub-sensor) | `temperature_sensor` |
| `sensor` humidity / pressure / illuminance / volume_flow_rate | `humidity_sensor` / `pressure_sensor` / `light_sensor` / `flow_sensor` |
| `sensor` carbon_dioxide, carbon_monoxide, pm1, pm25, pm10, volatile_organic_compounds, nitrogen_dioxide, ozone | `air_quality_sensor`, all merged into one endpoint `group:air_quality`. Attach a temperature or humidity sensor to it with `attach_to: group:air_quality`. |
| `sensor` power, energy, voltage, current, apparent_power, reactive_power, frequency, power_factor | `electrical_sensor` (see Metering) |
| `sensor` battery | Power Source (see Battery) |
| `event` | `generic_switch` without a binding. Send the events with `matter.send_switch_event` from `on_event:`. |
| `button` | The same, but only when listed by id; globs and `all` skip buttons. |

Sub-sensors nested one or two levels deep (`scd4x: co2: {id: ...}`, `pmsx003: pm_2_5: {id: ...}`) are found,
and their key is used as the device class hint (`co2`, `pm_2_5`, `voc`, ... are understood). Only entities with
an explicit `id:` can be exposed. More rules are added to a registry (`expose/inference.py`).

### Metering

A meter belongs to the endpoint of a switch, light, cover or fan. It is attached to that endpoint (the
endpoint gets the Electrical Sensor device type next to its own) when:

1. the item has `meters: <load id>` (or `attach_to:`), or
2. the sensor is *related* to exactly one load: its id starts with the load's id (`relay_1_power` for
   `relay_1`), then its name starts with the load's name (`Relay 1 Power` for `Relay 1`), then its id ends
   with the load's number (`power_2` for `relay_2`) when at least two loads carry distinct numbers and the
   sensor's number matches exactly one. Batteries never match on a number alone. Prefixes match on a word boundary, so `Relay 12 Power`
   does not belong to `Relay 1`, or
3. the sensor is listed by its own id (not picked by a glob or `all`) and only one load is exposed.

An unrelated sensor under a glob or `all` (an ADC "Battery voltage" next to a lamp) is not attached. Meters
that are not attached share one standalone endpoint `group:electrical`. A second sensor for an attribute the
group already has (two power sensors) gets an endpoint of its own instead of an error. To force a standalone
endpoint use `as: electrical_sensor`, and `options: {current_type: dc}` for DC.

TODO (wave-power owns Power Topology): every `electrical_sensor` endpoint is created with NodeTopology, so
several metered endpoints on one node each advertise it. Revisit when wave-power decides how a node with
several meters declares its topology.

### Battery

A battery sensor puts a Power Source on an application endpoint (`battery:` of the endpoint), like most real
devices. It goes to the endpoint named with `attach_to:`, or to the one it is related to by id or name
(same rules as meters, without the number rule and the lone-candidate shortcut). Otherwise it gets its own
`power_source` endpoint. An endpoint that already has a battery is not a candidate: a second battery gets its
own endpoint. (Power Source on endpoint 0 is not available yet.)

A Smoke CO Alarm without any Power Source in the plan logs a warning that suggests exposing the battery with
`attach_to:`. The esp-matter device type does not require one; whether the Matter device library text does
is not verified here.

### `as:` for devices with many attributes

`as: electrical_sensor`, `as: air_quality_sensor` and `as: smoke_co_alarm` pick the attribute from the
entity's `device_class`, with the same tables as the rules. Without a usable class the error lists the
classes. Such an item is never merged: it gets its own endpoint.

### Tags and labels

An endpoint that shares an application device type with a sibling (same parent) must carry a Descriptor
TagList. For such endpoints expose writes a Common Number tag itself and also sets the entity name as the
endpoint label. The number comes from the id, not from the hashed endpoint number:

- if every id of the set ends in a distinct number (`relay_1`, `relay_2`, `relay_10`), that number;
- Every id that ends in a number keeps it (if it is free and at most 30), whatever the other ids are, so
  adding or removing an unrelated entity never moves it. The other ids take the lowest free numbers in
  natural id order (`One`, `Two`, ...), skipping numbers used by `tags:` of exposed or hand-written
  endpoints. Those positional numbers can move when entities are added before them; the log says so. Give
  `tags:` to pin them.

Hand-written `endpoints:` count as siblings, and `parent:` by id and by number are the same parent.

Endpoints that are alone get neither tag nor label, unless the item has `tags:` or `label:`. A derived label
longer than 16 bytes is truncated (with a warning); if truncation makes labels of one set identical, a number (the trailing number of the id, else the position) is appended and a warning says so; an explicit `label:` that long is an error. Utility device
types (Power Source, Electrical Sensor) never count as duplicates.

### Skipped unnamed entities

A glob or `all` skips entities without a `name:` (ESPHome treats them as internal). This is logged as a
warning that lists the ids and the fixes: give each a `name:`, list the id explicitly in `expose:`, or add it
to `exclude:` to silence it.

### Not supported yet

`bridged: true` is not available: the base has the `bridged_node` and `aggregator` device types but no bridge
composition, so expose cannot build one. Use `endpoints:` with `parent:` for bridges.

## Endpoint numbers

The number is a pure function of the entity key, so it is the same on every build host and survives edits
to other entities:

```python
def endpoint_for_key(key):
    h = 0x811C9DC5
    for b in key.encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF   # FNV-1a 32
    h = (h >> 16) ^ (h & 0xFFFF)                  # fold to 16 bits
    return h % 65534 + 1                          # 1..65534
```

The key is the entity `id` (`relay_1` is endpoint 30374), `group:<name>` for a merged endpoint, or the
`was:` id. Pins (`endpoint:` and the numbers under `endpoints:`) win. A duplicate pin or any hash
collision is a compile error naming both keys; nothing is salted or renumbered:

```
matter: endpoint 47922 collides: e84 and e276. Pin one of them to a free endpoint: {id: e84, endpoint: 6810} (6810 is free); e276 then keeps 47922.
```

Pin one of the colliding entities to the suggested free number (`{id: e84, endpoint: 6810}`); the other keeps its hashed number. Pinning a key to the shared number does not help, because the other key still hashes there. Renaming an id moves its endpoint
unless you add `was: old_id`. Collisions are rare (about 1% at 50 exposed entities).

To move an existing hand-numbered endpoint to `expose` without re-pairing, pin its number:
`expose: [{id: relay, endpoint: 1}]`, and delete the `endpoints: 1:` block.

## The plan

`esphome config` logs the plan at INFO level:

```
endpoint  key       device types          entities  label  number from
30374     relay_1   on_off_plug_in_unit   relay_1   Relay  hash
34748     desk      extended_color_light  desk      Desk   hash
skipped relay_3: excluded
```

## Migration from a bare `matter:`

Configs that only set node options (`node_label`, `on_commissioned`, `ble_commissioning`, ...) keep working
unchanged: a bare `matter:` is a root node, and `esphome config` logs the `expose:` list it would infer.
