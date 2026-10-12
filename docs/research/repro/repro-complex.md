# repro-complex: bridges, energy, appliances (branch repro-complex)

Examples: `examples/devices/complex/*.yaml` (23 files, each with `# mirrors: <catalog-id>`). All 23 pass `esphome config` +
`compile --only-generate` with the B1 fix below; 20 pass on the unpatched base. Tooling: `python -m tools.conformance <yaml> -v --emit-dump`
plus my own `devdiff.py` (predicted dump vs HA flat fixture). Base = tender-rubin + sdk-gaps + t08-expose + spike-bridge (merged; conflicts
resolved minimally: bridge parents now go through `register_endpoint_parent` of the conformance registry, spike's own parent list dropped;
`bridged_node` registration is emitted after the conformance parent so the aggregator wins; t08 `expose/inference.py` import of the removed
`CONF_FEATURES` made tolerant). `tool-devicediff` was NOT merged (per coordinator).

Notation: EQ equivalent, ACC acceptable, GAP-C component gap, GAP-S esp-matter SDK limit, RD real device nonconformant.
Static prediction caveats: PartsList is empty for non-root endpoints in `--emit-dump` (layout artifact: runtime derives it from parents);
Thermostat Heating/Cooling follow climate traits at runtime (checker emits CL-CHOICE info); baseline "TagList missing" KNOWN entries are
stale (generated main.cpp does emit `register_endpoint_tag`, verified on ir-hub-bridge).

## Per-device table

