# Reproduction: buttons, remotes, switches, doorbells (branch repro-buttons)

Examples: `examples/devices/buttons/*.yaml` (20 files, all pass `tests/validate.sh`, commit f5bbc2c). Method: `python -m tools.conformance <yaml> -v --keep-layout` (layout incl. parent/label/tags) plus structural diff against the catalog entry. Raw diff outputs (produced with the tool-devicediff `diff` command before the coordinator withdrew that merge; outputs only, the merge was reverted): `repro-buttons-diffs/*.diff.txt`. Negative cases (do not compile): `repro-buttons-negative/`. Local, uncommitted integration fixes needed to run at all: `repro-buttons-local-patches/local-integration.patch`.

Switch event sequences were not examined (other agent).

## Per-device table

| Device (evidence) | YAML variants | Match | Differences (class) |
|---|---|---|---|
| Haojai 6-button Thread (dump) | haojai-6gang-endpoints, -expose | endpoints variant EQUIVALENT: 6x generic_switch, MS/MSR/MSL/MSM, TagList Number 1..6 identical to dump | battery Power Source on EP0 -> separate endpoint (GAP-COMPONENT); ICD (GAP-COMPONENT/SDK); RECHG feature (GAP-COMPONENT); FixedLabel extra on expose variant (ACCEPTABLE). Expose variant: tags scrambled, see G1 |
| IKEA BILRESA dual (dump) | ikea-bilresa-dual-button-endpoints, -expose | 2 generic_switch, feature map, MultiPressMax 2, Position + Switches tags, custom tag label | 4 tags in dump, esp-matter hard cap 3 (GAP-SDK, `#define ESP_MATTER_MAX_SEMANTIC_TAG_COUNT 3`, not Kconfig); battery + OTA Requestor device type on EP0 (GAP-COMPONENT); expose variant has no tags at all (G2) |
| IKEA BILRESA scroll wheel, 9 ep (dump) | ikea-bilresa-scroll-wheel-endpoints | feature maps 22 (rotary) / 30 (button) match, 9 siblings, group tags | dump uses Position:Column with label "1/2/3" to tell groups apart; our checker rejects (G4). Up to 5 tags (GAP-SDK). Rotary steps by hand via matter.send_switch_event (G5) |
| Aqara W100 climate + 3 buttons (dump) | aqara-w100-climate-buttons-endpoints, -expose | 98% on the diff tool: pinned ep 1..6, temp/humidity/3 buttons, Number+Position tags, power_source endpoint | REPLC, charge level (GAP-COMPONENT); ICD; Humidity Sensor advertised rev 2 < 3 (info) |
| WAGO relay 16A + button (dump) | wago-relay-16a-expose | plug + generic_switch siblings, no TagList needed (different types) | FixedLabel extras, Lighting feature on OnOff plug, Scenes cluster present vs Groups-only (ACCEPTABLE); hash endpoint numbers |
| Inovelli VTM31 2-in-1 (dump) | inovelli-vtm31-dimmer-endpoints | all 6 endpoints, dimmer_switch client + Binding, buttons MultiPressMax 5, labels | ModeSelect clusters + mfg cluster 0x122FFC31 (GAP-COMPONENT, L); LevelControl transition-time attrs missing on EP1; dump has no TagList (REAL-DEVICE-NONCONFORMANT), we add Number tags; FixedLabel key "Button"/"DeviceType" vs our "name" (G6) |
| Silabs light switch (dump) | silabs-light-switch-endpoints | on_off_light_switch + Binding server + generic_switch MS+MSR | client ScenesManagement/ColorControl, Groups server, Identify TriggerEffect missing (GAP-COMPONENT, S); no `expose` rule for client types |
| Mock Doorbell / Chime (dump) | doorbell-endpoints, doorbell-chime-endpoints | Doorbell 0x0148 with Switch MS+MSR+MSL | Chime device type: mandatory Chime cluster not created -> conformance ERROR DT-CLUSTER-MISSING (GAP-COMPONENT/SDK, M); `expose as: doorbell` fails (G3); audio_doorbell needs camera/WebRTC clusters (GAP-SDK, none) |
| Aqara Shutter Switch H2 (dump) | aqara-shutter-switch-h2-endpoints | cover lift + 2 generic_switch MS-only, 2-tag TagList, tag on non-duplicate cover | Electrical Sensor on EP0 (G7); OTA requestor device type |
| chef multi-column switch, 9 ep (dump) | multi-column-remote-endpoints | 2 device types per endpoint (generic_switch + power_source), FM 22/30 | chef ZAP has no TagList (REAL-DEVICE-NONCONFORMANT); same battery bound 9 times (GAP-COMPONENT, S: `attach_to`/shared source) |
| Shelly i4 (doc) | shelly-i4-expose (LatchingSwitch), shelly-i4-endpoints (momentary) | 4 siblings with Number tags + labels | latching vs momentary choice is inferred from nothing (user decides); expose tags scrambled |
| Hue dimmer layout / 3-button scene switch (doc) | hue-dimmer-style-client-endpoints, -generic-switch-expose, -generic-switch-endpoints | client dimmer_switch with send_command; 4 generic switches with Switches-namespace tags | no YAML pre-binding; expose lacks tags |
| ESPHome `event:` entity | esphome-event-entity-endpoints (negative: repro-buttons-negative/) | generic switch driven by on_event + send_switch_event | `expose` of an event: error "no device type is inferred"; `as: generic_switch` -> "[event] is an invalid option for [generic_switch]" (G8) |

