# HVAC: room air conditioner, air purifier, extractor hood, filters, water heater, heat pump

This page covers what is added on top of [fan.md](fan.md) and [climate.md](climate.md): the Fan Control cluster on other device types, filter monitoring, and `climate_id` on the water heater and heat pump.

## Fan Control on any endpoint

`fan_id` binds the Fan Control cluster to an ESPHome fan. It is accepted

* in the config of every device type that can contain the Fan Control server cluster: `fan`, `air_purifier`, `extractor_hood`, `room_air_conditioner`, `microwave_oven`;
* in `clusters: fan_control:` on **any** endpoint (for example next to a `thermostat`).

The features (MultiSpeed, Rocking, AirflowDirection), the speed conversion, Wind and Step behave as described in [fan.md](fan.md).

```yaml
matter:
  endpoints:
    1:
      air_purifier:
        fan_id: purifier_fan          # Fan Control is mandatory here
    2:
      room_air_conditioner:
        climate_id: ac
        fan_id: indoor_fan            # optional Fan Control cluster, created for you
    3:
      thermostat:
        climate_id: hall
      clusters:
        fan_control:
          fan_id: hall_fan            # see the note below
```

On a device type where Fan Control is optional (room air conditioner, microwave oven) a device level `fan_id` adds the cluster, exactly as `clusters: fan_control: {}` would. Only one binding is possible per endpoint.

> The `thermostat` device type only lists Fan Control as a **client** cluster. A Fan Control server on a thermostat endpoint is accepted (several real products do this and the conformance checker does not flag it) but it is not part of the device type. For a strictly conforming device use a `room_air_conditioner`.

`fan_id` is **not** accepted at device level on `thermostat` (use `clusters: fan_control:`). A `climate_id` at device level only ever binds the Thermostat cluster.

### Fan modes of a climate

A room air conditioner often has no separate fan entity: the fan speed is a *fan mode* of the climate. `clusters: fan_control: {climate_id: ...}` binds the cluster to the fan and swing modes of the climate:

```yaml
matter:
  endpoints:
    1:
      room_air_conditioner:
        climate_id: ac
      clusters:
        fan_control:
          climate_id: ac               # usually the same climate
          # with_features: [step]      # optional, see below

climate:
  - platform: thermostat   # or climate_ir, ...
    id: ac
    # ... fan_mode_*_action / swing_*_action make the modes available
```

| Climate fan mode | Matter FanMode |
|---|---|
| `OFF` | Off |
| `LOW`, `QUIET` | Low |
| `MEDIUM`, `MIDDLE` | Medium |
| `HIGH`, `ON` | High |
| `AUTO` | Auto |
| `FOCUS`, `DIFFUSE`, custom fan modes | not exposed |

* `FanModeSequence` is built from the fan modes the climate supports and only offers modes it can do. The supported modes are ranked low (`LOW`, `QUIET`), medium (`MEDIUM`, `MIDDLE`), high (`HIGH`, `ON`): three ranks give Off/Low/Medium/High, two ranks Off/Low/High (the Matter High is then the higher of the two), one rank Off/High. Each gets Auto when the climate supports `AUTO` (the Auto feature is added). A climate fan mode that is not one of those (`QUIET` next to `LOW`) is reported as the nearest supported one.
* **Off.** The Matter sequences always contain Off, so Off must do something. With a `FAN_OFF` fan mode that is used. Without one, FanMode Off (or PercentSetting 0, or stepping down to Off) turns the **climate off** (`mode: OFF`), and a climate in mode `OFF` reports FanMode Off. Writing a fan mode while the climate is off therefore reports Off again. Give the climate a `fan_mode_off_action` if you want the fan alone to be switchable.
* `PercentSetting` / `PercentCurrent` use the cluster's own mapping (Low 33, Medium 66, High 100). There is no MultiSpeed feature: climate fan modes have no step count.
* Swing modes map to Rocking: `VERTICAL` = RockUpDown, `HORIZONTAL` = RockLeftRight, `BOTH` = both bits. `RockSupport` has the bits the climate can do. A climate that only supports `BOTH` takes any rock request as `BOTH`.
* A swing the climate can't do is logged and the settled RockSetting is reported back, like unsupported SystemModes. Climate state updates that change neither fan mode nor swing (temperature, ...) are not reported.
* `step` moves through Off, Low, Medium, High (Auto is not a level). Wrap / LowestOff work as for fans.
* The features `auto`, `multi_speed`, `rocking`, `airflow_direction` and `wind` are derived or unavailable and are rejected in `with_features` together with `climate_id`. `wind_presets` is rejected too.
* A climate with none of low, medium or high fan modes keeps only Off (and Auto): an error is logged at boot, Step is skipped, Matter keeps running.
* The ESPHome climate owns the state: it is pushed to Matter at boot instead of replaying the persisted FanMode.

The climate swing / fan mapping is verified by compiling, not on hardware.

## Filter monitoring (HEPA, Activated Carbon)

The HEPA Filter Monitoring (0x0071) and Activated Carbon Filter Monitoring (0x0072) clusters are optional on the `air_purifier` and `extractor_hood` device types. Create them in `clusters:` and bind them to ESPHome entities:

```yaml
matter:
  endpoints:
    1:
      air_purifier:
        fan_id: purifier_fan
      clusters:
        hepa_filter_monitoring:
          condition: hepa_life           # sensor, remaining life in %
          warning_level: 20%             # ChangeIndication Warning at or below
          critical_level: 5%             # ChangeIndication Critical at or below (default 10%)
          replace_needed: hepa_replace   # binary sensor, on = Critical
          reset_button: hepa_reset       # pressed by the ResetCondition command
          on_reset:                      # and / or run automations
            - sensor.template.publish:
                id: hepa_life
                state: 100
        activated_carbon_filter_monitoring:
          replace_needed: carbon_replace
```

| Option | Matter |
|---|---|
| `condition` (sensor, 0 to 100) | Condition attribute (feature Condition) |
| `degradation_direction` (`down`, default, or `up`) | DegradationDirection. `down`: 100 is a new filter, wearing down to 0. `up`: 0 is new, rising to 100 |
| `warning_level` | Warning feature; ChangeIndication Warning at or beyond the level |
| `critical_level` | ChangeIndication Critical at or beyond the level. Default 10% (90% for `up`) when `condition` is set |
| `replace_needed` (binary sensor) | ChangeIndication Critical while on. Without `condition` this is the only source |
| `reset_button`, `on_reset` | ResetCondition command: presses the button and/or runs the automations. Without one of them the command is not created |

Rules checked at config time: at least one of `condition` and `replace_needed`; `warning_level`, `critical_level` and `degradation_direction` need `condition`; the warning level must be on the "not yet critical" side of the critical level. `with_features` can still name `replacement_product_list`.

When a controller sends ResetCondition the Matter stack first resets Condition (to 100, or 0 for `up`) and ChangeIndication, then the button is pressed and the automations run. The bound sensors are **not** read again at that moment: make `reset_button` / `on_reset` reset the underlying sensor (as in the example), which then reports the new value. If the sensor keeps its old value it is reported again with its next change.

Not supported: InPlaceIndicator, LastChangedTime, a ReplacementProductList with content.

## Water heater and heat pump

`climate_id` is accepted on both. The Thermostat cluster is bound as described in [climate.md](climate.md).

```yaml
matter:
  endpoints:
    1:
      water_heater:
        climate_id: boiler
    2:
      heat_pump:
        climate_id: heat_pump_thermostat
```

* **`water_heater`**: Thermostat is a mandatory server cluster of the device type, so this is the same binding as for the `thermostat` device type, but **heating only**: the Heating feature, `HeatingOnly` ControlSequence and Off/Heat SystemModes. Cooling, auto and other modes of the climate are not exposed (a warning is logged at boot if the climate can cool), and a climate in such a mode reports SystemMode Off. The Water Heater Mode and Water Heater Management clusters of the device type still have no delegate in this component, so the endpoint stays non-conformant (missing ChangeToMode) and the boost commands do nothing; the conformance checker reports this for a `water_heater` endpoint.
* **`heat_pump`**: the Matter device type only lists Thermostat as a **client** cluster, so a conforming heat pump keeps its thermostat on a child endpoint. This is the required layout, `climate_id` directly on `heat_pump` is a config error that shows it:

  ```yaml
  matter:
    endpoints:
      1:
        heat_pump: {}
      2:
        parent: 1
        thermostat:
          climate_id: heat_pump_thermostat
  ```

  If you need the climate entity on the heat pump endpoint itself (Home Assistant shows a Thermostat server wherever it is), opt in with `thermostat_on_endpoint: true` next to `climate_id`. That creates the Thermostat **server** next to the device type, which is not what the device type describes, and logs a warning. `thermostat_on_endpoint` together with `clusters: thermostat:` is rejected.

## Gaps

Items found by the HVAC device reproduction and not (fully) solved here:

| # | Gap | Status |
|---|---|---|
| G7 | Thermostat presets, setback, schedules, UI configuration, local temperature calibration | Not implemented. The features `Presets`, `Setback`, `ScheduleConfiguration`, `MatterScheduleConfiguration`, `ThermostatSuggestions` are rejected with `climate_id`. Presets would need a Thermostat delegate backed by ESPHome climate presets (preset list, `PresetTypes`, `SetActivePresetRequest`, atomic request handling); that is not cheap. |
| G8 | `fan_id` only on `fan` | Done: every device type with a Fan Control server, and `clusters: fan_control:`. |
| G10 | Wind / Step features without behaviour | Done: implemented for fans (Wind = preset modes, Step = speed), Step for climates, rejected without a binding. |
| G11 | Filter monitoring has no entity | Done: sensors, binary sensor, button / automation. |
| G1 | Room AC Fan Control not bound to the climate | Done: `clusters: fan_control: {climate_id}` (fan modes and swing). |
| - | Water Heater Mode / Management and Heat Pump energy clusters have no delegates | Open, see the repro-complex gap list. |
| - | Hardware behaviour of everything on this page | Not verified: no device was commissioned. Compile checked and unit tested only. |
