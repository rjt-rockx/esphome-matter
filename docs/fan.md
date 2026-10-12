The `fan` device type exposes an ESPHome fan as a Matter fan. It contains the Fan Control cluster (besides the mandatory Identify, Groups and Descriptor clusters).

### Pre-defined behaviour

The `fan_id` config option maps an ESPHome fan to a Matter endpoint and handles the default behaviour for a Matter fan. This is usually what you want.

```yaml
matter:
  endpoints:
    1:
      fan:
        fan_id: some_fan

fan:
  - platform: speed
    id: some_fan
    output: some_output
    speed_count: 3
    restore_mode: ALWAYS_OFF
```

State is synchronized both ways between the ESPHome fan entity and Matter. If the fan is changed in Home Assistant, the Matter attributes are updated, and the other way around. See [ceiling-fan.yaml](../examples/ceiling-fan.yaml) for a complete example.

### Feature mapping

The Matter features of the Fan Control cluster are selected from the traits of the ESPHome fan when the endpoint is created. The `features` option of the `fan` device type is not needed for these.

| ESPHome fan            | Matter                                                                  |
|------------------------|-------------------------------------------------------------------------|
| on/off                 | `FanMode` / `PercentSetting` (0 is off)                                 |
| `speed_count`          | `MultiSpeed` feature, `SpeedMax` = `speed_count`                        |
| `oscillation_output`   | `Rocking` feature, `RockSupport` = left/right, `RockSetting` bit 0      |
| `direction_output`     | `AirflowDirection` feature (forward = 0, reverse = 1)                   |

The `FanModeSequence` is derived from the speed count: 3 or more speeds give Off/Low/Medium/High, 2 speeds give Off/Low/High and a fan without speed control gives Off/High.

A fan that doesn't support speed control (for example `platform: binary`) is exposed without the MultiSpeed feature. Setting any speed on it turns it on.

### How speeds are converted

Matter describes the fan speed as a percentage (`PercentSetting`) and, with `MultiSpeed`, also as a discrete step (`SpeedSetting`, 0 to `SpeedMax`). ESPHome uses the step (`1` to `speed_count`). A percentage `p` is converted to the step `ceil(p * speed_count / 100)`, which is the same rounding the Matter cluster uses. The Low, Medium and High fan modes map to the step 1, the middle step and the last step.

The `PercentCurrent` and `SpeedCurrent` attributes follow the ESPHome fan state (`speed * 100 / speed_count` rounded down, and 0 when the fan is off).

### Limitations

* The `Auto` fan mode has no ESPHome equivalent and is ignored. Do not enable the `Auto` feature unless you handle it yourself.
* The `Wind` and `Step` features are not mapped. They can be enabled with the `features` option and handled with `on_attribute` automations.
* ESPHome fan presets are not exposed.
* Matter persists the fan mode (and so the speed). Use `restore_mode: ALWAYS_OFF` on the ESPHome fan so Matter restores the power-on state. A warning is logged when another restore mode is used.
* The Fan device type has no OnOff cluster, so the on/off state is only available through the Fan Control cluster.

### Custom behaviour

The fan can also be controlled fully with ESPHome actions and `on_attribute` triggers by omitting `fan_id`, as with other device types. Without `fan_id` the features are configured with the `features` option and the cluster uses the esp-matter defaults.
