There are four different lights in Matter; `on_off_light`, `dimmable_light`, `color_temperature_light` and `extended_color_light`. These are fully supported by esphome-matter.

The `on_off_light` is the simplest light device type. This light only has the OnOff cluster and as the name suggests, only support the "on" and "off" states.

The `dimmable_light` adds the LevelControl cluster on top to support dimming.

The `color_temperature_light` adds the ColorControl cluster to support color temperature.

The `extended_color_light` is for RGB lights. This device type adds additional attributes and commands to the ColorControl cluster to support the full colour spectrum.

### Pre-defined behaviour

All four lights support the `light_id` config option. This maps a light to a Matter endpoint and handles the default behaviour for a Matter light. This is usually what you want.

```yaml
matter:
  endpoints:
    1:
      extended_color_light:
        light_id: some_light
        #  It can be useful to set a higher minimum, for example when a low brightness
        #  turns off the light completely. The native Matter level range is 1-254, but
        #  percentages are also valid for the min_level and max_level options.
        # min_level: 1
        # max_level: 254

lights:
  - id: some_light
```

State is synchronized both ways between the ESPHome light entity and Matter. So if you set the brightness via Home Assistant, the Matter brightness attribute will be updated accordingly. The other way around is also true.

##### Colors

Matter can describe a color in several ways and the active one is stored in the `ColorMode` attribute. esphome-matter follows whichever mode the controller uses:

| Matter color mode | Attributes | ESPHome light |
| --- | --- | --- |
| XY | `CurrentX`, `CurrentY` | RGB |
| Color temperature | `ColorTemperatureMireds` | color temperature (or cold/warm white) |
| Hue and saturation | `CurrentHue`, `CurrentSaturation` | RGB |
| Enhanced hue and saturation | `EnhancedCurrentHue`, `CurrentSaturation` | RGB |

XY and color temperature are always available on an `extended_color_light`. Hue/saturation, enhanced hue and color loop are optional features of the ColorControl cluster. Many controllers (including Home Assistant) use XY, but some only use hue and saturation. Enable them with the `with_features` option. The feature can be written as `hue_and_saturation`, `HueAndSaturation` or `Hue And Saturation`:

```yaml
matter:
  endpoints:
    1:
      extended_color_light:
        light_id: some_light
        with_features:
          - Hue And Saturation
          - EnhancedHue
          - ColorLoop
```

When the color of the ESPHome light changes (for example via the ESPHome API or a physical button), esphome-matter reports it in every representation that the endpoint has: XY, hue and saturation and enhanced hue are all updated, so they stay in sync. The `ColorMode` attribute reports what the light is actually showing right now. For example, a light with the `rgb_cold_warm_white` color mode (an RGBWW light without `color_interlock`) reports color temperature mode while it shows white, and XY mode while it shows a color. A black RGB value has no valid color, so it is not reported and the previous color is kept.

The light only receives the attributes that belong to the active color mode. Attributes that CHIP keeps in sync in other modes are ignored. This stops, for example, the XY coordinates that CHIP derives from a color temperature from overriding that color temperature. When a controller switches an RGBWW light between a color and a color temperature, the white channels (or the RGB channels) of the other mode are switched off.

**ColorLoop.** ESPHome has no generic color loop effect for non-addressable lights (`addressable_rainbow` only works on addressable strips), so there is no ESPHome effect to map `ColorLoop` to. When the `ColorLoop` feature is enabled, CHIP runs the loop itself by continuously changing the hue attributes and the light follows those hue changes like any other transition (see below). It looks best together with the `EnhancedHue` feature. Starting and stopping it, its direction and time are handled by CHIP and the light doesn't know about it, so it is not a native ESPHome effect. Without the `ColorLoop` feature, color loop commands are not supported.

ESPHome light effects are not visible in Matter, because Matter has no generic effect list.

##### Transitions

A Matter controller can ask for a transition time with `MoveToLevel`, `MoveToLevelWithOnOff`, `MoveToColor`, `MoveToColorTemperature`, `MoveToHue`, `MoveToSaturation`, `MoveToHueAndSaturation`, `EnhancedMoveToHue` and `EnhancedMoveToHueAndSaturation`, and with the `On`, `Off` and `Toggle` commands. esphome-matter looks at these commands before the CHIP stack executes them and does the following:

