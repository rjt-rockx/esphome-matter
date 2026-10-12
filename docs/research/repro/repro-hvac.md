# Reproduction report: HVAC (thermostats, room AC, heat pump, fans, ceiling fan + light, air purifier)

Branch `repro-hvac` (worktree agent-a9971b88f1acfd06e), based on `claude/tender-rubin-39rgtk` + `sdk-gaps` + `t08-expose`.
Example YAMLs: `examples/devices/hvac/*.yaml` (validate today) and `examples/devices/hvac/_gaps/*.yaml` (blocked, see B1 / G-list).
Every example carries `# mirrors: <catalog-id>` in line 1 and a header with what matches / differs.

## Method and caveats

- Layout: `python -m tools.conformance <yaml> --keep-layout` (runs `esphome compile --only-generate` with `MATTER_LAYOUT_JSON`), generated `main.cpp` read for parents, labels and binding calls.
- Diff: `--emit-dump` + `/home/user/research/devices/tools/repro_diff.py` against the HA flat fixture (or `jsonl:<id>` for doc/inferred entries). `tools.conformance diff` (branch `tool-devicediff`) was not merged (coordinator decision); the headers are ready for it.
- **Dynamic features.** Thermostat and FanControl features/attributes are derived from the ESPHome entity traits in C++ at runtime (`dynamic_features` in the layout). The static predicted dump therefore shows Thermostat `[]` and no setpoint/limit attributes, FanControl `[]` and no SpeedMax/RockSupport/etc. Every "FEATURES Thermostat / ATTRS Thermostat only REAL" line in the raw diff is this artefact (G6), not a real difference. The classification below uses the trait-derivation rules in docs/climate.md and docs/fan.md instead.
- The static dump also omits tags/parents: the raw diff shows `tags pred None` and empty `parts` for children. The generated code does emit them (`register_endpoint_tag(3, 7, 1, "")` for the W100 switches, `register_endpoint_parent(2, 1)` for children), so those diff lines are not real differences.
- The box was heavily loaded; the diffs were run once each and are not hardware-verified. No device was commissioned.
- Merge fix on this branch (not part of the examples): `components/matter/expose/inference.py` imported the removed `CONF_FEATURES`; changed to `with_features` fallback. Needed for any `esphome config`.

## Per-device table

Classes: EQ equivalent, ACC acceptable, GAP-C component gap, GAP-S esp-matter gap, RDN real device nonconformant.

