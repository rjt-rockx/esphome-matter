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

### Air quality level (the `AirQuality` attribute)

The Air Quality cluster of the `air_quality_sensor` has a single enumerated attribute: Unknown, Good, Fair, Moderate,
Poor, VeryPoor, ExtremelyPoor (0 to 6). Controllers show it as the headline "air quality" of the device (Apple Home
shows only this, not the concentrations). Without a source it stays **Unknown**. `air_quality` provides the source in two ways:

```yaml
matter:
  endpoints:
    # 1. A sensor that already reports the level as a number from 0 to 6 (for example from a template sensor).
    1:
      air_quality_sensor:
        air_quality: air_quality_index
        carbon_dioxide: co2

    # 2. Derived from a measurement with thresholds.
    2:
      air_quality_sensor:
        air_quality:
          sensor: pm_2_5          # any sensor, usually one that is also mapped below
          thresholds:             # optional for pm25, pm10 and carbon_dioxide sensors (device_class)
            fair: 9.0
            moderate: 35.4
            poor: 55.4
            very_poor: 125.4
            extremely_poor: 225.4
        pm_2_5: pm_2_5
```

* **Index form** (`air_quality: sensor_id`): the value is rounded and must be 0 to 6, anything else (including NaN)
  reports Unknown. All optional levels (Fair, Moderate, VeryPoor, ExtremelyPoor) are enabled. A sensor with a
  `unit_of_measurement` triggers a warning because it probably is a measurement; use the mapping form for those.
* **Derived form** (`sensor` and `thresholds`): a level applies to values **strictly above** its threshold (the top
  of the previous category), so a reading equal to a threshold is still the lower level. The level is Good up to and
  including the lowest threshold. A small hysteresis (`hysteresis`, default `2%` of the threshold) keeps a reading that
  hovers around a threshold from flipping the level: it only drops again at or below `threshold - 2%`. Higher levels win: with the values in the example above, 40 µg/m³ is Moderate and
  60 µg/m³ is Poor. Thresholds must increase with the level. You may leave levels out (`fair: 800` and `poor: 1500`
  only reports Good, Fair and Poor); only the levels you name enable their cluster feature. A `nan` reading reports Unknown.
* **Default thresholds** when `thresholds` is omitted and the sensor has one of these `device_class` values. They are
  indicative values, not a Matter requirement, and expect the listed unit:

  | `device_class` | Unit | Fair above | Moderate above | Poor above | VeryPoor above | ExtremelyPoor above |
  |---|---|---|---|---|---|---|
  | `pm25` | µg/m³ | 9.0 | 35.4 | 55.4 | 125.4 | 225.4 |
  | `pm10` | µg/m³ | 54 | 154 | 254 | 354 | 424 |
  | `carbon_dioxide` | ppm | 800 | 1000 | 1500 | 2000 | 5000 |

  PM2.5 follows the US EPA AQI breakpoints of the 2024 revision (Good 0-9.0, Moderate 9.1-35.4, Unhealthy for
  Sensitive Groups 35.5-55.4, Unhealthy 55.5-125.4, Very Unhealthy 125.5-225.4, Hazardous 225.5 and up) and PM10 the
  EPA PM10 breakpoints; Moderate ... Hazardous map to Fair ... ExtremelyPoor. The CO2 values are common indoor guidance.
  The sensor's `unit_of_measurement` must match the table (`µg/m³`, `μg/m³` and `ug/m3` are all accepted) or the
  configuration is rejected; with explicit `thresholds` the unit is not checked. Any other sensor
  (VOC, formaldehyde, ...) needs explicit `thresholds`; the configuration is rejected otherwise.
* The thresholds are fixed when the firmware is built. Combining several pollutants into one level (the worst of PM2.5
  and CO2, for example) is not done: use a template sensor that returns the index of your choice and the index form.

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

`battery` on the endpoint works for every device type and is the recommended way: the battery then belongs to the same endpoint as the sensor, which is where controllers are most likely to look for it (not verified for every controller). Do not use both on the same endpoint. The `power_source` device type requires a `battery` (or `with_features: [Wired]` for a mains powered source).

The percentage is converted to the half percent resolution of Matter (0 to 200, 0.5 % steps). The cluster's `Status` attribute is set to `Active`. `BatChargeLevel` follows the percentage (`Warning` below 20 %, `Critical` below 10 %, configurable).

`battery` also takes a map for the other battery attributes (voltage, charging state, replacement), can be placed on the root node with an `EndpointList`, and has a `wired` counterpart for mains supplies. See [power.md](power.md).

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
* `ElectricalEnergyMeasurement` is created when `energy_imported` (cumulative energy consumed) or `energy_exported` (cumulative energy produced) is mapped, and only the attribute of the mapped direction exists (`CumulativeEnergyImported` or `CumulativeEnergyExported`). kWh sensors are converted to the mWh of the `EnergyMeasurementStruct`. Use a sensor with `state_class: total_increasing`. Float sensors have about 7 significant digits, so a sensor with a large total (say 12345.678 kWh) can't report every Wh.
* `current_type` selects the electrical type: `ac` (default) or `dc`. DC only supports `voltage`, `active_current` and `active_power`. Mapping a sensor that requires AC (reactive/apparent/rms values, frequency, power factor) with `current_type: dc` is an error. `neutral_current` enables the PolyphasePower feature.

`topology` selects which endpoints the sensor measures (`node`, the default, `tree` or `set`), and the sensor can sit next to an application device type on the same endpoint (not on the root node). See [power.md](power.md#power-topology).

The accuracy that is reported for the measurements is a nominal one (10 % over the whole range) because esphome-matter doesn't know the accuracy of the measuring hardware.

Controller support for these clusters differs and has not been verified for every controller; expect some controllers to ignore them.

## Not supported

* `soil_sensor` exposes the Soil Measurement cluster (`soil_moisture`, a sensor in `%`, reported in 1 % steps). The measurement limits are fixed to 0 to 100 %. This mapping is untested on real hardware.
* Periodic energy, harmonics, power quality and the cumulative energy reset are not supported.
* The `BatVoltage`, `BatTimeRemaining` and charging attributes of the Power Source cluster are not supported.