* The target and the transition time (in 1/10 s) are read from the command, and the ESPHome light gets a single light call with the real target and the real transition length. A controller's 5 second fade is a 5 second fade on the light, without intermediate steps.
* A `TransitionTime` of null (`MoveToLevel` and `MoveToLevelWithOnOff`) uses the `OnOffTransitionTime` attribute of the LevelControl cluster, or no transition if the endpoint doesn't have that attribute. `On`, `Off` and `Toggle` use `OnOffTransitionTime` as well, as CHIP does; `OnTransitionTime` and `OffTransitionTime` are not used, because CHIP doesn't use them either. `On` goes to `OnLevel` if that is set. A `TransitionTime` of 0 applies the change instantly.
* `MoveToLevelWithOnOff` turns the light on, or off at the lowest level, and the level is limited to `MinLevel` and `MaxLevel` like CHIP does. Commands that CHIP ignores, such as `MoveToLevel` or a color command on a light that is off while `ExecuteIfOff` is not set in the `Options`, don't change the light either.
* CHIP still executes the command and steps the Matter attributes towards the target, so the attributes keep reporting the progress of the transition. While the transition of a command runs (plus half a second), these intermediate attribute changes are not applied to the light, so they don't interrupt the fade or restart it in small increments. Level and OnOff changes are ignored this way after level and `On`/`Off`/`Toggle` commands, color attributes after color commands.
* When that time is over, the light is synchronized with the final attribute values in a single instant change. That normally changes nothing; it corrects any difference between the command and what CHIP ended up with. `Stop`, `StopWithOnOff` and `StopMoveStep` end the window directly and the light settles on the value where CHIP stopped.
* A new command during a transition starts from the current state of the light, which is what the controller expects.
* The direction of `MoveToHue` and `EnhancedMoveToHue` is not used. ESPHome fades in RGB space, so the light takes the shortest path between the two colors. Lights without brightness control (`on_off_light`) never use transitions.
* The `default_transition_length` of the ESPHome light is only used for changes that are made from the ESPHome side, not for changes that come from Matter.

Commands with a rate instead of a transition time (`Move`, `Step`, `MoveHue`, `StepColor`, `ColorLoopSet` and so on, and their `WithOnOff` variants) have no target or end time that could be given to the light. CHIP runs them by writing every step to the attributes, and for these esphome-matter falls back to following the steps. Changes are collected on the main loop and applied as a single light call; a change that follows another one within 300 ms is applied with a transition as long as the time since the previous step, so the light fades from step to step. The first change after a pause is applied instantly and the light is one step behind the Matter attribute. This is a heuristic: steps further apart than 300 ms are applied as separate instant changes.

The state that ESPHome publishes after such a change contains the values that came from Matter and is not sent back to Matter, so a running CHIP transition is not disturbed. A change that is made on the ESPHome side during a transition is reported to Matter normally.

##### Power-on behaviour

Matter automatically handles power-on behaviour for lights. The value of the `StartUpOnOff` attribute on the OnOff cluster determines the power-on behaviour. The default is "null" which means the previous state will be restored, but "on", "off" and "toggle" are also possible values.

Besides the `StartUpOnOff` attribute, there is also the `StartUpCurrentLevel` attribute on the `LevelControl` cluster and the `StartUpColorTemperatureMireds` attribute on the `ColorControl` cluster. By default, these are null, which means the previous state will be restored. Currently, Home Assistant has a bug where these values can't be set back to null once set to a numerical value. So if you have set these attributes and want to restore the defaults, you'll have to find another way, possibly by sending raw Matter commands via your Matter controller.

After a restart, the level and color are restored on the ESPHome light before it is turned on, in a single light call, so the light doesn't flash at full brightness first.

Since Matter now manages the restore behaviour, the `restore_mode` option of an esphome light should not be used anymore.

### Custom behaviour

Alternatively, the behaviour of the light can be defined fully using esphome actions and triggers. The [extended-color-light](../packages/extended-color-light.yaml) package contains a re-implementation of all the pre-defined actions.

### F.A.Q.

Q: Even though the startup behaviour is set to "on" or "previous state", the light doesn't turn on after a restart.
A: Check the StartUpCurrentLevel attribute on the LevelControl cluster. Home Assistant appears to set this to 0 sometimes. This results in a light that is "on" after a restart but with a brightness of zero.

Q: Startup behaviour is set "previous state" but the colour of the light isn't remembered.
A: The StartUpColorTemperatureMireds attribute on the ColorControl cluster might be set to a non-null value. This value must be set to null for colours to be remembered.