| Catalog id (evidence) | Our YAMLs | Result | Structural differences |
|---|---|---|---|
| mock-room-airconditioner (dump) | `_gaps/mock-room-airconditioner` (endpoints: RAC + `clusters: fan_control` + child temp EP2 parent 1), `mock-room-airconditioner-expose` | endpoints form blocked by B1; expose form validates | RAC type, OnOff, Thermostat(HEAT/COOL/AUTO derived), parent nesting: EQ. FanControl exists but unbound: GAP-C G1. OnOff has DeadFrontBehavior (DF), dump has none: ACC (RAC requires it in 1.6). Expose: sensor becomes a sibling, no parent/fan cluster: GAP-C G3. FixedLabel added: ACC G9. Dump WND feature: nothing implements it: G10. |
| ir-room-ac-climate (dump of ESPHome model) | `ir-room-ac-single` | validates | One RAC endpoint, Thermostat+OnOff: EQ. Catalog FanControl[MS]: GAP-C G1. IR is assumed-state only. |
| eve-thermo-v5 (dump) | `eve-thermo-v5`, `_gaps/eve-thermo-v5-expose` | endpoints validates; expose fails | Thermostat(HEAT) device type rev 4: EQ. Power Source on EP1 (`battery:`) vs root device type with EndpointList: GAP-C G4 (ACC for HA). Presets PRES, UI config, Binding, vendor cluster: GAP-C G7. Client TemperatureMeasurement (remote sensing): GAP-C G5. ICD, OTA requestor: out of category. Expose: climate needs `as:`, battery has no rule: G2. |
| aqara-thermostat-w500 (dump) | `aqara-thermostat-w500` | validates | Thermostat(HEAT), humidity EP2 flat siblings, parts [1,2]: EQ. Electrical Sensor on EP1 not EP0: ACC (EP0 cannot take an application type). Setback + UI config: G7. Groups on EP1 extra: ACC. conformance warns CL-ATTR-NA CumulativeEnergyExported present with only imported energy: GAP-S (esp-matter creates it). |
| resideo-x2s / nest / esphome-thermostat-heat-cool (dump/doc) | `esphome-thermostat-heat-cool`, `-expose` | validates | Thermostat HEAT+COOL: EQ. AutoMode added because ESPHome heat+cool implies HEAT_COOL: ACC. Groups[GN] missing: ACC. Expose: needs `as: thermostat` (G2); `attach_to: hvac` gives humidity on the thermostat endpoint (works). |
| tado-smart-radiator-thermostat-x (dump) | `tado-radiator-thermostat-x` | validates | Thermostat(HEAT)+RH on one endpoint: achieved as second device type `humidity_sensor` (DeviceTypeList [thermostat, humidity] vs [thermostat]): ACC, controller behaviour [verify]. |
| silabs-fan / mock-fan (dump) | `silabs-fan`, `silabs-fan-expose` | validates | Fan type, MultiSpeed/Rocking/Direction from traits: EQ. AUT (no ESPHome auto): ACC. WND, STEP: not backed by behaviour: GAP-C G10. mock-fan OnOff/LevelControl/ModeSelect on the Fan endpoint: RDN (SDK test app). Expose needs `as: fan`: G2. |
| switchbot-circulator-fan-pid0802 (doc) | `switchbot-circulator-fan` | validates | Fan + FanControl: EQ (hbridge fan, 100 steps). |
| mock-air-purifier / chef air purifier (dump) | `mock-air-purifier`, `_gaps/air-purifier-filters` | purifier validates; filters blocked (B1) | air_purifier + air_quality child merging PM1/2.5/10/CO2 + temp + humidity children: EQ for tree shape. HEPA/carbon filter monitoring: blocked B1 and no entity for Condition: GAP-C G11. Fan not bound (`fan_id` only on `fan`): GAP-C G8. AirQuality enum Unknown, no FAIR/MOD features: G12. Concentration LEV/MED/CRI/PEA/AVG features: ACC. Thermostat EP5: SDK mock only (RDN-ish, not built). |
| longan-link-hvac-thermostat-fan (dump) | `_gaps/longan-link-hvac` | blocked by B1 | Dump = Thermostat type with OnOff+FanControl servers: RDN (not in the Thermostat device type). Ours: RAC with OnOff+Thermostat+FanControl: ACC, more correct. Fan modes unbound G1; FixedLabel/Binding/UI config G7. |
| govee-ceiling-fan-light-pid1310, inovelli-vtm36-fan-light-pid0004 (doc) | `govee-ceiling-fan-light`, `vtm36-fan-light-expose` | validate | Fan + light as siblings: EQ. Groups/Scenes extras: ACC. Expose needs `as: fan`. |
| sonoff-ifan02 (inferred) | `sonoff-ifan02` | validates | Fan(3 speeds) + on_off_light: EQ; Auto: ACC. |
| big-ass-fans / spec-ceiling-fan-with-light (doc) | `ceiling-fan-light-composed` | validates | Light as child of the fan (`parent: 1`): spec-legal, controller rendering unverified (G13). |
| aqara-climate-sensor-w100 (dump) | `aqara-w100-climate-sensor` | validates | Temp, humidity, 3 generic switches (auto TagList), Power Source EP6: EQ. Position tags (ns 8): ACC. ICD: out of scope. The "remote sensing" consumer side is G5. |
| sdk-chef-heat-pump-energy (dump) | `heat-pump-metering` | validates | heat_pump + electrical_sensor on EP1, thermostat/temps as children: ACC alternative. Thermostat client on EP1, Device Energy Management, Power Source on the pump: GAP-C G14. |
| silabs-range-hood-fan-light (dump) | `silabs-range-hood` | validates | extractor_hood + on_off_light siblings: EQ for shape; hood FanControl unbound: G8. |

## Bugs found

- **B1 (S, blocks M0 exit for any extra cluster).** `clusters: {fan_control: {}}` (any key) raises `KeyError: 'fan_control'` during `esphome config`. `components/matter/endpoints.py` `Endpoint.resolve()` (~line 276) and the registration loop (~line 388) look up the snake_case conf key in `CLUSTERS_BY_NAME` (CamelCase). Fix: `CLUSTERS_BY_CONF_KEY[...]`, and `_cluster_configs[cluster.name]`. Verified on a patched scratch copy (RAC+FanControl, Longan, HEPA filters then generate). Being fixed separately.
- Merge: `expose/inference.py` import of `CONF_FEATURES` (fixed on branch).
- `tools.conformance` baseline still reports "TagList missing" KNOWN for sibling sensors/switches although the generated code now adds tags (stale baseline entry; harmless).

