# Sensors

ESPHome `sensor` entities are exposed to Matter by mapping their id to an attribute of a device type, see [device types](device-types.md). This page describes what happens with the value of the sensor and which options are available.

```yaml
matter:
  endpoints:
    1:
      pressure_sensor: pressure_id
    2:
      temperature_sensor:
        temperature: temperature_id
      battery: battery_id
```

## Units

Matter uses a fixed unit for every measurement. esphome-matter looks at the `unit_of_measurement` of the sensor when the configuration is validated and converts the values when needed, so **always set the right `unit_of_measurement` (and preferably the `device_class`) on the sensor**.

| Option | Matter unit | Accepted sensor units |
|---|---|---|
| `temperature` | 0.01 °C | `°C`, `°F`, `K` |
| `pressure` | 0.1 kPa (= hPa) | `hPa`, `mbar`, `Pa`, `kPa`, `bar`, `mmHg`, `inHg`, `psi` |
| `relative_humidity` | 0.01 % | `%` |
| `illuminance` | log scale of lux | `lx` |
| `flow` | 0.1 m³/h | `m³/h`, `L/h`, `L/min`, `L/s`, `m³/min`, `m³/s` |
| `voltage`, `rms_voltage` | mV | `V`, `mV`, `kV` |
| `active_current`, `reactive_current`, `apparent_current`, `rms_current`, `neutral_current` | mA | `A`, `mA`, `kA` |
| `active_power`, `rms_power` | mW | `W`, `mW`, `kW` |
| `apparent_power` | mVA | `VA`, `kVA` |
| `reactive_power` | mvar | `var`, `kvar` |
| `frequency` | mHz | `Hz`, `mHz`, `kHz` |
| `power_factor` | 0.01 % | no unit (ratio -1.0 to 1.0) or `%` |
| `energy_imported`, `energy_exported` | mWh | `kWh`, `Wh`, `MWh` |
| `battery` | 0.5 % | `%` |

A sensor without `unit_of_measurement` is assumed to use the Matter unit of the table (hPa for pressure, °C for temperature, ...) and a warning is logged. These checks are done during `esphome config`:

* a unit that belongs to a different kind of measurement (for example a `°C` sensor mapped to `pressure`, or a `V` sensor mapped to `battery`) is an **error**,
* an unknown unit, a missing unit or a `device_class` that does not fit the attribute is a **warning**.

Pressure sensors are the most common mistake: ESPHome sensors usually report hPa, but many (BMP280 based boards, for example) report Pa. Matter expects 0.1 kPa, which is the same number as hPa, so a sensor in Pa is divided by 100 and a sensor in kPa is multiplied by 10.

## Air quality (concentration)

The concentration clusters of the `air_quality_sensor` have a mandatory `MeasurementUnit` attribute. It is derived from the `unit_of_measurement` of each bound sensor:

| Sensor unit | Advertised `MeasurementUnit` |
|---|---|
| `ppm`, `ppb`, `ppt` | ppm, ppb, ppt |
| `µg/m³` (`ug/m3`), `mg/m³`, `ng/m³` | ugm3, mgm3, ngm3 |
| `Bq/m³` | bqm3 |
| `pCi/L` | bqm3 (value multiplied by 37) |
| `#/m³`, `#/cm³` | pm3 (particles per m³) |

If the sensor has no (known) unit, the default of the option is used: **ugm3** for `pm_1`, `pm_2_5` and `pm_10`, **bqm3** for `radon` and **ppm** for everything else.

To advertise a different unit than the sensor reports, use `measurement_units`. The value is converted when both units measure the same quantity (ppm to ppb, µg/m³ to mg/m³, ...). A conversion between different kinds (for example µg/m³ to ppm) is not possible because it depends on the substance and is rejected.

```yaml
matter:
  endpoints:
    1:
      air_quality_sensor:
        carbon_dioxide: co2
        formaldehyde: ch2o   # reports ppm
        measurement_units:
          formaldehyde: ppb  # but Matter shows ppb
```

The valid values are `ppm`, `ppb`, `ppt`, `mgm3`, `ugm3`, `ngm3`, `pm3` and `bqm3`.

Note: the MeasurementUnit is part of the cluster server that esphome-matter registers for these clusters. If another server was already registered for the same cluster, the unit cannot be changed and a warning is logged at boot.

## Battery

The Matter `Power Source` cluster reports `BatPercentRemaining`. Battery sensors are bound with the `battery` option, which takes a sensor with a battery level in `%` (Matter has no way to report a battery voltage as a level; use a template sensor to calculate the level from a voltage, see the [example](../examples/all-sensors.yaml)).

There are two equivalent ways to do this:

```yaml
matter:
  endpoints:
    # 1. On the endpoint of the sensor itself. A Power Source cluster with the Battery feature
    #    is added to the endpoint next to the sensor clusters.
    1:
      temperature_sensor: temperature_id
      battery: battery_id

    # 2. On a dedicated power source endpoint.
    2:
      power_source:
        battery: battery_id
```

`battery` on the endpoint works for every device type and is the recommended way: the battery then belongs to the same endpoint as the sensor, which is where controllers are most likely to look for it (not verified for every controller). Do not use both on the same endpoint. The `power_source` device type requires a `battery` (or `features: [Wired]` for a mains powered source).

The percentage is converted to the half percent resolution of Matter (0 to 200, 0.5 % steps). The cluster's `Status` attribute is set to `Active`. The `BatChargeLevel` attribute stays `OK`.

## Electrical measurements

The `electrical_sensor` device type exposes power and energy sensors. The measurement clusters are created automatically based on the sensors that are mapped:

```yaml
matter:
  endpoints:
    1:
      electrical_sensor:
        voltage: voltage_id
        active_current: current_id
        active_power: power_id
        frequency: frequency_id
        power_factor: power_factor_id
        energy_imported: energy_id
```

* `ElectricalPowerMeasurement` is created when any of `voltage`, `active_current`, `reactive_current`, `apparent_current`, `active_power`, `reactive_power`, `apparent_power`, `rms_voltage`, `rms_current`, `rms_power`, `frequency`, `power_factor` or `neutral_current` is mapped. Without any sensor the cluster is created anyway (the device type requires at least one measurement cluster).
* `ElectricalEnergyMeasurement` is created when `energy_imported` (cumulative energy consumed) or `energy_exported` (cumulative energy produced) is mapped. kWh sensors are converted to the mWh of the `EnergyMeasurementStruct`. Use a sensor with `state_class: total_increasing`. Float sensors have about 7 significant digits, so a sensor with a large total (say 12345.678 kWh) can't report every Wh.
* `current_type` selects the electrical type: `ac` (default) or `dc`. DC only supports `voltage`, `active_current` and `active_power`. Mapping a sensor that requires AC (reactive/apparent/rms values, frequency, power factor) with `current_type: dc` is an error. `neutral_current` enables the PolyphasePower feature.

The accuracy that is reported for the measurements is a nominal one (10 % over the whole range) because esphome-matter doesn't know the accuracy of the measuring hardware.

Controller support for these clusters differs and has not been verified for every controller; expect some controllers to ignore them.

## Not supported

* `soil_sensor` exposes the Soil Measurement cluster (`soil_moisture`, a sensor in `%`, reported in 1 % steps). The measurement limits are fixed to 0 to 100 %. This mapping is untested on real hardware.
* Periodic energy, harmonics, power quality and the cumulative energy reset are not supported.
* The `BatVoltage`, `BatTimeRemaining` and charging attributes of the Power Source cluster are not supported.
