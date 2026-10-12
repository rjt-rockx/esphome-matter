# Design note: delegates for Energy EVSE, Device Energy Management and Service Area

Status: proposal. Not implemented. The mode clusters, Mode Select, Operational State and RVC Operational State are implemented, see [../delegates.md](../delegates.md).

## Why these three are different

The delegate layer for modes and operational state can be generic because the SDK owns the state machine: the delegate only answers list questions (labels, tags, state ids) and forwards a few commands to the user. Energy EVSE, Device Energy Management (DEM) and Service Area have no such split. The SDK expects the *application* to implement the behaviour, and the specification attaches safety and timing requirements to it:

| Cluster | Delegate methods | What the delegate has to know |
|---|---|---|
| `EnergyEvse::Delegate` | 8 commands (`Disable`, `EnableCharging`, `EnableDischarging`, `StartDiagnostics`, `SetTargets`, `GetTargets`, `LoadTargets`, `ClearTargets`) and 24 `On*Changed` notifications (state, supply state, fault, charge currents, session id/duration/energy, SoC, vehicle id, ...) | EVSE state machine (not plugged, plugged, charging, ...), charging schedule targets, session accounting, circuit capacity |
| `DeviceEnergyManagement::Delegate` | 9 commands (power adjust, start time adjust, pause/resume, forecast modify, constraint based adjust, cancel) and ESA getters (type, can generate, state, min/max power, opt out, power adjust capability, **forecast**), `SetESAState` | A power forecast struct (slots, costs), what the device may be adjusted to, restore after adjustment |
| `ServiceArea::Delegate` | 7 (select areas allowed, validate area set, skip area, supported areas/maps change allowed) plus a `StorageDelegate` for areas, maps and progress | The map of the home as the device sees it |

The reference application in connectedhomeip (`examples/energy-management`) is about 4200 lines for the EVSE, DEM and water heater delegates and the manager that ties them. A generic YAML layer that hides that would have to invent the state machine; a thin layer that only forwards calls would hand the user the safety logic.

## What a controller actually needs

Practical consumers (Home Assistant, Apple Home, Google Home) only use a small part of these clusters today:

* **EVSE**: `State`/`SupplyState`/`FaultState` (to display plug and charging status), `EnableCharging`/`Disable` (start and stop), `ChargingEnabledUntil`, `MaximumChargeCurrent`/`UserMaximumChargeCurrent` (a current limit), session counters. `SetTargets` and the schedule are used by the Apple Home / smart energy features and are the part that requires a real planner.
* **DEM**: HA does not control it. Apple Home reads `ESAState` and the forecast; controllers that implement Matter energy management (Apple "Grid Forecast" style features) use the forecast and `PowerAdjustRequest`. Without those commands the device is a passive ESA.
* **Service Area**: HA exposes `SelectAreas` for robot vacuums (room cleaning). Everything but `SupportedAreas`, `SelectedAreas`, `CurrentArea` and `Progress` is optional.

## Proposed YAML

Same principle as the delegate layer in `delegates.py`: the user supplies *sources* and *actions*, the component implements the cluster rules.

### Service Area (first, size M)

Static `areas` from YAML, `select` for the selected area(s), `current_area` source, optional progress. It reuses the select/text_sensor binding and the `run_on_main_loop_and_wait` bridge.

```yaml
clusters:
  service_area:
    areas:
      - {id: 1, name: Kitchen}
      - {id: 2, name: Living room}
    select: vacuum_room               # SelectAreas <-> select
    current_area: vacuum_current_room # text_sensor
    on_select_areas:                  # may matter.service_area.reject
      - ...
```

`IsSetSelectedAreasAllowed` is the "not while cleaning" rule: answered from the RVC operational state that is already known (reject while `running` unless the `SelectWhileRunning` feature is enabled). `SupportedAreas` is constant, so `IsSupportedAreasChangeAllowed` returns false and the storage delegate can be a static array. Maps (`Maps` feature) are not supported. esp-matter has the `service_area` cluster creator (SupportedAreas, SelectedAreas, SelectAreas) and `ServiceAreaDelegateInitCB`, and the RVC device type lists the cluster as optional, so only the delegate is missing.

### Energy EVSE (size L, in stages)

Stage 1 ("EVSE-lite", no schedule, no V2X) maps the reported state to sources and the control to actions. The component would own the state machine transitions that the SDK reference implements, driven by the sources:

