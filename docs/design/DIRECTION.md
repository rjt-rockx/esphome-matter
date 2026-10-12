# esphome-matter: design direction

Status: decided 2026-10-12 (six proposals, spikes 808afba/bdd3094, four judges, KNOWLEDGE.md, lenses). TASKS.json cites § numbers.

## §1 Vision

Any ESPHome device becomes a correct, stable, local Matter device in a few lines of YAML. The user names entities to expose; the component picks spec-correct device types, prints its choice as explicit YAML, keeps endpoint identity stable across edits and OTA, and signals structure changes. Power users get the full data model through the same engine. Nothing is decided at runtime that `esphome config` cannot show.

**One engine, one explicit IR, sugar on top, no hidden state.**

## §2 Principles

| # | Principle | Source |
|---|---|---|
| P1 | One canonical IR, the explicit `endpoints:` tree. Every front door lowers into it, and `esphome config` prints the lowered form. | lens-matter-devs 1, 20; all four judges |
| P2 | Spec device types only. Conformance is checked at compile time, and every error names the fix. | lens-matter-devs 1-6; lens-esphome-reviews 9, 11 |
| P3 | Exposure is explicit. Never expose silently; `expose: all` is opt-in. `internal: true` keeps its meaning. | lens-esphome-reviews 2, 18; lens-ohf P8 |
| P4 | Endpoint identity is a pure function of the YAML. No build-host files and no device history decide numbering. | lens-matter-devs 9; judge-esphome-maintainer §2 |
| P5 | Structure changes are signalled (ConfigurationVersion) and never surprising (no automatic topology flips). | lens-matter-devs 11; judge-end-user §0.1 |
| P6 | Native node by default. Bridging is for real bridges and is always the user's explicit choice. | lens-matter-devs 7, 10; lens-ohf P2 |
| P7 | One path per entity: the native API for Home Assistant, Matter for other ecosystems. HA never commissions the Matter side of an ESPHome device that has `api:`. | lens-ohf §3.3; ha-coexistence §2 |
| P8 | Measure and publish footprint. Gate hard-to-reverse decisions on emulator and hardware evidence. | lens-esphome-reviews 21-23; lens-matter-devs 30 |
| P9 | Small upstreamable steps; byte-identical codegen is the refactor oracle. | lens-esphome-reviews 31-36; lens-zigbee §9 |
| P10 | Secrets belong to the owner; pairing material visible only while useful; easy reset. | lens-ohf P5, §3.5 |

## §3 Resolved decisions

