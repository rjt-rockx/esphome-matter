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

`matter:` with neither `expose:` nor `endpoints:` is an error, because nothing is exposed by accident. The
error prints a list you can paste:

```
matter: nothing to expose. Add one of:
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
| `label` | Endpoint label. Dropped (debug log) while the base has no endpoint `label:` key. |
| `attach_to` | Add this entity's clusters to another exposed entity's (or group's) endpoint. No number of its own. |
| `was` | The previous id of a renamed entity; keeps its endpoint number. |
| `meters` | Reserved for the Electrical Sensor mapping. |

`exclude:` takes ids or globs at the top level of `matter:`.

### Precedence and errors

- Explicit id beats glob beats `all`. `exclude:` beats globs and `all`.
- Excluding an id that is listed explicitly in `expose:` is an error.
- An id matched twice at the same level with different overrides is an error.
- Globs and `all` skip `internal: true` entities, entities without a `name:` (ESPHome makes them internal),
  `entity_category: config|diagnostic`, and entities that have no mapping. The reason of every skip is
  printed in the plan. An explicit id may expose an internal or diagnostic entity (with a warning).
- A glob that matches nothing, an unknown id (with "did you mean" suggestions) and an entity whose domain
  has no Matter device type (button, number, select, text, text_sensor, ...) are errors with the reason.

## What is inferred

| Entity | Device type |
|---|---|
| `switch` | `on_off_plug_in_unit` (`as: on_off_light` for a lamp) |
| `light` | by platform: `binary` on_off_light, `monochromatic` dimmable_light, `cwww`/`color_temperature` color_temperature_light, `rgb`/`rgbw`/`rgbww`/`rgbct` extended_color_light. Other platforms need `as:`. |
| `sensor` temperature (class, unit, or a `temperature:` sub-sensor) | `temperature_sensor` |
| `sensor` humidity | `humidity_sensor` |

Sub-sensors nested one or two levels deep (`scd4x: temperature: {id: t}`, `pmsx003: pm_2_5: {id: ...}`) are
found, and their key is used as the device class hint. Only entities with an explicit `id:` can be exposed.
More rules are added to a registry (`expose/inference.py`); `as:` is always available.

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

Configs that only set node options (`node_label`, `on_commissioned`, `ble_commissioning`, ...) now need
`expose: []` (or a list), because a bare `matter:` is an error.
