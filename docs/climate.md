# Climate (Thermostat and Room Air Conditioner)

The `thermostat` (0x0301) and `room_air_conditioner` (0x0072) device types can be bound to an ESPHome `climate` entity with `climate_id`. State is synchronized both ways, like it is for lights.

```yaml
matter:
  endpoints:
    1:
      thermostat:
        climate_id: room_heater

climate:
  - platform: thermostat
    id: room_heater
    sensor: room_temperature
    heat_action:
      - switch.turn_on: heater_relay
    idle_action:
      - switch.turn_off: heater_relay
```

See `examples/thermostat.yaml` for a complete heat-only example.

## Features are derived from the climate

Do not configure `Heating`, `Cooling` or `AutoMode` in `features` when `climate_id` is set (this is a config error). They are derived from the climate traits when the endpoint is built:

| Climate traits | Matter |
|---|---|
| `HEAT` mode | Heating feature |
| `COOL` mode | Cooling feature |
| `HEAT_COOL` or `AUTO` mode, or two-point target temperature | Heating and Cooling features, plus AutoMode if `HEAT_COOL` or `AUTO` is supported |
| Heating only / cooling only / both | ControlSequenceOfOperation HeatingOnly / CoolingOnly / CoolingAndHeating |
| `visual` min/max temperature | Abs, Min and Max setpoint limits |
| Action support | ThermostatRunningState attribute |

If a climate supports neither heating nor cooling, a thermostat exposes Heating and a room air conditioner exposes Cooling (Matter requires at least one).

Without `climate_id` you can still select features by hand, for example `features: [Heating, Cooling, AutoMode]`. Matter requires at least one of Heating and Cooling, and AutoMode needs both.

## Mapping

| ESPHome | Matter |
|---|---|
| mode `OFF`, `HEAT`, `COOL`, `FAN_ONLY`, `DRY` | SystemMode Off, Heat, Cool, FanOnly, Dry |
| mode `HEAT_COOL`, `AUTO` | SystemMode Auto. Writes of Auto map to `HEAT_COOL` if supported, else `AUTO` |
| `target_temperature` (single point) | OccupiedHeatingSetpoint in `HEAT` mode, OccupiedCoolingSetpoint in `COOL` mode |
| `target_temperature_low` / `_high` (two point) | OccupiedHeatingSetpoint / OccupiedCoolingSetpoint |
| `current_temperature` | LocalTemperature (0.01 C, null when unknown) |
| action `HEATING`, `COOLING` (and `DRYING`), `FAN` | ThermostatRunningState Heat, Cool, Fan bits |
| `visual` min / max | Abs/Min/Max Heat and Cool setpoint limits |
| (none) | MinSetpointDeadBand: 2.0 C (AutoMode only) |

Modes are mapped by name. Note that ClimateAction `FAN` (6) and ClimateMode `FAN_ONLY` (4) have different values.

Matter requests are checked against the climate traits before a `ClimateCall` is made, because ESPHome silently drops unsupported values. A rejected request is logged and the Matter attribute is set back to the climate state. This applies to:

- SystemModes the climate does not support, and EmergencyHeat, Precooling and Sleep, which have no ESPHome equivalent.
- Writes to the setpoint that is not active for a single-point climate (for example the cooling setpoint while in `HEAT` mode). In `HEAT_COOL` and `AUTO` mode a single-point target is not adjustable.

For a room air conditioner the OnOff cluster follows the climate mode: off sets `OFF`, on restores the last active mode (or the first supported one).

## SetpointRaiseLower

The command is part of the Thermostat cluster and is handled by the Matter stack, which writes the setpoint attributes. These writes are expected to reach the binding like any other attribute write, but this has not been verified on hardware.

## Not supported yet

- Fan modes (FanControl on the room air conditioner), swing modes, humidity, presets and schedules.
- ThermostatRunningState when the climate does not report actions.
- Apple Home behaviour is untested. Apple does not publish its Thermostat requirements.

Some controllers expect the heating setpoint to stay below the cooling setpoint (by the dead band in AutoMode). Use a sensible `visual` range and defaults.

The feature-selection approach was informed by the esp-matter thermostat requirements; the fork github.com/eth2353/esphome-matter was checked for prior art and has no thermostat or climate support.
