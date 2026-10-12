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

XY and color temperature are always available on an `extended_color_light`. Hue/saturation, enhanced hue and color loop are optional features of the ColorControl cluster. Many controllers (including Home Assistant) use XY, but some only use hue and saturation. Enable them with the `features` option. Because the `Hue And Saturation` name contains spaces, it is written as is:

```yaml
matter:
  endpoints:
    1:
      extended_color_light:
        light_id: some_light
        features:
          - Hue And Saturation
          - EnhancedHue
          - ColorLoop
```

When the color of the ESPHome light changes (for example via the ESPHome API or a physical button), esphome-matter reports it in every representation that the endpoint has: XY, hue and saturation and enhanced hue are all updated, so they stay in sync. The `ColorMode` attribute reports what the light is actually showing right now. For example, a light with the `rgb_cold_warm_white` color mode (an RGBWW light without `color_interlock`) reports color temperature mode while it shows white, and XY mode while it shows a color. A black RGB value has no valid color, so it is not reported and the previous color is kept.

The light only receives the attributes that belong to the active color mode. Attributes that CHIP keeps in sync in other modes are ignored. This stops, for example, the XY coordinates that CHIP derives from a color temperature from overriding that color temperature.

**ColorLoop.** ESPHome has no generic color loop effect for non-addressable lights (`addressable_rainbow` only works on addressable strips), so there is no ESPHome effect to map `ColorLoop` to. When the `ColorLoop` feature is enabled, CHIP runs the loop itself by continuously changing the hue attributes and the light follows those hue changes like any other transition (see below). It looks best together with the `EnhancedHue` feature. Starting and stopping it, its direction and time are handled by CHIP and the light doesn't know about it, so it is not a native ESPHome effect. Without the `ColorLoop` feature, color loop commands are not supported.

ESPHome light effects are not visible in Matter, because Matter has no generic effect list.

##### Transitions

A Matter controller can ask for a transition time with `MoveToLevel`, `MoveToColor`, `MoveToColorTemperature`, `MoveToHue` and the other move commands. These commands are executed by the CHIP stack, which moves the attribute to the target in many small steps and writes every step. Neither the commanded target nor the transition time is visible to esphome-matter, only the steps.

Applying every step as an instant ESPHome change would make the light jump in small increments, and cancelling the stepping isn't an option because the attributes have to follow the steps anyway. Instead, esphome-matter does the following:

* Changes are collected on the main loop and applied as a single light call. An `OnOff` and level change that arrive together, for example from `MoveToLevelWithOnOff`, turn the light on at the right brightness instead of first at its old brightness.
* A change that is the first one after a pause is applied instantly. That is the case for `On`, `Off` and for commands without a transition time.
* A change that follows another one within 300 ms is considered to be a step of a running transition. It is applied with an ESPHome transition as long as the time since the previous step. The light fades from step to step, at the pace that CHIP sets, so a controller's 5 second fade takes 5 seconds on the light and the intermediate steps no longer flicker. The light is one step behind the Matter attribute, which is not noticeable.
* Steps closer together than 20 ms are applied instantly. They are too short for a transition to make a difference.
* The `default_transition_length` of the ESPHome light is only used for changes that are made from the ESPHome side, not for changes that come from Matter.

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
A: Check the StartUpCurrentLevel attribute on the LevelControl cluster. Home Assistant appears to set this to 0 sometimes. This results in a list that is "on" after a restart but with a brightness of zero.

Q: Startup behaviour is set "previous state" but the colour of the light isn't remembered.
A: The StartUpColorTemperatureMireds attribute on the ColorControl cluster might be set to a non-null value. This value must be set to null for colours to be remembered.
