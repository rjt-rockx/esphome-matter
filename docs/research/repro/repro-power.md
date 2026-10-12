# Reproduction report: plugs, strips, relays, energy meters, on/off switches

Branch `repro-power` (base `claude/tender-rubin-39rgtk` + `sdk-gaps` + `t08-expose`; one merge fixup: `expose/inference.py` imports `CONF_FEATURES` from `.schema`).
Examples: `examples/devices/power/*.yaml` (29 validating configs) and `examples/devices/power/gaps/*.yaml` (6 probes: 4 expected to fail `esphome config`, 2 expected non-conformant or unverified; not run by `VALIDATE_DIRS=examples/devices/power`).
Method: `python -m tools.conformance <yaml> --emit-dump`, endpoint plans (tags, parents, labels) captured with a `sitecustomize` hook (the layout export lacks them, gap G4), structural diff with `devices/tools/repro_diff.py` against the HA dump (evidence=dump) or the catalog entry (doc/inferred). Diffs: `/tmp/r/diffs/` (regenerate with `/tmp/r/diffall.py`).
Classes: EQ equivalent, ACC acceptable, GC gap-component, GS gap-sdk, RDN real-device-nonconformant.

## Per-device table

| Example (variants) | Real device / evidence | Result | Key differences and class |
|---|---|---|---|
| eve-energy-plug | Eve Energy, dump | conforms | EQ: plug dt, OnOff LT, Thread NetworkCommissioning TH. ACC: no Eve vendor cluster 0x130AFC01 (metering is vendor-specific there). ACC: ours adds ScenesManagement, lacks Groups GN. ACC: dt revision 4 vs 1. ACC: Thread diagnostics feature bits (PKTCNT...) absent. ACC: hashed endpoint number 38775 vs 1 (pin with `endpoint:`). |
| eve-energy-plug-metered-endpoints / -oneep | Eve plug "patched" (HA-edited), dump | conforms, 1 warning | (b) sibling electrical_sensor ep2 = real shape. (c) one endpoint, dts [plug, electrical_sensor] (D11). ACC: Power Topology NODE. GS: esp-matter creates `CumulativeEnergyExported` without the ExportedEnergy feature (CL-ATTR-NA, every metered endpoint). |
| eve-energy-20ecn4101 (-endpoints, -expose-default) | Eve Energy 2 outlet, dump | conforms | RDN: real has no TagList on its two identical plugs. Ours adds Common Number 1/2 (D5). GC: real labels `id`+`orientation` (FixedLabel multi-entry); ours has single `name` label. Alternative (b) uses Common Position top/bottom tags, spec-better. |
| tapo-energy-plug (+gaps/ expose probe) | Tapo energy plug, doc/inferred | conforms | Plug + EPM(ALTC) + EEM(CUME,IMPE) on ep1 as inferred. ACC: ours adds Power Topology NODE (required by device type), Groups, Scenes. GC: the `expose:` form fails (G1). |
| sonoff-s31 | Sonoff S31, doc | conforms | One endpoint with apparent power and power factor. EQ. |
| tuya-wp35-power-strip (-endpoints) | Tuya WP35 strip, doc/inferred | conforms | 5 sibling plugs. ACC: tags Number One..Five, labels. **GC: tag numbers follow endpoint-id order, not YAML order (G2).** |
| tapo-p316m-siblings / -composed | Tapo P316M, doc (SmartThings thread) | conforms, 6 warnings | siblings: ep1-6 plugs + ep7-12 sensors identical to the real node. composed: 6 endpoints, meter on each plug. ACC: sensor endpoints get Power Topology NODE x6 [verify spec allows several NODE]. |
| power-strip-metered (+gaps/power-strip-tree-metering) | spec-derived strip, inferred | conforms | GC: real spec form has TREE topology on the total with children; ours only NODE (G3). The gaps probe (`with_features: [tree_topology]`) compiles but yields NODE+TREE: tools.conformance CL-CHOICE error and no config-time error. Parent/PartsList correct. |
| shelly-plus-1pm (-expose) | Shelly Plus 1PM, doc | conforms | ep1 plug+meter, ep2 generic_switch (input), ep3 temp. ACC: Generic Switch gets MS/MSR/MSL/MSM (doc shows MSR+MSM). -expose form loses the meter (G1) but infers temp (ntc) and `as: generic_switch` works. |
| shelly-plus-2pm | Shelly Plus 2PM, doc | conforms, 4 warnings | Two sibling plugs + meters + temp. ACC: EEM CUME+IMPE vs CUME. Tags assigned automatically. |
| sonoff-4ch (-endpoints) | Sonoff 4CH, inferred | conforms | Four plugs with tags. **Hash order gives `relay_4` tag One (G2).** |
| sonoff-tx-wall-switch (-buttons) | Sonoff TX style, inferred | conforms | `as: mounted_on_off_control` works for glob. GC: real composition has the Switch cluster on the same endpoint as the load; spec-clean alternative uses separate generic_switch endpoints (-buttons). Superset DeviceTypeList [0x010F,0x010A] (D11) not available (G7). |
| wago-home-relay-16a (-expose) | WAGO Home Relay 16A, dump | conforms | EQ ep2 generic_switch MSM max 2. ACC: OnOff LT present (real has no LT, no Scenes); StartUpOnOff etc. extra. |
| silabs-light-switch | Silabs sample, dump | conforms | EQ on_off_light_switch + generic_switch. GC: client clusters limited to Identify+OnOff (real also ScenesManagement, ColorControl clients) and no Groups server on the switch (G8). ACC: Switch features MS/MSR/MSL/MSM vs MS/MSR. |
| inovelli-vtm30 | Inovelli VTM30, dump | conforms (partial repro) | Reproduced: load, 3 buttons, meter. GC: real load is plug-in unit + LevelControl (we use dimmable_light, no ModeSelect/UserLabel/vendor cluster); no dimmer_switch client co-existing with plug; RGB LED endpoint, humidity, temperature not attempted. GC: real button tags use ns 0x43 (Switches) positions; ours Common Number only unless `tags:` given. RDN-ish: LevelControl attrs OnOffTransitionTime etc. missing. |
| atios-electricity-monitor (+gaps/...-bridged) | Atios ADE-KD behind KNX bridge, dump | conforms | Standalone electrical_sensor = EQ to the leaf's measurement clusters. Bridged form (aggregator ep1, ep29 bridged_node+electrical_sensor, parent 1) matches the dump's structure exactly. GC: BridgedDeviceBasicInformation attributes (VendorName, ProductName, NodeLabel, SerialNumber...) not populated (G6). |
| yandex-smart-socket | Yandex socket, dump | conforms | GC: legacy ElectricalMeasurement 0x0B04 and vendor clusters not expressible; replaced by modern Electrical Sensor. RDN: real uses the deprecated cluster. |
| olimex-esp32-poe-relay | doc | conforms | Ethernet transport; tags follow hash order (G2). |
| wall-dimmer-dimmable-light | mock dimmable light, dump | conforms | EQ dimmable_light via inference. |
| wall-dimmer-mounted-dimmable-load-unbound (+gaps x2) | chef mounted dimmable load, dump | conforms but unbound | GC: `light_id` rejected on mounted_dimmable_load_control and dimmable_plug_in_unit (G5). |

