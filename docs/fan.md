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

The Matter features of the Fan Control cluster are selected from the traits of the ESPHome fan when the endpoint is created. These features can't be set with the `with_features` option when `fan_id` is used.

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

The `PercentCurrent` and `SpeedCurrent` attributes follow the ESPHome fan state (`speed * 100 / speed_count` rounded down, 100 for a fan without speed control, and 0 when the fan is off).

### Wind and Step

Both features are backed by the fan, or rejected when the configuration can't back them. Neither is a silent no-op.

**Step** (the `Step` command moves the speed up or down by one step) needs a fan with speed control. Add `step` to `with_features`:

```yaml
matter:
  endpoints:
    1:
      fan:
        fan_id: ceiling_fan
        with_features: [step]
```

`Direction` increase/decrease changes the speed by one step. `Wrap` continues from the highest speed to the lowest and the other way around; `LowestOff` (default on) makes Off the lowest step, otherwise the lowest step is speed 1 and a step down on a running fan stops there. A fan that is off is not turned on by a step down. `step` without `fan_id` is a config error (nothing would handle the command), and so is `step` on a `platform: binary` fan; any other fan without speed control fails the endpoint build with a log message.

**Wind** maps the Matter `WindSetting` (natural wind, sleep wind) to ESPHome fan **preset modes**. Add `wind` to `with_features`, or `wind_presets`, which implies it:

```yaml
matter:
  endpoints:
    1:
      fan:
        fan_id: ceiling_fan
        with_features: [wind]          # presets named "natural" and/or "sleep"
        # Names of your own presets:
        # wind_presets:
        #   natural: Breeze
        #   sleep: Quiet

fan:
  - platform: speed
    id: ceiling_fan
    # ...
    preset_modes: [Natural, Sleep]     # matched case insensitively
```

* At least one of the two presets must exist in the fan's static `preset_modes` list, otherwise the config is rejected with a message naming the fan and the presets. A name given in `wind_presets` that the fan doesn't have is also an error. `WindSupport` has the bits of the presets that exist.
* Selecting a preset (from ESPHome or a controller) sets the matching `WindSetting` bit; activating anything else clears it. ESPHome presets are exclusive, so when a controller sets both bits the one that is not active yet wins and `WindSetting` is corrected.
* Clearing `WindSetting` clears the preset the way ESPHome does for a manual speed change: by setting the current speed (at least 1). A fan without speed control can't clear its preset, the request is logged and `WindSetting` is set back.
* A fan that is off keeps `WindSetting` as ESPHome reports it. Setting a preset does not turn the fan on.
* `wind` and `wind_presets` need `fan_id`. Without a bound fan the config is rejected.

### Limitations

* The `Auto` fan mode has no ESPHome equivalent and is ignored. Do not enable the `Auto` feature unless you handle it yourself.
* Matter persists the fan mode (and so the speed). Use `restore_mode: ALWAYS_OFF` on the ESPHome fan so Matter restores the power-on state. A warning is logged when another restore mode is used.
* The Fan device type has no OnOff cluster, so the on/off state is only available through the Fan Control cluster.
* Preset modes other than the Wind ones are not exposed.
* The Wind / Step logic is verified by compiling and unit tests of the pure parts (`tests/unit/test_fan_step.py`), not on hardware.

### `fan_id` on other device types

`fan_id` is accepted on every device type that can contain the Fan Control server cluster, and in `clusters: fan_control:` on any endpoint. The mapping, features, Wind and Step work the same as for the `fan` device type. See [hvac.md](hvac.md) for room air conditioners, air purifiers and extractor hoods, and for binding Fan Control to the fan modes of a climate.

### Custom behaviour

The fan can also be controlled fully with ESPHome actions and `on_attribute` triggers by omitting `fan_id`, as with other device types. Without `fan_id` the features are configured with the `with_features` option and the cluster uses the esp-matter defaults. `wind` and `step` are not available then, because nothing would back them (the config is rejected).
