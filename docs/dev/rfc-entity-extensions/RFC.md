# RFC (draft): entity extensions, a registry for per-entity options

> **Status: internal draft. It has not been posted anywhere.** This file lives only in the
> esphome-matter repository. Nothing here goes to ESPHome maintainers unless the
> project owner decides to send it. It is written in the form a `#devs` post plus a
> core PR description would take, so the decision can be made on the text as it stands.

| | |
|---|---|
| Target | esphome/esphome `dev` (prototype made against `c6ed2069a566`, 2026.11.0-dev) |
| Prototype | [`0001-entity-extensions-registry.patch`](0001-entity-extensions-registry.patch): 9 files, +240/-17 outside `tests/`, +168 tests |
| Reference user | `zigbee`, ported with no YAML change and identical `esphome config` output and generated C++ |
| Motivating user | Matter (external component today), which needs a nested per-entity `matter:` block |

## 1. Summary

ESPHome integrations that add options to entities (`zigbee: endpoint`, `mqtt: qos`,
`web_server: sorting_group_id`) are imported by each entity domain and wired in by hand:
one schema `.extend()`, one `add_extra()` and one `setup_*()` call per domain. This RFC
proposes a small registry for that wiring:

```python
# esphome/components/<integration>/__init__.py
ENTITY_EXTENSIONS = [
    EntityExtension("switch", SWITCH_OPTIONS, key="matter", validate=..., setup=...),
]

# esphome/components/switch/__init__.py (once per domain)
_SWITCH_SCHEMA.add_entity_extensions("switch")
...
await setup_entity_extensions("switch", var, config)
```

An extension applies only while its owning component is loaded. Its options are
validated by its own schema, with errors reported on the entity path, and merged into
the validated entity config. Nested keys are the default; a flat mode exists only so
that zigbee keeps its current YAML. The domains stop importing the integrations.

The first PR adds the registry, wires the four domains zigbee uses today and ports
zigbee onto it. Zigbee's test configs then produce the same validated config and the
same `main.cpp` as before the change.

## 2. Problem

1. **Every integration with per-entity options is a hard dependency of every entity
   domain it touches.** `sensor/__init__.py` imports `mqtt`, `web_server` and `zigbee`
   (line 7). It extends its base schema with `zigbee.SENSOR_SCHEMA` (line 332), adds
   `zigbee.validate_sensor` as an extra validator (line 377) and awaits
   `zigbee.setup_sensor()` in `setup_sensor_core_` (line 1056). `binary_sensor`, `switch`
   and `number` repeat the same three lines. Each new integration repeats the pattern in
   N domains, and each domain imports every such integration whether or not it is
   configured. Importing `esphome.components.sensor` today pulls in the zigbee package
   (82 vs 79 `esphome.*` modules with the patch).