| Example | Mirrors (evidence) | Result | Key differences |
|---|---|---|---|
| hub-bridge-lights (+ -expose) | Hue/Dirigera/SwitchBot hub, sdk bridge-app (doc/dump) | checker clean. Aggregator EP1 + 3x (Bridged Node + light) EQ to esp-matter bridge example | static only (ACC); BDBI only NodeLabel/UniqueID/Reachable (GAP-C-BDBI); `expose:` cannot bridge (native flat alternative given); rgb light advertises ColorTemperature (XY+CT FM=24), should be XY(+HS) (GAP-C-RGB-CT, lights) |
| fritz-gateway-bridge | fritz_bridge (dump) | clean | real device has NO aggregator and root-level Bridged Node (RD-ish / unusual); spike requires an aggregator parent -> GAP-C-BRIDGE-NOAGG; mirrored with Aggregator added |
| knx-bridge-metered | atios_knx_bridge (dump) | clean. EP1 Aggregator, EP29 Bridged Node + Electrical Sensor stacked = EQ | BDBI attrs missing (VendorName, ProductName, SerialNumber, SW version: 0x1,0x3,0x5,0x8,0xa,0xe,0xf) GAP-C-BDBI; EPM extra attrs 0x4/0x5 and EEM extra 0x2 (GAP-C-EEM) |
| ble-proxy-bridge (nested), -airthings (single endpoint), -native-expose | LYWSD03MMC / Wave Mini (inferred shape) | clean. Nested form (Bridged Node endpoint, sensor endpoints with `parent:`) and single-endpoint form both work | `battery:` on the Bridged Node gives Power Source there EQ; Reachable binding via template binary_sensor; BLE (Bluedroid) + Matter on Wi-Fi unverified (docs/ble-commissioning.md); AirQuality enum Unknown (GAP-C-AQ) |
| espnow-bridge | sdk esp-now_bridge_light (doc) | clean. 2 temp nodes + 1 relay plug as Bridged Nodes, bidirectional via template switch | static MACs; two sibling temperature bridged nodes: auto TagList emitted (ok) |
| ir-room-ac (+ -expose), ir-hub-bridge | ir-room-ac-climate (esphome), mock_room_airconditioner (dump) | clean. RAC EP1 (OnOff+Thermostat, derives Cooling/Heating from climate_ir traits) + child Temperature Sensor via `parent:` | FanControl absent (climate fan modes not bound; real mock has FM=11 Fan Control) GAP-C-RAC-FAN; `expose` can't nest the sensor (GAP-C-EXPOSE-PARENT); IR hub = Aggregator + 3 bridged nodes works |
| evse-wallbox | silabs_evse_charging (dump) | ERROR-free structure, but checker doesn't know device type `evse` (profile name `energy_evse`; TOOL gap) so EnergyEVSE/EVSE Mode/0x050C not in predicted dump; generated code does call `energy_evse::add`. Stacked DTs EQ: PS+DEM+ES. EPM FM=2 vs 31, EEM 5 vs 15 (ACC) | ALL EVSE logic needs `EnergyEvse::Delegate`: not implemented (GAP-C-EVSE, L); DEM Mode via `clusters:` => CL-CMD-MISSING ChangeToMode (SDK: mode commands only with delegate); Identify missing in prediction |
| solar-battery-hybrid / -tree | mock_solar_battery_storage (mock) | flat form clean (warning EEM Cumulative* attr present without feature); tree form: ERROR CL-CHOICE NodeTopology + TreeTopology | GAP-C-PT-TREE: `electrical_sensor` always adds NodeTopology, `tree_topology/set_topology` can't replace it; PowerSource battery detail attrs (BatVoltage, charge state/level, capacity, 0x5.. etc) not bindable; DEM omitted |
| heat-pump | sdk-chef-heat-pump-energy (zap dump) | ERRORs: DEM Mode ChangeToMode(+Response) missing (device type DEM auto-creates mode cluster, no delegate) | Heat Pump DT 0x0309 stacked with PS/ES/DEM creatable; Thermostat is a client in the sample (GAP-C-CLIENT-CLUSTERS), extra Thermostat endpoint used instead |
| water-heater (+ -thermostat) | silabs_water_heater (dump) | water-heater: 4 ERRORs (WaterHeaterMode, DEM Mode commands missing). -thermostat: clean | no `climate_id` for water_heater: Thermostat inside Water Heater is unbound (GAP-C-WH-CLIMATE); WaterHeaterManagement/tank percent = delegate; thermostat variant is the working path |
| metered-dual-outlet (+ -stacked) | eve_energy_20ecn4101, eve_energy_plug_patched (dump) | clean, EQ to the patched Eve shape (plug EP + sibling Electrical Sensor EP, EPM ALTC, EEM IMPE+CUME) | PT FM=1 NODE present; real dump has no PT FM (RD: patched fixture); Groups FM (GN) and Eve mfg cluster absent (ACC); EEM exports attr extra 0x2 |
| refrigerator | silabs_refrigerator (dump) | ERROR CL-EVENT-MISSING RefrigeratorAlarm Notify | **Refrigerator And Temperature Controlled Cabinet Mode (0x0052, mandatory) is not created at all** (GAP-S: cluster missing in esp-matter 1.6 / component clusters.json); OnOff and Identify absent on the fridge; TemperatureControl attr 0x3 missing; cabinet TemperatureMeasurement only by stacking `temperature_sensor` DT (extra DT 0x302) |
| laundry-washer | silabs_laundrywasher (dump) | 4 ERRORs: LaundryWasherMode SupportedModes/CurrentMode/ChangeToMode(+Resp) not created | OperationalState commands missing (delegate); meter EP2 EQ; PT SET|DYNAMIC (FM 12) not reachable (GAP-C-PT-TREE); dishwasher same shape (not separately written) |
| robot-vacuum (+ -switch) | eufy_vacuum_omni_e28 / roborock (dump) | 5 ERRORs: RVC Run/Clean Mode ChangeToMode(+Resp), RVC OperationalState OperationCompletion event | Service Area cluster not creatable (not in the RVC optional list that bound; FM 6 in dumps) GAP-C-SERVICEAREA; PS(BAT|REC)+RVC stacking EQ; all control needs delegates; -switch fallback (start/dock plugs) works and passes |

## Feasibility with esp-matter 1.6 (what is really possible)

