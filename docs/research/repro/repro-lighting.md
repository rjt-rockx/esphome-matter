# Reproduction report: lighting

Branch `repro-lighting` (worktree), examples in `examples/devices/lighting/`. Method: write the YAML a user would write, run
`python -m tools.conformance <yaml> --emit-dump` (predicted node model from the exported layout + esp-matter profile) and diff it
against the real HA dump (ids from `ha-fixtures.jsonl`, flat maps in `ha-fixtures-flat/`). Diff script: scratchpad `lighting/rd.py`
(device types + revisions, server list, feature maps, attribute/command lists, clients, tags, parts).
Values (ColorTempPhysicalMin/Max etc.) are not in the predicted dump; they were checked in `endpoints.py` codegen (taken from the
ESPHome light traits for color_temperature_light / extended_color_light).

Setup note: merging `t08-expose` into the base needed a one-line glue fix (components/matter/expose/inference.py imported
`CONF_FEATURES`, which the base renamed to `CONF_WITH_FEATURES`) and `sdk-gaps` needed `layout_dump.dump_layout(CORE.data[...][CONF_ENDPOINTS])` in
`register_endpoints`. Both are in the first commit. Real-device version differences (root node r4 vs r1, device-type and cluster revisions, ScenesManagement
being added by the newer device type revisions, Wi-Fi vs Ethernet NetworkCommissioning) show up in every diff and are classified ACCEPTABLE once here.

## Per-device results

Legend: EQ equivalent, ACC acceptable, GAP-C component gap, GAP-SDK, REAL-NC real device nonconformant.

| Device (evidence) | YAML variants | Result |
|---|---|---|
| extended-color-light (HA mock, dump) | `extended-color-light-expose`, `-endpoints` | Device type, OnOff[LT], LevelControl[OO,LT], ColorControl[HS,XY,CT] EQ. ModeSelect missing: GAP-C (select not mapped). ScenesManagement, newer revisions, FixedLabel (expose adds the entity name as label): ACC. Real device has ColorLoop*/EnhancedCurrentHue attributes without the features: REAL-NC. Real BridgedDeviceBasicInformation on a non-bridged node: REAL-NC. |
| color-temperature-light (HA mock, dump) | `-expose` (cwww), `-endpoints` (`color_temperature` platform, min_level) | CT feature only, physical range from traits (6500 K/2000 K = 153/500): EQ. Real also carries Hue/Sat/X/Y attributes with no feature: REAL-NC. ModeSelect GAP-C. |
| mock-dimmable-light (dump) | `dimmable-light-expose`, `-endpoints`, `-ac-dimmer` | EQ except: LevelControl OnOffTransitionTime/OnTransitionTime/OffTransitionTime/DefaultMoveRate/frequency attributes not created (GAP-C G3), Groups GN feature (ACC, `group_names` feature exists), empty EP6, root Switch/FixedLabel/UserLabel (REAL-NC leftovers). |
| mock-onoff-light (dump) | `onoff-light-relay-expose` (switch + `as: on_off_light`), `onoff-light-binary-endpoints` | EQ. Real lacks GlobalSceneControl/OnTime/OffWaitTime/StartUpOnOff although OnOff has LT: REAL-NC. Plain relay needs `as:` (default is plug-in unit): UX note. |
| onoff-light-with-levelcontrol-present (Leviton D215S, dump) | `_gaps/onoff-light-with-level-control` | BLOCKED by B1 (`clusters:` KeyError). With the fix applied locally the result needs `level_control: with_features: [on_off, lighting]` (checker error DT-FEATURE-MISSING otherwise) and then matches except OnOffTransitionTime/DefaultMoveRate (G3). Also: `light_id` on an on_off_light never drives level (G2). |
| mock-dimmable-plugin-unit (dump) | `dimmable-plug-in-unit-custom` (on_attribute + set_attribute), `_gaps/...-expose` | `light_id`/`as:` rejected: GAP-C G1. Custom form reaches structure EQ (no transitions, no level restore, no min_level). |
| mock-mounted-dimmable-load-control (dump) | `mounted-dimmable-load-control-custom`, `_gaps/...-light-id` | Same as above (G1). esp-matter adds ScenesManagement and Min/MaxLevel that the mock lacks: ACC. |
| inovelli-vtm31 (VTM31-SN 2-1 dimmer, dump) | `inovelli-2in1-dimmer-endpoints`, `-expose` (mixed expose + one endpoint) | All six application endpoints, device types, Switch[MS,MSR,MSL,MSM], dimmer_switch clients [Identify, OnOff, LevelControl], labels: EQ. Auto Common Number tags on the three buttons (real has none): ACC. ModeSelect on every endpoint and manufacturer cluster 0x122FFC31 and OTA Requestor device type: GAP-C. Identify TriggerEffect missing on non-light endpoints: ACC. Real LED bar LevelControl has no features: REAL-NC. |
| silabs-light-switch (dump) | `light-switch-controller` | on_off_light_switch + generic_switch EQ. Client ScenesManagement/ColorControl and ICD: GAP-C G6/G9 (optional clients not creatable; ICD out of scope). Root Binding is nonstandard: REAL-NC. |
| silabs-range-hood (dump) | `range-hood-fan-light-endpoints` | Fan + on_off_light siblings. Extractor Hood has no `fan_id` (uses `fan` device type instead): GAP-C G7. On/Off light extra attributes (StartUpOnOff etc.) are the conformant ones: real REAL-NC. |
| tuya-rgbcct-strip (inferred) | `rgbcct-strip-endpoints` (rgbww, HS+EHUE+CL), `rgbcct-strip-addressable-expose` | FM 31 = HS,EHUE,CL,XY,CT as in the catalog: EQ. Addressable strip needs `as:`; RGB-only light still advertises CT (G8). |
| sdk-chef dimmable light + occupancy binding | `_gaps/dimmable-light-occupancy-binding` | Needs Binding server (B1) and an OccupancySensing client: GAP-C G6. |