## Prioritized GAP list

1. **G1 (bug, S, M1/M0)**: auto Common Number tags are assigned by endpoint-number order. With hashed expose numbering the tags are scrambled vs YAML order (btn_1 gets Number 6; Left gets 2, Right 1; Aqara top/middle/bottom -> 3/1/2). DIRECTION section 9 says trailing integer of the key, else natural ordinal of keys. Affects every expose multi-button device. Fix: order by key (natural sort), not endpoint id.
2. **G2 (S-M, M1)**: no `tags:` on expose items (position/switches tags impossible). Add `tags:` (and let `label:` pass). Affects BILRESA, W100, Hue, Aqara.
3. **G3 (S, M1/M3)**: `as: doorbell` / audio_doorbell cannot bind a binary_sensor in expose (TYPE_ID_KEYS only has generic_switch). Add doorbell. Also a doorbell cannot drop default MSM (expose `features:` only adds).
4. **G8 (M, M3)**: event entity -> generic_switch binding. expose reaches the `event` key which the device type schema lacks. Needs event_id binding (event_types -> Switch events/MultiPressMax).
5. **G4 (S)**: Duplicate/distinguishable check ignores tag labels; certified IKEA scroll wheel distinguishes groups only by the label of Position:Column. Compare labels too or downgrade to warning.
6. **G-SDK tags (S if patchable)**: esp-matter hardcodes 3 semantic tags per endpoint (spec allows 6; BILRESA uses 4, scroll wheel 5). Error text says only "length of value must be at most 3": mention the esp-matter limit.
7. **G-Root (M-L, M3)**: node-level Power Source / Electrical Sensor / OTA Requestor device types on EP0 (BILRESA, Haojai, Aqara H2). `endpoints: 0` is rejected.
8. **G6 (S-M)**: FixedLabel key is hardcoded "name". HA names entities from FixedLabel label `ha_entitylabel` (only evidence: HA test fixture mock_generic_switch_multi + test_event; HA source not in refs, unverified). Make the key configurable or default to `ha_entitylabel`; inspect Inovelli-style "Button" keys.
9. **G9 (S)**: default `label:` from the entity name hard-errors when over 16 bytes ("Garage door opener left button"). Truncate with a warning (DIRECTION 9 says warn).
10. **G10 (M)**: Chime cluster (mandatory for the chime device type) not created; ModeSelect, Groups server on client switches, extra client clusters (Scenes/ColorControl), Identify TriggerEffect missing.
11. **G11 (S)**: `power_source` battery has no REPLC/RECHG/charge level; the same battery sensor must be repeated per endpoint.
12. **G5 (M)**: rotary encoder -> Switch events has no mapping; manual lambdas.
13. ICD / LIT for battery Thread devices (L, GAP-SDK).

## YAML UX pain
- `tag: on` / `tag: off` parse as booleans in YAML; unquoted gives "tag: True" and a confusing error. Needs `"on"` quoting, or accept `true/false` with a hint.
- Tag limit error is generic. Software version required with `ota: true` (fine, clear).
- Tool integration: merging t08-expose onto the base needs `CONF_FEATURES` -> `CONF_WITH_FEATURES` (inference.py import); layout export lacked tags/parent/label before tool-devicediff, and `known_gaps.json` still claims TagList is never emitted (stale); COMPAT-OTA warning ignores `ota: true`.
- Verbosity: 9-endpoint devices need 100+ YAML lines without a repeat/template; `expose` glob cuts this to 2 lines but loses tags (G1/G2).
- Conflict: merging sdk-gaps vs base conflicted in endpoints.py and tests/unit/test_conformance.py (resolved by keeping base and renaming the tool test to test_conformance_tool.py in my working tree; that merge is in the branch history).