1. **Structure is cheap, behaviour needs C++ delegates.** esp-matter 1.6 ships device-type creators for aggregator, bridged_node,
   energy_evse, device_energy_management, electrical_sensor, solar_power, battery_storage, heat_pump, water_heater, refrigerator,
   temperature_controlled_cabinet, laundry_washer/dryer, dish_washer, robotic_vacuum_cleaner, oven/cooktop... The component can emit
   them all (`device_types.json`), including stacked device types per endpoint (EVSE+DEM+ES+PS verified). But EnergyEvse,
   DeviceEnergyManagement, PowerTopology, ElectricalPowerMeasurement, mode clusters (`*Mode`), OperationalState, RVC OperationalState,
   LaundryWasherControls, DishwasherAlarm, ServiceArea, TemperatureControl are delegate-driven
   (`data_model/esp_matter_delegate_callbacks.cpp`: `*DelegateInitCB` only builds the `Instance` when a delegate is set). Only EPM (own
   delegate in matter_sensors.cpp) and window covering are wired. Without a delegate the mode clusters lack ChangeToMode (checker: CL-CMD-MISSING),
   so **every mandatory-mode device type (EVSE, DEM, water heater, washer, dishwasher, RVC, fridge) is non-conformant and inert** today.
2. **Bridges**: static aggregator + bridged nodes of the node's own entities work with spike-bridge (all 8 bridge examples). Dynamic
   bridging (Hue/Dirigera/Aqara hubs, runtime-learned devices) is NOT feasible with the compile-time endpoint tree; esp-matter itself supports
   runtime `endpoint::create` (bridge_apps), so a separate runtime-bridge component (RFC, out of scope in DIRECTION §12) would be needed.
3. **ESPHome as BLE-proxy bridge**: feasible as static bridged sensors fed by `xiaomi_*`/`airthings` platforms; the open risk is BLE
   (Bluedroid) coexisting with Matter on Wi-Fi (not build-tested; BLE-commissioned builds reject ESPHome BLE).
4. **ESP-NOW**: feasible same way (receive callbacks -> template entities -> bridged nodes); radio channel must follow Wi-Fi; no Thread.
5. **IR**: climate_ir -> room_air_conditioner works (ir-room-ac); other IR targets as on/off plugs; no TV/media device type.
6. **Energy**: metering (EPM/EEM) and power-source battery are real; EVSE/DEM/heat pump/water heater/solar/battery are structure only.
   Practical working alternatives: plain Thermostat for water heater/heat pump, sensors for solar/battery.
7. **Appliances**: no controller UX relies on them being absent; the fallback "start/dock as plug" gives control on all controllers.

## Minimal path (smallest useful steps)

1. B1 fix (clusters key), S.
2. Generic delegate runtime for **Mode clusters** and **OperationalState** bound to ESPHome `select`/`text_sensor` + actions (`mode_select`-like), M-L; unlocks washer/dishwasher/RVC/water heater/DEM Mode conformance.
3. Delegate for **EnergyEvse** (state/supply state/fault/charging current limit bound to ESPHome sensors/number/switch), L; plus DEM minimal "ESA state only" (no forecast) delegate, M.
4. `electrical_sensor` topology choice (`topology: node|tree|set`, `parent` children) and PS battery attributes, S-M.
5. Bridge: BDBI identity options (`vendor_name`, `product_name`, `serial`, `software_version`), `bridged_node` without aggregator, `layout: bridged`, S-M.

## Prioritized GAP list