## Prioritized GAP list

| # | Gap | Devices | Proposed fix | Size | Milestone |
|---|---|---|---|---|---|
| B1 | `clusters: <name>:` crashes `esphome config` (KeyError `'level_control'`: `endpoints.py` `resolve()` and `register()` index `CLUSTERS_BY_NAME` with the snake_case conf key). Documented in generated/device_types.md, never exercised by a test or example. Verified: `CLUSTERS_BY_CONF_KEY[...]` (and `_cluster_configs[cluster.name]`) at the two sites fixes it. | onoff-with-level, occupancy binding, anything with optional clusters | Fix lookup, add a test config with `clusters:`. | S | P2 (conformance/engine) |
| G1 | `light_id` only on 4 light device types. `dimmable_plug_in_unit`, `mounted_dimmable_load_control`, `on_off_plug_in_unit`+LevelControl, `mounted_on_off_control` cannot bind a light. The error is the generic "[light_id] is an invalid option for [dimmable_plug_in_unit]" and `expose: as:` offers no better hint. | 2 dumps | Allow `light_id` on those types (reuse matter_lights with level_control); better error naming allowed types. | M | expose / light binding |
| G2 | `on_off_light` + LevelControl: level cluster would be unbound (light mapping decided by device type). | Leviton D215S | Bind level when LevelControl is present on the endpoint. | M | |
| G3 | No way to create optional LevelControl attributes (OnOffTransitionTime, OnTransitionTime, OffTransitionTime, DefaultMoveRate, frequency). An `on_attribute` usage does not create them, so lights.md "uses OnOffTransitionTime if the attribute exists" is dead in practice; every real dimmer has them. | all dimmable devices | Create these when `light_id` is bound (default OnOffTransitionTime 0.5 s) or add `with_attributes:`. | S/M | lights |
| G4 | ModeSelect not mappable (`select`): present on every Inovelli/Signify endpoint. | 4 dumps | Planned `select` mapping. | L | later |
| G5 | Optional LevelControl/OnOff on any endpoint via `clusters:` has no attribute opt-ins. | | see G3 | | |
| G6 | Client clusters (OccupancySensing, ScenesManagement, ColorControl client; Binding on light) not creatable. | binding chef, light-switch | Allow client clusters in `clusters:` with `side: client`. | M | bindings |
| G7 | `extractor_hood` has no `fan_id`. | range hood | Accept `fan_id`. | S | fan |
| G8 | RGB-only light (no color temperature range) as extended_color_light still has CT feature (device type requires it); physical mireds default. | WS2812 strips | Document or derive ColorCapabilities; spec forces CT, acceptable. | doc | |
| G9 | ICD / OTA Requestor device type on root. | switches | out of scope | | |

## YAML UX notes

- Feature names are inconsistent: `Hue And Saturation` works but `Enhanced Hue` and `Color Loop` do not (`EnhancedHue`/`enhanced_hue` needed). docs/lights.md says all forms work. Error message helps ("did you mean").
- Plain relay lamp: `expose: [relay]` silently becomes a plug; `as: on_off_light` needed. Addressable strips and any non-listed light platform also need `as:`.
- `expose` copies the entity name into a FixedLabel on every endpoint (even single-endpoint nodes); explicit `endpoints:` do not. Lowered config prints `clusters: {}` and default `min_level: 1`/`max_level: 254` noise.
- Strapping pin warnings on C6 for GPIO4/5/8/15 (examples use 10/11/18/19). `rgb_order` is deprecated for `channel_colors` in ESPHome dev.
- The checker baseline entry "Descriptor needs TagList for duplicate siblings" is stale: the component now adds Common Number tags, but the static dump does not model TagList/FixedLabel contents, so it still reports KNOWN for the three Inovelli buttons.
- The dump model is structural only: ColorMode/ColorCapabilities/ColorTempPhysical values and transitions are not verified.