2. **External components cannot add per-entity options at all.** The only routes are
   monkeypatches. Mutating `_SENSOR_SCHEMA` after import misses platforms that were
   already built, because `extend()` copies the schema at call time. `add_extra()`
   validators run after the mapping has already rejected the unknown key. Wrapping
   `ComponentManifest.config_schema` works but is process-global and leaks between
   dashboard runs. All three were tried and measured
   ([background](#appendix-b-why-an-external-component-cannot-do-this-today)).
3. **Flat options collide.** Zigbee's `endpoint:` sits directly on the entity. Any
   other protocol with endpoints (Matter, Thread, BACnet) cannot reuse that name, and
   nothing detects the clash today: the schema that is extended last simply wins.

## 3. Prior art in core

| Integration | Schema | Gating | Validation | Codegen |
|---|---|---|---|---|
| `zigbee` | `zigbee.{BINARY_SENSOR,SENSOR,SWITCH,NUMBER}_SCHEMA`, **flat** keys `endpoint`, `cluster`, `report`, `use_device_type` (+ `zigbee_id` on nRF52), extended into 4 domains | `cv.requires_component("zigbee")` per key; `cv.OnlyWith(..., ["esp32","zigbee"])` for defaults | `add_extra(zigbee.validate_*)`. Runs on every non-internal entity while zigbee is loaded (zigbee auto-exposes) and records endpoints in `CORE.data` | `await zigbee.setup_*(var, config[, min, max, step])` |
| `mqtt` | `cv.MQTT_COMPONENT_SCHEMA` / `MQTT_COMMAND_COMPONENT_SCHEMA`, flat (`qos`, `retain`, topics...) | `requires_component("mqtt")`, `OnlyWith(CONF_MQTT_ID, "mqtt")` | none | `mqtt.register_mqtt_component()` if `mqtt_id` |
| `web_server` | `WEBSERVER_SORTING_SCHEMA`, **nested** `web_server: {sorting_weight, sorting_group_id}` | `requires_component("web_server")` | none | `web_server.add_entity_config()` (19 domains) |

Two observations shape the proposal:

- The nested `web_server:` block is the better shape: one key per integration, no name
  clashes, and it reads as "this entity's settings for X".
- All three integrations already gate on "component loaded" (`requires_component`,
  `OnlyWith`, `if DOMAIN not in CORE.loaded_integrations`). The registry turns that
  convention into the mechanism, so the options do not exist in the schema at all
  until the owner is loaded.

## 4. Proposal

### 4.1 API

`esphome/core/entity_extensions.py` (new, 168 lines including docs):

```python
@dataclass(frozen=True)
class EntityExtension:
    domain: str                     # "sensor", "switch", ...
    schema: Any                     # any validator; a mapping schema for flat mode
    key: str | None = None          # nested key (recommended); None = flat (legacy)
    validate: Callable[[ConfigType], ConfigType] | None = None   # sees the merged entity
    setup: Callable[..., Awaitable[None]] | None = None          # await setup(var, config, **kw)

def get_entity_extensions(domain) -> list[tuple[str, EntityExtension]]
async def setup_entity_extensions(domain, var, config, **kwargs) -> None
```

- A component declares `ENTITY_EXTENSIONS = [...]` at module level, read through a new
  `ComponentManifest.entity_extensions` property. This follows the same pattern as
  `FINAL_VALIDATE_SCHEMA` and `PREFETCH_FILES`.
- A domain opts in once: `_SWITCH_SCHEMA.add_entity_extensions("switch")`. This sets
  `_Schema._entity_domain`, which `extend()` copies the same way it copies
  `_extra_schemas`. Every platform schema built from the domain base inherits it,
  including sub-entities used as values (`scd4x: co2: {...}`).
- A domain calls `await setup_entity_extensions("switch", var, config)` where it now
  calls `zigbee.setup_switch()`. `number` passes `min_value=`, `max_value=` and `step=`
  as keyword arguments.

### 4.2 Validation semantics

When a schema with `_entity_domain` validates a mapping, it does the following:

1. It finds the active extensions: those whose owner is in `CORE.loaded_integrations`
   (sorted by owner name, so the order is deterministic). With none active, it falls
   back to the original code path. A config without the owner therefore rejects the
   key as an unknown option, exactly as it would for a typo.
2. For each extension it splits its option(s) off the raw mapping and validates them
   with the extension's schema. A nested extension whose key is absent is skipped. A
   flat extension always runs, so `OnlyWith` defaults are still inserted as they are
   today. Errors get the path `[key, ...]` (nested) or `[option]` (flat).
3. It validates the rest with the entity's own schema, including its extras.
   Extension errors and entity errors are reported together in one
   `MultipleInvalid`.
4. It merges the validated values back. **Options the platform schema defines itself
   always win.** Their keys are never split off, and a flat extension's defaults
   never overwrite them. This matches today's behaviour, where the platform's
   `.extend()` overrides zigbee's key. Two active extensions that claim the same
   option on the same domain produce a validation error that names both owners.
5. It runs each active extension's `validate` on the merged config. This is where
   zigbee records endpoints and where Matter would check `internal`/explicit IDs.

There is no global state. Everything is derived per validation from
`CORE.loaded_integrations`, so a long-lived dashboard process cannot leak an extension
from one config into the next. That leak is the defect of every monkeypatch
alternative.

### 4.3 Codegen semantics

`setup_entity_extensions()` awaits each active extension's `setup`. Nested extensions
are set up only for entities that carry their key. Flat ones are set up for every
entity, which is what zigbee needs: its `setup_*` decides by itself and returns early
for `internal`.

### 4.4 What the prototype patch contains

| File | Change |
|---|---|
| `esphome/core/entity_extensions.py` | new: dataclass, lookup, split/validate/merge, setup |
| `esphome/voluptuous_schema.py` | `_entity_domain`, `add_entity_extensions()`, hook in `__call__`, copy in `extend()` (+22/-1) |
| `esphome/loader.py` | `ComponentManifest.entity_extensions` property (+10) |
| `esphome/components/{sensor,binary_sensor,switch,number}/__init__.py` | drop the `zigbee` import, `.extend(zigbee.*_SCHEMA)`, `add_extra(zigbee.validate_*)` and `zigbee.setup_*()`; add `add_entity_extensions()` and `setup_entity_extensions()` (4 lines each) |
| `esphome/components/zigbee/__init__.py` | `ENTITY_EXTENSIONS` for the 4 domains (flat, with a comment saying why) (+22) |
| `tests/unit_tests/core/test_entity_extensions.py` | 12 tests: gating, nested, domain filter, error paths, `extend()` and sub-entities, platform-wins, flat defaults, flat collisions, `validate`, `setup` kwargs, "no zigbee options without zigbee" |

## 5. Reference user: zigbee on the registry

Zigbee's `SENSOR_SCHEMA` and friends, its `validate_*` and its `setup_*` are reused
unchanged. Only the registration moves:

```python
# Zigbee's entity options predate the registry and stay flat (key=None) so
# existing configs keep working.
ENTITY_EXTENSIONS = [
    EntityExtension("binary_sensor", BINARY_SENSOR_SCHEMA,
                    validate=validate_binary_sensor, setup=setup_binary_sensor),
    EntityExtension("sensor", SENSOR_SCHEMA, validate=validate_sensor, setup=setup_sensor),
    EntityExtension("switch", SWITCH_SCHEMA, validate=validate_switch, setup=setup_switch),
    EntityExtension("number", NUMBER_SCHEMA, validate=validate_number, setup=setup_number),
]
```

Results, with the patched tree compared against the unpatched one (commands in
[Appendix A](#appendix-a-verification)):

| Check | Result |
|---|---|
| `script/test_build_components.py -e config -c zigbee` (6 configs: esp32-c6-idf test/validate/router, nrf52 adafruit/mcumgr/xiao-ble) | 6 passed, both trees |
| `esphome config` output, compared as data (mapping order ignored) | identical for all 6 |
| `esphome compile --only-generate` `main.cpp` | byte-identical for the 3 ESP32 configs; the 3 nRF52 configs differ only in the build timestamp `date_code` line, which changes on every run |
| Other entity users, `-e config -t esp32-idf`: template, scd4x (sub-entities), mqtt, web_server, binary_sensor, sensor, switch, number, bme280_i2c | 9 passed |
| nRF52 `deep_sleep` (checks `zigbee` in `loaded_integrations`) and template | 2 passed |
| `pytest tests/unit_tests/core tests/unit_tests/test_voluptuous_schema.py tests/unit_tests/test_loader.py tests/unit_tests/test_config_validation_paths.py tests/unit_tests/test_lazy_imports.py tests/component_tests` | 2541 passed with the patch vs 2529 without (+12 new tests). The same 58 tests fail in both trees, because this environment lacks `pytest-asyncio` |

**Behaviour changes**, all small and listed for review:

1. Without `zigbee:` in the config, `endpoint: 3` on a switch now reads
   `[endpoint] is an invalid option for [switch.template]` instead of
   `This option requires component zigbee.` The new message is accurate but less
   helpful. A follow-up could keep an in-tree hint table (option → component) for
   unknown keys. That would only produce error text and never add schema.
2. `zigbee.validate_*` now runs after the platform's own `add_extra` validators, not
   between the domain's extras and the platform's. No in-tree platform extra reads
   zigbee keys, and the outputs above are identical.
3. The JSON language schema (`script/build_language_schema.py`, used by the VS Code
   extension and Device Builder) no longer sees zigbee keys inside the domain
   schemas, because they are no longer `.extend()`-ed in statically. **This must be
   fixed in the same PR.** The dumper should iterate every component's
   `ENTITY_EXTENSIONS` and attach the options to the domain schema, annotated with the
   owning component. That is about 40 lines and is not in the prototype. In return,
   editors gain support for *external* extensions too, which today is impossible.

## 6. Alternatives considered

| Option | Verdict |
|---|---|
| Keep hard-wiring each integration into each domain | Works for in-tree code (it is what zigbee does). It does not scale, couples every domain to every integration, and gives external components nothing. **This is the fallback if the registry is declined** (§8). |
| Generic `_Schema.add_pre(validator)` hook that integrations register into | Smaller in core, but every integration then reimplements the split/validate/merge, error paths and gating. The registry is the same hook with that logic written once. |
| Monkeypatch `ComponentManifest.config_schema` (external) | Works on 2025.6 through 2026.11-dev, but it is process-global, leaks across dashboard runs and has no editor support. Not acceptable for something meant to be adopted. |
| `PREFETCH_FILES` hook to strip keys from the raw config (external) | Exceptions in prefetch are downgraded to warnings, so a typo silently drops the binding. It also does not run for an empty block and abuses a download hook. |
| An integration's own top-level list (`matter: expose: [ids]`) | Sanctioned and works today. It is what the Matter component ships now. It is not where users look, though, and it does not reach `!extend` overlays as naturally. Both forms are kept (§7). |

## 7. Motivating user: a per-entity `matter:` block

The esphome-matter component already has a central form: `matter: expose:` items
`{id, as, endpoint, features, label, ...}`. These are lowered at the tail of
`CONFIG_SCHEMA` into the explicit `endpoints:` IR that drives codegen. With the
registry, the same item can sit on the entity:

```yaml
matter:                                   # node settings only

switch:
  - platform: gpio
    id: relay
    name: Plug
    pin: GPIO4
    matter: true                          # == expose: [relay]; ep 38775 on_off_plug_in_unit
light:
  - platform: rgbww
    id: lamp
    # ...
    matter: {endpoint: 3, features: [enhanced_hue], label: Reading}
sensor:
  - platform: scd4x
    co2:         {id: co2, name: CO2, matter: {endpoint: 20}}   # sub-entities work
    temperature: {id: t,   name: Temperature, matter: true}
```

- **Schema reuse.** The extension schema *is* the expose item schema without `id`
  (the entity is the id), plus `true` as shorthand for `{}`. One definition serves
  both forms, so docs, errors and editor completion match.
- **Lowering through the same planner.** The entity block is equivalent to an expose
  item `{id: <entity id>, **block}`. The planner collects both forms, so endpoint
  numbering (the stable hash of the id), inference, merge groups and collision errors
  behave the same. Nothing in C++ changes. An entity listed in both places is an
  error.
- **Hooks used.** `validate` requires an explicit `id:` (the endpoint number is keyed
  on it) and warns on `internal: true`. `setup` is not needed, because codegen stays
  in the endpoint engine.
- **Domains.** Matter needs `light`, `fan`, `cover`, `climate`, `lock`, `valve` and
  `event` in addition to the four zigbee domains. Each is a 4-line follow-up PR
  (§8).
- **Feature check.** The external component registers `ENTITY_EXTENSIONS` only when
  `hasattr(loader.ComponentManifest, "entity_extensions")`. On older ESPHome it keeps
  the `expose:` form alone and ships no monkeypatch.

A runnable demo of this shape is in [`demo/`](demo/). It is a tiny external component
`matter_entity_demo` that registers the nested key on the four wired domains and
logs the endpoint plan. On the patched tree:

```
INFO plan: endpoint    20 <- sensor.co2 as=<inferred> features=[] label=<entity name>
INFO plan: endpoint     7 <- switch.fan_relay as=on_off_light features=[] label=Fan
INFO plan: endpoint 38775 <- switch.relay as=<inferred> features=[] label=<entity name>
INFO plan: endpoint 51481 <- sensor.room_t as=<inferred> features=[] label=<entity name>
INFO Configuration is valid!
```

`demo/bad.yaml` shows that the extension errors and the entity's own errors are
reported together, at the entity's path:

```
switch.template: [source bad.yaml:18]
  Expected boolean value, but cannot convert maybe to a boolean. ...
  optimistic: maybe
  matter_entity_demo:
    value must be at least 1.
    endpoint: 0
    [colour] is an invalid option for [matter_entity_demo]. Please check the indentation.
    colour: red
```

`demo/bad-no-id.yaml` shows the `validate` hook ("an exposed entity needs an explicit
id:"). `demo/without.yaml` shows the plain unknown-option error when the component is
not loaded.

## 8. PR plan

ESPHome's bot labels PRs over 1000 changed lines outside `tests/` as too big, and
reviewers prefer one platform per PR. Every step below stays far under that limit.

| # | PR | Size (non-test) | Notes |
|---|---|---|---|
| 0 | `#devs` thread with this text | none | Ask before code, as the contributing guide asks for anything structural. |
| 1 | `[core] Entity extensions registry; port zigbee` | ~300 (prototype 257, plus ~40 for the language-schema dumper) | Registry, `voluptuous_schema` hook, loader property, 4 domains, zigbee port, schema dumper, unit tests. Zigbee test configs unchanged. CODEOWNERS of zigbee as co-reviewers. |
| 2..8 | `[light] Accept entity extensions`, then the same for `fan`, `cover`, `climate`, `lock`, `valve` and `event` | ~4 each, plus 1 test | One line in the base schema and one `setup_entity_extensions` call. |
| 9 | (optional) `[mqtt]` / `[web_server]` onto the registry | ~60 each | Removes the remaining hard-wired imports. `web_server` is already nested. `mqtt` would need the flat mode, like zigbee. Separate, because it touches 19 domains. |
| 10 | (optional) Unknown-option hint table | ~30 | Restores "This option requires component X" for in-tree owners. |

In the esphome-matter repo, only once PR 1 has merged: register `ENTITY_EXTENSIONS`
behind the feature check, reuse the expose item schema, and feed the planner. That is
a few hundred lines of Python and no C++.

**If the registry is declined**, the fallback stays the same as the design decision
(DIRECTION D7). The per-entity `matter:` block lands only if Matter itself moves into
core, hard-wired the way zigbee is. That means a nested `matter.SWITCH_SCHEMA`
extended into each domain with `requires_component("matter")`, an `add_extra`
validator and a `setup_*` call, one domain per PR. The external component keeps
`matter: expose:` and never ships a monkeypatch.

## 9. Open questions for reviewers

1. Should flat mode exist at all, or should zigbee keep its static wiring until a
   deprecation period moves it to `zigbee: {endpoint: ...}`? The prototype keeps flat
   mode, so the port changes no YAML.
2. Should `validate` run for every entity of the domain (zigbee needs this, because
   it auto-exposes) or only for entities that carry the nested key? The prototype runs
   it for every entity and leaves the decision to the hook.
3. Should `get_entity_extensions()` be cached per validation run? It is a sorted walk
   over `loaded_integrations` with cached manifest lookups, per entity. No difference
   was measurable in these tests, but large configs have not been profiled.
4. Where should the hint table for unknown options live (behaviour change 1)?

## Appendix A: verification

All runs used the ESPHome tree at `c6ed2069a566` (dev, 2026.11.0-dev), copied twice
(`base` and `work`, with the patch applied to `work`). Python 3.13. Each tree was run
from its own directory so that `python -m esphome` imports that tree.

```sh
git -C <esphome> apply --check docs/dev/rfc-entity-extensions/0001-entity-extensions-registry.patch   # clean
cd <tree> && python script/test_build_components.py -e config -c zigbee --no-grouping -f            # 6 passed (both)
# config-output and codegen comparison: run `esphome config` / `compile --only-generate`
# on tests/test_build_components/build/zigbee.*.yaml in both trees and compare
python script/test_build_components.py -e config -c template,scd4x,mqtt,web_server,binary_sensor,sensor,switch,number,bme280_i2c -t esp32-idf --no-grouping -f   # 9 passed
python script/test_build_components.py -e config -c deep_sleep,template -t nrf52-adafruit --no-grouping -f                                             # 2 passed
python -m pytest -o addopts="" tests/unit_tests/core tests/unit_tests/test_voluptuous_schema.py tests/unit_tests/test_loader.py \
  tests/unit_tests/test_config_validation_paths.py tests/unit_tests/test_lazy_imports.py tests/component_tests
ruff format --check / ruff check on all touched files                                                 # clean
cd <work> && python -m esphome config <repo>/docs/dev/rfc-entity-extensions/demo/{ok,bad,bad-no-id,without}.yaml
```

`tests/unit_tests/test_config_validation.py` was not run. It needs `hypothesis`, which
is not installed here, and it fails to collect in both trees.

## Appendix B: why an external component cannot do this today

These are measured on ESPHome 2025.6 through 2026.11-dev:

- `_Schema.extend()` copies the mapping and `_extra_schemas` at call time. Platform
  modules build `CONFIG_SCHEMA = sensor.sensor_schema(...)` at import, so a later
  change to `_SENSOR_SCHEMA` never reaches them. An external component that imports
  `sensor` (most do, for `cv.use_id(sensor.Sensor)`) is always too late.
- `add_extra()` validators run after the mapping validation, which has already
  rejected the unknown key.
- `ComponentManifest.config_schema` is a lazy `getattr`. Wrapping it works regardless
  of YAML order, but it is a process-global patch that survives `CORE.reset()` and
  applies to the next config in the same dashboard process.
- `PREFETCH_FILES` (2026.9+) runs before schema validation, but its exceptions are
  downgraded to warnings by design.