| # | Gap | Devices | Proposed YAML / feature | Size | Milestone |
|---|---|---|---|---|---|
| 1 | **B1: `clusters:` KeyError** (`CLUSTERS_BY_NAME[conf_key]` in endpoints.py:276/392; fix `CLUSTERS_BY_CONF_KEY` + `_cluster_configs[cluster.name]`; patch `/home/user/research/devices/repro-complex-clusters-key.patch`) | refrigerator, laundry, RVC, any optional cluster | none | S | M0 |
| 2 | Delegate-less mode / operational-state clusters are created non-conformant (no ChangeToMode, no OperationalState commands) | EVSE, DEM, water heater, heat pump, washer, RVC, fridge | `matter_modes.cpp`: modes list from YAML (`clusters: {rvc_run_mode: {modes: [...], select: id}}`) + OperationalState bound to a `select`/`text_sensor` | M-L | M3+ |
| 3 | EnergyEvse / DEM have no delegate | EVSE, heat pump, water heater, solar/battery DEM | `evse: {state: text_sensor, charging_switch: switch, max_current: number}` | L | M3+ |
| 4 | Refrigerator mode cluster 0x0052 missing; Refrigerator Alarm Notify event missing; TemperatureControl attr | fridge | SDK/cluster table add | S-M, GAP-S | M3 |
| 5 | Service Area cluster not creatable; RVC OperationCompletion event | RVC | cluster table / delegate | M | later |
| 6 | Power Topology choice: always NODE; no TREE/SET/ dynamic | solar/battery tree, washer, dishwasher | `electrical_sensor: {topology: tree|set}` + `with_features` choice honored | S | M3 |
| 7 | BDBI identity attributes beyond NodeLabel/UniqueID/Reachable | all bridges | `bridged_node: {vendor_name, product_name, serial_number, software_version, ...}` | S | M5a |
| 8 | Bridged node without aggregator parent (Fritz shape) and nested bridge needs manual `parent:`; `expose`/`layout: bridged` absent | fritz, KNX, BLE | `layout: bridged` sugar, optional `aggregator: false` | M | M5b |
| 9 | `expose`: no inference for climate, battery sensors (`sensor`/`device_class: battery` from platform default `battery_level`), no `parent:`/nesting, `meters:` reserved | thermostat, BLE, RAC | per DIRECTION M3 | M | M1-M3 |
| 10 | RAC Fan Control not bound to climate fan modes; swing | ir-room-ac, mock RAC | `room_air_conditioner` auto Fan Control | M | M3 |
| 11 | `water_heater`/`heat_pump` no `climate_id` | water heater | allow `climate_id` on Thermostat-containing DTs | S | M3 |
| 12 | EEM creates both Imported and Exported cumulative attrs regardless of feature (CL-ATTR-NA warnings in 9 examples) | all metered | only create attrs matching enabled features | S | M0 |
| 13 | Client clusters on endpoints (Thermostat client of heat pump, Binding sources) | heat pump | `clusters` with `client:` | M | later |
| 14 | RGB light advertises ColorTemperature (XY+CT) with no white channel | hub-bridge-lights | derive CT only when white channel declared (DIRECTION §6) | S | M1 |
| 15 | PowerSource battery detail attrs (BatVoltage, charge state/level, time remaining, capacity) not bindable | RVC, battery storage | `battery: {percent, voltage, charging}` | S-M | M3 |
| 16 | Expose label defaults to entity name; names > 16 bytes are a hard error ("Labels must be 1 to 16 bytes") | any with long names | truncate + warn (DIRECTION §9) or default no label | S | M1 |

Tooling gaps: checker profile lacks `evse` (name `energy_evse`), static PartsList always empty, TagList KNOWN baseline entries stale,
Thermostat features unknowable statically.

## YAML UX pain

* The same hub needs 3-6 lines of boilerplate per endpoint (`bridged_node`, `parent: 1`); `parent` repeated; no YAML way to say "everything below this aggregator".
* `energy_imported` binding silently creates export attribute; `with_features: [exported_energy]` conflicts ("does not contain the ElectricalEnergyMeasurement cluster").
* ESPHome-side friction (not ours): `climate: thermostat` demands `min_idle_time`, `min_heating_off_time`, `min_heating_run_time`.
* Error text for the B1 crash is a raw KeyError traceback with no hint.
* `expose` rejection messages are good ("use `as:`"), but battery and climate need `as:` for things D11 says are inferred.
* Power-user device types give no hint which delegates are missing; a build succeeds and the device is silently inert. A compile-time warning "X is structure-only (no delegate)" would prevent surprise.