## Prioritized GAP list

| # | Gap | Devices | Proposal | Size | Milestone |
|---|---|---|---|---|---|
| G1 | `expose:` cannot express metering: power/energy/voltage/current/apparent_power/frequency/power_factor sensors have no rule; `meters:` is reserved. Error is clear but there is no way forward. | every metered plug, strip, Shelly, S31, Tapo, Yandex | Rule family: electrical sensors merge into the plug endpoint (`meters: relay`) or a standalone `group:electrical` endpoint; infer `current_type`, features from the sensors. Example: `expose: [relay, {id: mains_power, meters: relay}]` or auto-attach when exactly one switch is exposed. | M | M3 (DIRECTION s6 electrical row) |
| G2 | **Common Number tag numbers follow endpoint-id order.** With hashed ids, `relay_4` can get tag One and `relay_1` Two (sonoff-4ch, tuya strip, olimex). Labels say "Relay 4" with tag "One". Wrong for controllers that render tag names. | all multi-outlet `expose:` configs | Assign by expose order (YAML/natural id order) for hashed ids, or derive from trailing digits; keep endpoint-id order only for explicit `endpoints:`. | S | M0/D5 follow-up |
| G3 | Power Topology is fixed to NODE. `with_features: [tree_topology]` is accepted by `esphome config`, then the node carries NODE+TREE (CL-CHOICE). Several NODE endpoints on one node for per-outlet meters is also questionable. | strips, multi-channel meters | Make the topology selectable (TREE/SET), validate at config time; default NODE only for the first/only meter, TREE for a parent total. Auto-set parents with `meters:` groups. | M | M3 |
| G4 | `MATTER_LAYOUT_JSON` export lacks plans (tags, parents, labels, FixedLabel, Descriptor TagList feature). tools.conformance therefore reports a known-gap "TagList missing" on every sibling set and a flat PartsList. | all sibling configs | Export `CORE.data['matter']['plans']` in `layout_dump`. | S | conformance tooling |
| G5 | `light_id` is unavailable on `dimmable_plug_in_unit` / `mounted_dimmable_load_control` (only names ending in `light`); wall dimmers cannot use the spec-closer types. | dimmers, VTM30 load | Allow `light_id` on any device type that requires LevelControl+OnOff; `switch_id` already works for mounted_on_off_control. | S | M2 |
| G6 | BridgedDeviceBasicInformation data not settable (VendorName, ProductName, NodeLabel, SerialNumber, ...). Bridged node validates structurally. | bridged leaf (Atios) | `bridged_node: {vendor_name:, product_name:, ...}`. | S | M5 |
| G7 | Mounted On/Off Control composition: no Switch cluster on the load endpoint, no superset [0x010F, 0x010A] dt list. | TX wall switches | Support `as: mounted_on_off_control` superset lowering (D11) and optional `button:` binding. | M | M3 (verify controllers) |
| G8 | Client clusters unconfigurable (ScenesManagement, ColorControl clients on a light switch; Groups server on the switch); OnOff LT cannot be disabled; ScenesManagement added to plugs although optional. | silabs switch, Wago | Per-endpoint `client_clusters:` list; feature toggles for OnOff. Low value. | M | later |
| G9 | FixedLabel supports one `name` label only; real devices publish several (`id`, `orientation`) and use UserLabel. Expose derives a label from every entity name (16-byte limit hard error: "Board temperature" failed). | Eve 2-outlet, VTM30 | Truncate or warn for derived labels; allow `labels: {k: v}`; prefer tag position tags. | S | M0 |
| G10 | Legacy ElectricalMeasurement 0x0B04 and vendor clusters absent. | Yandex, Eve | none recommended (deprecated). Document as non-goal. | - | - |
| G11 | esp-matter creates `CumulativeEnergyExported` with IMPE+CUME only. | all metered | Report/patch upstream; checker warning CL-ATTR-NA. | S | GAP-SDK |
| G12 | Hashed endpoint numbers differ from real devices (1,2,..) which makes catalogs and diffs harder; pins work (`endpoint:`). | all expose configs | None; document pinning when mirroring a device. | - | ACC |

## YAML UX notes

- `expose:` is excellent for plugs, strips (glob) and generic switches (`as:`), and the printed plan/INFO lines are useful; but any metered device falls back to `endpoints:` (verbose: 5-8 lines per electrical_sensor, repeating sensor ids).
- Silent acceptance of `tree_topology` producing a non-conformant node, and silent tag/number mismatch (G2), are the main surprises.
- Label default from the entity name bites with 16-byte limit; the error message is fine but arrives at the lowered path for expose.
- Electrical sensor option names (`active_current`, `energy_imported`) differ from ESPHome's (`current`, `energy`); mapping is mechanical and a `meters:` shortcut could infer from `device_class`.
- ESPHome side: several meter chips give no cumulative energy (ADE7953); an `integration` sensor (unit Wh) works with the unit table.