| # | Decision | Rationale | Rejected alternatives |
|---|---|---|---|
| D1 | **Canonical engine = explicit `endpoints:` tree**, converged with upstream DavidvtWout (`with_features`, conformance resolver, events). Today's `features:`/`extra_clusters:` become deprecated aliases with rewrite hints. Endpoint 0 is rejected in `endpoints:`. | 1:1 with the SDK model; byte-identical codegen from lowered sugar (spike); converges upstream. | A new runtime model; product catalog as the engine. |
| D2 | **Default UX = `matter: expose:`** with ids/globs, `exclude:`, and per-item `as:`, `endpoint:`, `features:`, `label:` (plus `options:`, `attach_to:`, `meters:`, `was:`). Lowered at the **tail of `CONFIG_SCHEMA` from `CORE.raw_config`**, re-checked in `FINAL_VALIDATE` against validated configs. | Spike-proven: `use_id` errors stay stock, ID objects get generated, C++ is unchanged. Lowering in FINAL_VALIDATE leaves `id: None` refs and skips `use_id` checks. | Lowering in FINAL_VALIDATE; per-entity key via monkeypatch. |
| D3 | **Bare `matter:` with neither `expose:` nor `endpoints:` is a compile error** that prints a ready-to-paste `expose:` list. `expose: []` means a root-only node. `expose: all` is opt-in. | Explicit intent, heap budget, no tile clutter (judges: end-user, matter-expert). | Zero-config expose-all (entity-centric, maintainer judge). |
| D4 | **Endpoint numbering**: pinned wins; else a 16-bit hash of the entity key (§7); a collision is a **hard compile error** asking for a pin. Sparse IDs are gated (§13): QEMU + matter.js harness, then chip-tool/CHIP TCs, then HA/Apple/Google. **Fallback** if a major controller fails: an on-device allocator (§7.6). | Stateless and reproducible on every build host; erroring on collision removes the spike's silent renumber. | NVS allocator as default (hidden device state, not reproducible from YAML); build-machine lockfile (diverges across HA add-on, CLI and CI); salted rehash (renumbers old entities). |
| D5 | **Spec conformance fixes first**: TagList on Duplicate siblings, ConfigurationVersion bump via an NVS structure hash, explicit parents, exact endpoint and device-type counts. | Multi-gang builds violate the Duplicate rule today; ConfigurationVersion never bumps; >15 endpoints is fragile. | Shipping new UX on a non-conformant base. |
| D6 | **Bridge = explicit opt-in**: spike-bridge's `bridged_node:` in the IR, later `layout: bridged` sugar. Never automatic, never flipped by an edit. | Bridging own functions stretches Aggregator semantics; topology flips force re-pairing; Apple's bridged path is its buggiest. | `mode: auto`; implicit `per_device` from `devices:`. |
| D7 | **Per-entity `matter:` key only after ESPHome gains an entity-extension hook** (or Matter goes into core, hard-wired like zigbee). It reuses the expose item schema. | The monkeypatch is process-global, leaks across dashboard runs, and has no editor support. | Monkeypatched key in a release. |
| D8 | **No product catalog in the schema.** Profiles ship as ESPHome `packages/`. Behaviours (`status_led`, `pairing`) are standalone optional keys. | Catalogs are policy maintainers push out; behaviours are useful alone. | `device: smart_plug`. |
| D9 | **No big-bang table runtime.** Shared runtime primitives are consolidated incrementally, after measurement, behind the unchanged `register_*` API. | Every step stays verifiable against the snapshots. | Table-driven engine as a prerequisite. |
| D10 | **Adopt onboarding/coexistence**: pairing and fabric entities via the native API (user-declared platforms), `matter.remove_fabric`, SerialNumber from the ESPHome MAC, the passcode persisted until first commissioning, and a "one path per entity" warning and guide. | Biggest real first-run failures (judge-end-user §0.3-0.4). | Auto-registered API entities (violates "entities are optional"). |
| D11 | **Inference corrections** (matter-expert): no Generic Switch from a bare `binary_sensor`; Electrical Sensor rides on the plug endpoint; battery = Power Source; room AC = one endpoint with Fan Control; StartUpOnOff stays writable (a `restore_mode` mismatch is a warning). | Spec composition rules (§6). | Inferring buttons from GPIO inputs; non-writable null StartUpOnOff. |

## §4 Canonical YAML scenarios

Entity blocks are abbreviated. The endpoint numbers in comments are the real §7 hashes.

**(a) Smart plug: relay, button, LED** (beginner, Apple Home, no HA)
```yaml
switch:  [{platform: gpio, id: relay, name: Plug, pin: GPIO4}]
binary_sensor:
  - platform: gpio
    id: btn
    pin: {number: GPIO9, inverted: true, mode: INPUT_PULLUP}
    on_click: {then: [switch.toggle: relay]}      # local toggle stays plain ESPHome
output: [{platform: gpio, id: led, pin: GPIO5}]
matter:
  expose: [relay]          # ep 38775 on_off_plug_in_unit "Plug"
  status_led: led          # blinks while a window is open; Identify blinks it
  pairing:
    button: btn            # hold 5 s: open window; hold 15 s: factory reset
```
The setup code printed at first boot stays valid across power cycles until the first commissioning (§11).

**(b) RGBWW light**
```yaml
light:
  - {platform: rgbww, id: desk, name: Desk, red: r, green: g, blue: b, cold_white: cw, warm_white: ww,
     cold_white_color_temperature: 6500 K, warm_white_color_temperature: 2700 K}
matter:
  expose: [desk]   # ep 34748 extended_color_light, color_control [xy, hue_saturation, color_temperature]
                   # ColorTempPhysicalMin/Max = 153/370 mireds from the kelvin values
```

**(c) Multi-sensor: temperature, humidity, CO2, PM2.5, battery**
```yaml
matter:
  expose: [t, h, co2, pm25, batt]
# plan (esphome config):
#   ep 52400  temperature_sensor   <- t
#   ep 55900  humidity_sensor      <- h
#   ep 31397  air_quality_sensor   <- co2, pm25     (merge group group:air_quality)
#   ep 0      power_source (BAT)   <- batt          (EndpointList = 52400, 55900, 31397)
```
`{id: t, attach_to: co2}` puts Temperature Measurement on the air-quality endpoint for a single tile.