```yaml
clusters:
  energy_evse:
    state: evse_state                 # text_sensor: not_plugged_in | plugged_in_no_demand | plugged_in_demand | plugged_in_charging | ...
    supply_state: evse_supply         # derived (disabled | charging_enabled) if not given
    fault: evse_fault                 # text_sensor, optional
    circuit_capacity: 32              # A, constant
    maximum_charge_current: evse_limit   # number (A), sets MaximumChargeCurrent, bidirectional with UserMaximumChargeCurrent
    session_energy: evse_session_wh   # sensor
    session_duration: evse_session_s  # sensor
    enable_charging:                  # command: EnableCharging(chargingEnabledUntil, minimum, maximum current)
      - switch.turn_on: evse_relay
    disable:
      - switch.turn_off: evse_relay
```

`EnableCharging` must be answered with the *actual* result, like the mode and operational state commands: the same synchronous bridge (`matter.evse.reject: {status: failure}`) is reused. The expiry (`chargingEnabledUntil`) is a timer the component keeps and uses to call `disable`.

Required companions (device type `evse`, not optional): Energy EVSE Mode (already supported by the mode layer, tags `manual` / `time_of_use` / `solar_charging`), Device Energy Management (at least as a passive ESA), Power Source/Electrical sensor for the meter (existing). The EVSE device type also requires `DeviceEnergyManagement` + its mode, so Stage 1 needs the DEM stage below.

Stage 2 adds `SetTargets`/`GetTargets`/`ClearTargets` with a small persisted target list, `NextChargeStartTime` etc. (needs time synchronisation), Stage 3 V2X.

Safety requirements to put in the config validation and docs before shipping: charging defaults to *disabled* after boot and on any component failure, the relay action is idempotent, a maximum current is always enforced by the component (the value from the controller is clamped to `circuit_capacity` and to the user limit), and the docs state clearly that this is not a certified EVSE.

### Device Energy Management (size M for the passive ESA, L for forecasts)

Passive ESA: `ESAType`, `ESACanGenerate`, `ESAState`, `AbsMinPower`/`AbsMaxPower`, `OptOutState` from YAML constants and a source, no features beyond `PowerForecastReporting`; every command returns `Failure` (or is not created: the spec makes the commands depend on features, so enabling no adjustment feature removes them). That is enough for conformance of a mandatory DEM (EVSE, heat pump, water heater) and for controllers that only read the state.

```yaml
clusters:
  device_energy_management:
    esa_type: evse
    can_generate: false
    abs_min_power: 0
    abs_max_power: 7400
    state: evse_esa_state    # optional text_sensor: online | offline | fault | power_adjust_active | paused
```

Forecasts (`PowerForecastReporting`, `StateForecastReporting`) need the slot list; a useful minimum is a *flat* forecast derived from the EVSE session (start, estimated end from remaining energy and current limit) published through the same delegate when the session changes. `PowerAdjustment`/`Pausable`/`StartTimeAdjustment` need the user's device to actually move load and should be an action pair (`on_power_adjust`, `on_pause`) like the operational state commands, with the same reject bridge. This is where the work is and should only be started with a concrete device (heat pump, wallbox) to test against.

## Order of work

1. Service Area (M). Closes the RVC gap with run mode and operational state.
2. DEM passive ESA (M). Unblocks conformance for EVSE, heat pump, water heater, which currently list DEM as structure only.
3. EVSE-lite with a hardware test (L). The checker (`tools/conformance`) now knows the `evse` device type of esp-matter (`energy_evse` namespace) after this change, so the conformance result of each stage is visible.
4. Forecast / schedule / V2X.

## Open questions

* Whether to expose the SDK state machine of the EVSE (`EnergyEvseDelegate` of the reference app is reusable as a library: it is in connectedhomeip `examples`, not in the SDK component, so it would be copied) or write the smaller state machine of Stage 1 from the spec. Copying means maintaining ~2000 lines of Apache-licensed code that targets a different build; writing it means re-deriving the transitions of the EVSE state diagram.
* The `ElectricalPowerMeasurement` delegate in `matter_sensors.cpp` is the only cluster delegate of this kind in the component today. Its `Instance` handling (managed instance retrieval via `get_delegate_managed_instance`) is the model that the DEM and EVSE delegates should follow, as the operational state mapping already does.
