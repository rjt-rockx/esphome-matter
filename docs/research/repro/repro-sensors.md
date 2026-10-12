# Reproduction: sensors (branch repro-sensors)

Examples: `examples/devices/sensors/*.yaml` (18 validate with `VALIDATE_DIRS=examples/devices/sensors tests/validate.sh`),
`_gaps/` = does not validate today. Diffs: `tools.conformance --emit-dump` vs the HA fixture (scratch devdiff), tags/parents/labels read from generated main.cpp.
Base: tender-rubin + sdk-gaps + t08-expose (merge fix: expose/inference.py imports CONF_WITH_FEATURES).
Common to every device (not repeated): root lacks OTA Requestor device type, ICD Management, Software/ThreadNetwork diagnostics variants; device-type revisions are newer than real (ACCEPTABLE); Identify TriggerEffect missing (ACCEPTABLE); Power Source FeatureMap BAT (2) vs real BAT+REPLC (10), missing BatReplacementDescription/BatQuantity/BatVoltage (GAP-COMPONENT G5).

## Per-device
| Example | Mirrors (evidence) | Result | Differences |
|---|---|---|---|
| eve-weather-expose / -endpoints / -battery-on-ep | eve-weather-sensor (dump) | 2 temp/hum endpoints match | Power Source on EP0 not possible (own endpoint 3, or cluster on EP1): G5. expose needs `as: power_source` for battery (G1). expose adds FixedLabel to every endpoint (EXTRA, G2). Eve mfg cluster: NONREPRODUCIBLE. |
| eve-contact-endpoints / -expose | eve-contact-sensor (dump) | endpoint-level `battery:` = exact shape (contact + PowerSource on EP1) | no REPLC, no PowerSourceConfiguration on root. expose: `as: contact_sensor` + `{as: power_source, attach_to}` gives 2 device types (extra 0x0011) |
| aqara-door-window-p2 | aqara-door-window-p2 (dump) | contact + power_source EP2 matches | BooleanStateConfiguration SENSLVL missing (G3), ICD (G6) |
| aqara-motion-p2 | aqara-motion-p2 (dump) | occupancy PIR, light, power OK | BSC missing, Occupancy HoldTime/HoldTimeLimits/PIR delay attrs missing (G7) |
| aqara-fp300-endpoints | aqara-presence-fp300 (dump) | radar occupancy (`device_class: presence`), light, temp, hum, power OK | same as above; FP300 single-zone only |
| aqara-w100 | aqara-sensor-w100 (dump) | temp, hum, 3 generic_switch with auto Common Number tags, power OK | real has a 2nd tag per switch (namespace 8 position); ours only ns 7 (ACCEPTABLE, can add `tags:`); Descriptor TagList FM/attr 0x4 absent in the checker's emitted dump although main.cpp registers tags (tool gap G8) |
| heiman-motion-m1 | heiman-motion-sensor-m1 (dump) | two device types on EP1 (occupancy + power_source) supported | BSC, HoldTime, FixedLabel/UserLabel, LIT ICD (G6) |
| heiman-smoke-detector | heiman-smoke-detector (dump) | smoke_co_alarm SMOKE + PowerSource on same EP | SmokeCOAlarm DeviceMuted attr and SelfTestRequest cmd absent, ICD CIP |
| heiman-co-sensor | heiman-co-sensor (dump) | CO alarm + battery | CO concentration cluster (MEA) cannot bind a ppm sensor (G7), second device type 0x0011 absent, root FixedLabel/UserLabel |
| ikea-klippbok-leak | ikea-klippbok-water-leak (dump) | leak detector OK | BSC VIS/AUD/SPRS (G3), Power Source on EP0 (G5) |
| apollo-air1-split / -single | apollo-air-1 (inferred) | AQ endpoint with CO2, PM1/2.5/10; split vs folded T/H both expressible; extra pressure ep | VOC/NOx index have no unit (not mapped); AirQuality enum stays Unknown (G9) |
| everything-presence-pro | everything-presence-pro (doc) | 2 occupancy (RAD, PIR) get tags 1,2; temp/hum/lux/CO2/plug OK | plug adds Groups/Scenes clusters (fine); tamper/targets not mappable |
| fp2-multizone | none (FP2-style) | 3 occupancy eps with explicit common_position tags + labels works | label adds FixedLabel; zones are per-endpoint only |
| chef-sensor-hub | sdk-chef-sensor-hub-9ep (dump) | 13 endpoints, every sensor type incl. flow, pressure, soil, leak/freeze/rain | Binding client clusters not created; occupancy gets feature OTHER without device_class |
| _gaps/ikea-alpstuga-endpoints | ikea-air-quality-monitor (dump) | FAILS | G4: AQ with `with_features: [level_indication]`; OnOff DeadFront cluster and TimeSync not expressible |
| _gaps/apollo-air1-expose | apollo-air-1 | FAILS | G1 |

## Prioritized GAP list
1. **G3 (bug) `clusters:` KeyError** and **BooleanStateConfiguration unreachable** (device-type `with_features: [visual..]` errors with empty "required by:"). Affects every Aqara/IKEA/Heiman real dump. Size S (clusters fix reported fixed on another branch); BSC via `with_features` S-M. M0/M3.
2. **G1 expose inference gaps**: binary_sensor (door/motion/moisture/cold), illuminance, pressure, battery -> Power Source, air-quality merge group and `attach_to`, smoke (explicit `as:`). Proposal: DIRECTION section 6 table (T09). Size M. M1.
3. **G2 expose FixedLabel on every endpoint**: real devices have none on single-instance endpoints; DIRECTION section 9 says duplicates only. Proposal: `fixed_labels: duplicates` default. Size S. M1.
4. **G4 air_quality_sensor `with_features: [level_indication]`** fails with empty "required by:" (concentration clusters). Size S-M.
5. **G5 Power Source**: node-level Power Source on EP0 (`matter: power_source:` per section 13) with EndpointList; REPLC feature plus mandatory description/quantity; BatChargeLevel. Size M. M3.
6. **G6 ICD**: ICD Management (CIP/LIT) on root for sleepy Thread sensors; nothing sets CONFIG_ENABLE_ICD_SERVER; MTD `poll_period` blocks OTA. Size L, outside current milestones (KNOWLEDGE b10).
7. **G7 optional attributes with no binding**: Occupancy HoldTime/HoldTimeLimits/PIR delay; SmokeCOAlarm DeviceMuted/SelfTest; CO concentration on smoke_co_alarm; Identify TriggerEffect. Size S-M each.
8. **G8 tool**: layout export (`layout_dump.py`) lacks tags/parents/labels/battery detail so the conformance checker still reports the stale "TagList known gap" and cannot diff TagList. Size S.
9. **G9** VOC/NOx index (unitless) and AirQuality enum from sensors: need an `options: {air_quality: <id>}` binding. Size M.

## YAML UX pain
- Errors with an empty list ("required by:.") for optional-cluster features.
- `clusters:` and `with_features` for optional clusters give opaque failures; battery has two spellings (endpoint `battery:` vs `power_source:`) with different placement semantics.
- Power Source placement differs per vendor (EP0 / own endpoint / on application endpoint / second device type); user must choose with no guidance.
- Two BooleanState-based device types need explicit device_class for polarity; bare binary_sensor never inferred (intended, D11) but expose then needs `as:` for everything.
- Sleepy-device settings live in `openthread:` (MTD, poll_period) with no Matter-side ICD story.