**(d) 8 relays, 4 buttons, thermostat** (tinkerer)
```yaml
matter:
  expose:
    - {id: "relay_*", as: on_off_light}              # 8 endpoints, TagList CommonNumber 1..8, label = entity name
    - {id: "btn_*", as: generic_switch, options: {multi_press_max: 2}}   # binary_sensors: explicit `as:`
    - hvac                                           # thermostat; Heating/Cooling/AutoMode from climate traits
  # layout: bridged    # M5, opt in BEFORE pairing if each relay must be its own Apple accessory/room
```
`relay_1`..`relay_8` hash to 30374, 18953, 18812, 18671, 18514, 18885, 18232, 29131 (no collisions).

**(e) Existing large config, minimal edits** (HA household)
```yaml
packages:
  matter: github://rjt-rockx/esphome-matter/packages/matter.yaml@v1   # external_components, esp-idf, size sdkconfig
matter:
  expose: [kitchen_light, coffee_relay, outdoor_temp, garage_door]
button:
  - {platform: matter, type: open_commissioning_window, name: "Matter: allow pairing"}
text_sensor:
  - {platform: matter, type: pairing_code, name: "Matter pairing code"}
  - {platform: matter, type: controllers, name: "Matter controllers"}
```
No entity block is edited; with `api:` present the compile prints the one-path note (§11.5).

**Power-user escape** (mixes freely with `expose`):
```yaml
matter:
  expose: [{id: lamp, endpoint: 3, features: [enhanced_hue], label: Reading}]
  endpoints:
    20: {room_air_conditioner: {climate_id: ac}, clusters: {fan_control: {fan_id: ac_fan}}}
    21: {temperature_sensor: {temperature: room_temp}, parent: 20}
```

## §5 Expose grammar and lowering