## Prioritised GAP list

| # | Gap | Devices | Proposed YAML / feature | Size | Milestone |
|---|---|---|---|---|---|
| G1 | Room AC / thermostat FanControl not bound to the climate fan modes (and swing) | mock RAC, Longan, IR AC | `room_air_conditioner: {climate_id: ac}` auto-creates FanControl from `climate.get_traits().get_supported_fan_modes()` (Auto/Low/Med/High -> FanMode, FanModeSequence, Percent); `clusters: fan_control: {fan_id: x}` for a separate ESPHome fan | M | M3 (room AC with Fan Control) |
| G2 | `expose:` inference missing for climate, fan, battery sensor, air quality group, binary/others | all `-expose` files | register rules per DIRECTION s6: climate->thermostat, fan->fan, battery->node Power Source, air quality merge group | S each | M1 |
| G3 | `expose:` items cannot express `parent:` or `clusters:`; only `attach_to` (works for same-endpoint device types) | RAC+temp child, purifier children | item keys `parent: <id>` and `clusters:`; or `children: [t, h]` shorthand | M | M1 |
| G4 | `battery:` puts Power Source on the application endpoint, not EP0 with EndpointList | Eve Thermo, W100 | root `battery:` / inference of battery sensor | M | M3 |
| G5 | Remote temperature sensing: client TemperatureMeasurement + Binding on the thermostat | Eve Thermo, W500-class | `thermostat: {climate_id, remote_sensor: bound}` using Binding + subscribe (same machinery as client switches, KNOWLEDGE b9b) | L | post-M3 |
| G6 | Static tools cannot see trait-derived Thermostat/Fan/WindowCovering features | all climates/fans | export resolved features in `layout_dump` by evaluating traits at config time where the ID is resolvable, or accept `assume_traits` in conformance | M | M0 (G1 gate) |
| G7 | Thermostat presets, setback, UI configuration, Binding cluster, local temperature calibration | Eve, W500, Longan | `thermostat: {with_features: [presets, setback]}` backed by climate presets; `clusters: thermostat_user_interface_configuration` | M | M3+ |
| G8 | `fan_id` only accepted on the `fan` type | air purifier, extractor hood, RAC | allow `fan_id` on any device type whose FanControl is required/optional (air_purifier, extractor_hood) | S/M | M3 |
| G9 | Every lowered endpoint gets a FixedLabel | all `-expose` | `fixed_labels: duplicates` default (DIRECTION s9) | S | M0/M1 |
| G10 | Fan Wind/Step/Auto features are selectable but unimplemented (Step is always rejected) | silabs-fan, RAC | map Step to speed_count up/down, Wind to presets; or hide the features | S | M3 |
| G11 | Filter monitoring has no entity: Condition/ChangeIndication | air purifier, hood | `filter_monitoring: {hepa: {condition: sensor_id, warning_below: 20%}}` via a sensor binding | M | M3+ |
| G12 | AirQuality enum has no source | purifier | `options: {air_quality: <sensor id or lambda>}` (already planned) | S | M1 |
| G13 | Fan with child light composition unverified in Apple/Google | ceiling fans | G4 hardware matrix; docs/ecosystems.md | doc | M2 |
| G14 | Heat Pump device type has no thermostat link, no Device Energy Management, no node Power Source | heat pump | out of scope short term | L | M5+ |
| G15 | GAP-S: esp-matter creates CumulativeEnergyExported when only imported energy is selected | W500-like | file upstream / hide | S | SDK |

## YAML UX pain

- A climate or fan inside `expose:` needs `as:`; the error for a battery sensor ("use `as:`") points at a key that cannot express it.
- `clusters:` crashes with a Python KeyError instead of a validation message (B1), and the error for FanControl features without the cluster ("does not contain the FanControl cluster required by:" with an empty list) names no fix.
- Silent no-op features: `with_features: [wind]`, `[step]`, `setback`, FanControl on a RAC all compile and do nothing.
- Thermostat+humidity on one endpoint works only through a second device type or `attach_to`; the natural idea (a `humidity:` key on the thermostat) does not exist.
- ESPHome side: `min_cooling_off_time` requires `cool_action`, `fan_mode_*_action` requires `min_fan_mode_switching_time` (cost me two failed first drafts; not a component issue).