- **Items**: a bare string (an id or an fnmatch glob over ids), `all`, or a map `{id, as, endpoint, features, label, options, attach_to, meters, was}`. `id` in a map may be a glob, and the overrides then apply to every match.
- **Precedence**: explicit id > glob > `all`. `exclude:` (ids or globs) beats globs and `all`. Excluding an explicitly listed id is an error. An id matched twice with conflicting overrides is an error.
- **Eligibility**: explicit `id:` required. Globs and `all` skip `internal: true` and `entity_category: config|diagnostic`; an explicit id may expose them (warning).
- **Unknown id** → error `matter.expose: no entity with id 'x'` with nearest-match suggestions. **Unmappable domain** (button, number, select, text, text_sensor, …) → error with the reason. Under `all`, these are skipped and the reason is printed.
- **Lowering**: one dict per planned endpoint, written into `config[endpoints]` under the §7 number and validated by the unchanged `ENDPOINT_SCHEMA`. Merge groups and `attach_to` produce one dict with several bindings. The item's `features:` becomes the IR's `with_features:`, `options:` merges into the device-type block, and `label:` becomes the endpoint `label:`.
- **FINAL_VALIDATE re-check** (read-only): compare the raw-config inference with the validated entity (`internal`, `entity_category`, platform default `device_class` such as internal_temperature's, light platform and color keys, unit). A mismatch warns and names the `as:` fix.
- **Plan output**: INFO table at `esphome config` and in `dump_config()`: endpoint, device types, entities, label, skipped entities with reasons, ecosystem warnings (Apple drops valve/soil; Google needs a console project for 0xFFF1), heap estimate.
- **Bare `matter:` error text**:
  ```
  matter: nothing to expose. Add one of:
    expose: [relay, desk, t, h]      # inferred from your config; edit as needed
    expose: all
    expose: []                       # root node only
  ```
- **`expose: all`** resolves conservatively from the raw config at schema time and warns above 16 endpoints (heap).

## §6 Inference rules

Inputs: domain, raw platform, `device_class` (else unit), traits from raw keys. Every row is overridable with `as:`.

| Input | Device type (id) | Clusters / features | Notes |
|---|---|---|---|
| `switch` (any class) | on_off_plug_in_unit (0x010A) | OnOff; StartUpOnOff writable | `as: on_off_light` (0x0100); `as: mounted_on_off_control` lowers to DeviceTypeList [0x010F, 0x010A] (superset rule, [verify Apple/Google]) |
| `light` binary | on_off_light (0x0100) | OnOff | |
| `light` monochromatic | dimmable_light (0x0101) | LevelControl (OnOff, Lighting) | |
| `light` color_temperature, cwww | color_temperature_light (0x010C) | ColorControl CT; physical mireds from kelvin | |
| `light` rgb, rgbw | extended_color_light (0x010D) | ColorControl XY+HS | |
| `light` rgbww, rgbct | extended_color_light (0x010D) | XY+HS+CT | |
| `light` addressable (esp32_rmt_led_strip, neopixelbus, fastled_*) | extended_color_light | XY+HS, +CT only if a white channel is declared | other platforms (partition, external) → error asking for `as:` |
| `sensor` temperature (°C/°F/K) | temperature_sensor (0x0302) | TemperatureMeasurement | unit conversion via sensor_mapping |
| `sensor` humidity | humidity_sensor (0x0307) | RelativeHumidityMeasurement | |
| `sensor` pressure | pressure_sensor (0x0305) | PressureMeasurement | |
| `sensor` illuminance | light_sensor (0x0106) | IlluminanceMeasurement | |
| `sensor` volume_flow_rate | flow_sensor (0x0306) | FlowMeasurement | |
| `sensor` carbon_dioxide, carbon_monoxide, pm1, pm25, pm10, volatile_organic_compounds(_parts), nitrogen_dioxide, ozone | **merge group `group:air_quality`** → air_quality_sensor (0x002C) | *ConcentrationMeasurement, NumericMeasurement; MeasurementUnit from unit | AirQuality stays Unknown unless `options: {air_quality: <id>}` |
| `sensor` power, energy, voltage, current, apparent_power, frequency, power_factor | **Electrical Sensor (0x0510) on the plug endpoint** | PowerTopology (NODE), EPM (0x0090) and/or EEM (0x0091) | plug = `meters:` target, else the single exposed switch; with no switch, a standalone endpoint keyed `group:electrical` |
| `sensor` battery (%) | **Power Source (0x0011) on EP0** | PowerSource BAT; BatPercentRemaining = 2×%; EndpointList = all application endpoints | `attach_to:` moves it to an entity's endpoint |
| `sensor` moisture, other classes, or no class | not inferred | | soil_sensor via `as:` (Apple rejects it) |
| `binary_sensor` door, window, opening, garage_door | contact_sensor (0x0015) | BooleanState, inverted (true = closed) | `options: {inverted: …}` |
| `binary_sensor` motion, occupancy, presence | occupancy_sensor (0x0107) | OccupancySensing; sensor-type feature from class (existing table) | |
| `binary_sensor` moisture | water_leak_detector (0x0043) | BooleanState | |
| `binary_sensor` cold | water_freeze_detector (0x0041) | BooleanState | |
| `binary_sensor` smoke, carbon_monoxide, gas | not inferred (life safety) | | `as: smoke_co_alarm` |
| `binary_sensor` with no class or another class | **never inferred** | | `as: generic_switch` / `contact_sensor` |
| `event` | generic_switch (0x000F) | Switch MS (+MSR, +MSL for long/hold types, +MSM with multi_press_max from double=2/triple=3) | unknown event_types → error asking for `options:`; needs the `event_id` binding (M3) |
| `cover` | window_covering (0x0202) | Lift (+PA_LF if position), Tilt (+PA_TL if tilt) | garage_door, gate, door classes warn |
| `fan` | fan (0x002B) | MultiSpeed from speed_count, Rocking from oscillation, AirflowDirection from direction | |
| `climate` | thermostat (0x0301) | Heating/Cooling/AutoMode from modes | `as: room_air_conditioner` (0x0072) → ONE endpoint, OnOff + Thermostat + FanControl bound to climate fan modes |
| `lock` | door_lock (0x000A) | DoorLock | |
| `valve` | water_valve (0x0042) | ValveConfigurationAndControl | warns that Apple rejects it |
| button, number, select, text, text_sensor, datetime, alarm_control_panel, media_player, update | not inferred | | no faithful mapping |

## §7 Endpoint numbering

1. **Key.** A single-entity endpoint uses the entity's `id` string exactly as written after substitutions (`relay_1`). A merge group uses `group:<name>` (`group:air_quality`, `group:electrical`). `was: old_id` uses `old_id` as the key, which keeps a renamed entity's number. `attach_to` items add clusters to another key's endpoint and get no number. Aggregators (M5) use `aggregator`.
2. **Hash** (normative, matches the spike):
   ```python
   def endpoint_for_key(key: str) -> int:
       h = 0x811C9DC5
       for b in key.encode("utf-8"):
           h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF   # FNV-1a 32
       h = (h >> 16) ^ (h & 0xFFFF)                  # fold to 16 bits
       return h % 65534 + 1                          # 1..65534 (never 0 or 0xFFFF)
   ```
3. **Resolution.** Collect pins (`endpoints:` keys, item `endpoint:`); duplicate pins → error. Hash every unpinned key. Any equality between a hash and a pin or another hash → **hard error**: `matter: endpoint 47922 collides: e84 and e276. Pin one: {id: e276, endpoint: 47922} keeps it; e84 could use 6810 (free).` The suggestion is printed only, never applied. There is no salting and no probing.
4. **Pins.** Range 1..65534. Endpoint 0 is the root and is rejected. Hand-written `endpoints:` numbers are pins, so existing configs keep their numbers.
5. **Edits.** Adding, removing or reordering entities never moves another entity. Renaming an id moves its endpoint unless `was:` is set; the plan flags "new key" and the boot log (§8) shows endpoints added and removed. Collision odds: ~1% at 50 exposed entities, ~3% at 100 (spike simulation). They are caught at compile time, so they cost a pin, never a silent renumber.
6. **Fallback (only if §13 G2/G4 fails on a major controller).** Default switches to an on-device allocator: dense IDs from 1, NVS table key→id, tombstones never reused, `endpoint:` pins still win. `esphome config` prints "predicted for a fresh device". Designed but not built unless triggered (TASKS T15).

## §8 Structure hash and ConfigurationVersion

- **When**: once per boot, after all endpoints and clusters are created and **before** `esp_matter::start()`.
- **Canonical walk** (runtime, so esp-matter version changes are caught too): for each endpoint ascending: `id`, `parent_endpoint_id`, DeviceTypeList as (id, revision) sorted, then each server cluster ascending: cluster id, FeatureMap value, sorted attribute ids, sorted accepted-command ids; then sorted client cluster ids. Serialize as little-endian fixed-width integers with a type byte per record, and hash with FNV-1a 64. Attribute *values* are excluded, except FeatureMap and TagList *presence*.
- **Storage**: NVS namespace `esphm`, key `struct_h` (u64), plus `struct_ep` (blob of (u16 endpoint, u32 primary device type), max 64 entries) for the diff log.
- **Bump rule**: let `stored` be the stored hash.
  - `stored` present and ≠ current: `ConfigurationMgr().GetConfigurationVersion(v)` then `StoreConfigurationVersion(v+1)`, under the CHIP lock, before start.
  - `stored` absent and fabric count > 0 (upgrade from a pre-hash firmware): bump once.
  - `stored` absent and not commissioned: no bump.
  - Then write the current hash and endpoint list, and log `matter: structure changed (v→v+1): +ep 18232 on_off_light, -ep 29131`.
- Factory reset keeps `esphm`; CHIP resets ConfigurationVersion; the hash matches, so no bump.
- Verify on QEMU + harness: change a config, reflash, read BasicInformation.ConfigurationVersion (0x0028/0x0018), expect +1; if the esp-matter attribute store and CHIP `ConfigurationManager` disagree, update both.
- **Bridged nodes** (M5) carry no per-node ConfigurationVersion in v1.

## §9 Labels and tags

- **Duplicate rule** (BaseDeviceType.xml:87-96): if an endpoint shares any application device type with a sibling (same parent), it MUST carry Descriptor TagList (TAGLIST feature).
- **Auto tags**: Common Number namespace 0x07, tag = the trailing integer of the key if every member of the duplicate set has a unique trailing integer in 0..30; otherwise the 1-based ordinal in natural-sort order of keys. Ordinals are stable when members are added only if trailing numbers are used, so the plan warns otherwise.
- **SemanticTagStruct.Label** = the endpoint label (≤64 chars), set on auto tags.
- **Explicit** `tags: [{namespace: common_position, tag: left, label: …}]` (1..6 entries, names from the 1.4.2 namespace XML) replaces auto tags. Explicit tags on non-duplicates are allowed.
- **Endpoint `label:`**: defaults to the entity `name:` for lowered endpoints, else none. Emitted as FixedLabel (0x0040) LabelList `[{label: "name", value: <label truncated to 16>}]` when `matter: fixed_labels:` is `duplicates` (default: only endpoints in a Duplicate set), `all`, or `none`. A truncation warns. HA reads FixedLabel as a name modifier; Generic Switch rev 2+ relies on TagList.

## §10 Composition hygiene

- Every endpoint gets an explicit parent: 0 for top level, the aggregator for bridged nodes, `parent:` for trees. Applied after all endpoints exist; cycles and missing parents are compile errors.
- `CONFIG_ESP_MATTER_MAX_DYNAMIC_ENDPOINT_COUNT` = total endpoints including root (minimum 2). `CONFIG_ESP_MATTER_MAX_DEVICE_TYPE_COUNT` = max device types on any endpoint, minimum 2. Verify both on QEMU boot.
- The `_node_t` private-header mirror gets a test that reads the resolved esp-matter source and asserts the struct prefix.

## §11 Onboarding and coexistence

1. **Passcode persisted until first commissioning**: a random passcode, discriminator, salt and verifier are generated once and stored in NVS. The plaintext passcode is kept only while the fabric count is 0 and is erased on the first commissioning (CommissioningComplete). When the last fabric is removed or on factory reset, a new passcode is generated. `persist_passcode: true` keeps today's always-persist mode; `persist_passcode: false` keeps per-boot RAM-only codes.
2. **Behaviours** (standalone, optional): `status_led: <output|light id>` (window open: 1 Hz blink; identify: fast blink; unprovisioned: slow pulse); `pairing: {button: <binary_sensor id>, open_window_hold: 5s, factory_reset_hold: 15s}`. `matter.factory_reset` is fixed (CHIP lock, erase the esp_matter KVS too).
3. **Native-API entities** (user-declared platforms, nothing auto-registered): `button: platform: matter` with `type: open_commissioning_window | factory_reset`; `text_sensor: platform: matter` gains `controllers` (fabric VendorID→name, Label); `sensor: platform: matter, type: fabric_count`. Action `matter.remove_fabric: {fabric_index | vendor_id}`. Triggers `on_commissioned` and `on_fabric_removed`.
4. **SerialNumber** defaults to the MAC from ESPHome's `get_mac_address_raw()` (fixes the H2). UniqueID stays random and persisted.
5. **One path per entity**: if `api:` and `matter:` are both present, print an INFO note at compile time ("don't add this device to Home Assistant's Matter integration; HA uses the native API; duplicates can't be merged") and link docs/home-assistant.md.
6. **Packages**: `packages/matter.yaml` (component, esp-idf, size sdkconfig, preflight) and example packages that only set `expose`/behaviours.

## §12 Bridge (explicit)

- **IR (M5a, spike-bridge as is)**: `aggregator: {}`; `bridged_node: <name>` or `{name, parent, unique_id, reachable: <binary_sensor id>}` on the same endpoint as the application type. NodeLabel ≤32 chars. UniqueID defaults to `<esphome name>-<key>` and is hashed above 32 chars. Parent links come from §10. More than 50 nodes warns (Alexa).
- **Sugar (M5b)**: `matter: layout: bridged`. An aggregator at key `aggregator`, plus one bridged node per exposed entity, or per ESPHome `devices:` sub-device, with NodeLabel = sub-device or entity name. The boot log warns loudly when the stored structure (§8) shows a layout change on a commissioned node.
- Dynamic/foreign-device bridging: out of scope (own RFC, after the esp-matter `min_uu_ep_id` issue).

## §13 Migration

- Configs with `endpoints:` keep working byte-identically, apart from the D5 conformance additions, which change the generated code deliberately; snapshots are regenerated once. Their numbers are pins.
- `features:` → `with_features:`, `extra_clusters: [X]` → `clusters: {x: {}}`: warn with exact rewrite; removed after ~6 months.
- `endpoints: {0: …}` → error ("0 is the root node; use `matter: power_source:` for a node battery").
- Bare `matter:` → error (§5). Add `expose: [...]`, or `expose: []` for a root-only node.
- Moving an existing endpoint to `expose` without re-pairing: `expose: [{id: relay, endpoint: 1}]`, and delete the `endpoints: 1:` block.
- First boot after upgrade bumps ConfigurationVersion once if commissioned (§8).

## §14 Verification gates

| Gate | What | Where | Blocks |
|---|---|---|---|
| G0 | `pytest` (unit + golden codegen snapshots) and `tests/validate.sh` with `VALIDATE_GENERATE=1`, on ESPHome 2026.6.0 / 2026.7.4 / dev | CI | every task |
| G1 | **Static conformance checker** (`tools/conformance`, branch tool-conformance) on every `tests/configs/*.yaml`: device-type mandatory clusters and features (spec XML 1.5/1.6), Duplicate→TagList, parents and PartsList, no endpoint 0 in IR, counts vs sdkconfig, ≤6 tags, label lengths | CI (pytest) | M0 exit, every IR change |
| G2 | **QEMU + matter.js harness** (`tools/qemu`, `tools/harness`): boot the firmware (openeth), commission on-network, read the Descriptor tree, DeviceTypeList, TagList, FixedLabel and ConfigurationVersion; scenarios: sparse IDs (a)–(d), add/remove/reorder, rename with `was:`, structure-change bump | CI (nightly/manual) | default numbering (M2) |
| G3 | **CHIP TCs** (wheels built from the local connectedhomeip): `TC_DeviceBasicComposition`, `TC_DeviceConformance`, `Test_TC_DESC_2_1`, plus per-cluster TCs for the touched clusters (`TC_OO_2_7`, `TC_SWTCH`, `TC_TSTAT_*`, `TC_FAN_*`) against QEMU, then a C6 | manual + nightly | M2 exit, M3 exit |
| G4 | **Hardware matrix**: C6 (Wi-Fi, Thread), C3, H2 × HA (matterjs-server), Apple, Google, Alexa/SmartThings (best effort), scenarios (a)–(e): sparse IDs, edit survival, ConfigurationVersion pickup, tag/label rendering, battery display, bridged naming → docs/ecosystems.md | human | M2 default, M5 bridge |

Gate rule: hard-to-reverse defaults (D4 numbering, D6 bridge sugar) ship only after G2 and G4 pass; a G4 failure on HA, Apple or Google triggers the §7.6 fallback.

## §15 Milestones

- **M0, green and conformant base** (no new UX): Action::play const-ref fix; golden codegen snapshots; upstream DavidvtWout merge with aliases and endpoint-0 rejection; structure hash + ConfigurationVersion; explicit parents, exact counts, `_node_t` guard; auto TagList, `tags:`/`label:`, FixedLabel; conformance checker (G1) in CI. Exit: G0 and G1 green; existing configs unchanged except the deliberate conformance additions.
- **M1, `expose:` v1**: `expose/` package (selectors, globs, exclude, overrides, hashing with collision errors, `was:`, bare-`matter:` error, plan output), inference table §6 for already-bound domains with the air-quality merge, FINAL_VALIDATE re-check, `expose: all`, entity-addressed actions (`entity:` in send_command/set_attribute/on_attribute). Exit: snapshot showing expose ≡ hand-written; G1 on lowered configs.
- **M2, numbering gate**: G2 sparse-ID and edit scenarios on QEMU, G3 composition TCs, G4 on HA, Apple and Google; heap and flash measured on C3 and C6 (13 endpoints). Exit: go (hash default) or fallback (§7.6, task T15).
- **M3, composition and inference corrections**: Electrical Sensor on the plug endpoint (`meters:`); node Power Source on EP0 with EndpointList; room AC with Fan Control; `event` → generic_switch binding; mounted_on_off_control superset. Exit: G1 + G3 on the scenario configs.
- **M4, onboarding and coexistence**: passcode persistence, `status_led`/`pairing` behaviours, factory_reset fix, native-API pairing/fabric entities, `remove_fabric`, controllers sensor, SerialNumber from MAC, one-path note, `packages/` and examples, README quickstart around `expose`. Exit: a beginner pairs scenario (a) with Apple Home using only the README.
- **M5, bridge and beyond**: spike-bridge `bridged_node:` IR, `layout: bridged`, G4 naming matrix; incremental runtime consolidation after measurement; an RFC for an ESPHome entity-extension hook, then the per-entity `matter:` key reusing the expose item schema.

Milestones group releases; `depends_on` in TASKS.json governs scheduling (most M4 tasks run in wave 1).
