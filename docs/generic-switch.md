# Generic Switch (stateless programmable button)

The `generic_switch` device type (0x000F) exposes a physical button as a Matter **Generic Switch**. It has no on/off state to control. It only emits *events* (`InitialPress`, `ShortRelease`, `LongPress`, ...) that Matter controllers use as automation triggers. This is the Matter equivalent of a stateless programmable switch or wireless button.

This is different from the client switches (`on_off_light_switch`, `dimmer_switch`) described in [binding.md](./binding.md), which send commands to other Matter devices.

```yaml
matter:
  endpoints:
    1:
      generic_switch:
        binary_sensor_id: button

binary_sensor:
  - id: button
    platform: gpio
    pin:
      number: GPIO0
      mode:
        pullup: true
        input: true
      inverted: true
```

With `binary_sensor_id` set, press and release timing of the binary sensor is turned into Matter events automatically. See [examples/wireless-button.yaml](../examples/wireless-button.yaml) for a complete example.

Controllers show the events differently. Home Assistant creates an `event` entity whose `event_type` is one of `initial_press`, `short_release`, `long_press`, `long_release`, `multi_press_ongoing` or `multi_press_complete`. Apple Home and Google Home support is controller specific and not verified here.

## Features and events

An event is only generated when the matching Switch cluster feature is enabled on the endpoint. Events of disabled features are dropped silently by Matter, so the `features` option must match what is emitted.

| Feature                     | Events                                    |
|-----------------------------|-------------------------------------------|
| `MomentarySwitch`           | `initial_press`                           |
| `MomentarySwitchRelease`    | `short_release`                           |
| `MomentarySwitchLongPress`  | `long_press`, `long_release`              |
| `MomentarySwitchMultiPress` | `multi_press_ongoing`, `multi_press_complete` |
| `LatchingSwitch`            | `switch_latched`                          |
| `ActionSwitch`              | Replaces `MomentarySwitchRelease`: one `initial_press` per sequence, no `short_release`, no `multi_press_ongoing`, `multi_press_complete` at the end. esp-matter always adds `MomentarySwitchMultiPress` to it. |

The Switch cluster requires exactly one of `MomentarySwitch` and `LatchingSwitch`. esphome-matter selects `MomentarySwitch` automatically if neither is configured. Use `features: [LatchingSwitch]` for a toggle switch. Other features that a selected feature depends on (e.g. `MomentarySwitchRelease` for `MomentarySwitchLongPress`) are enabled automatically.

If `features` is not configured, `MomentarySwitchRelease` is enabled. When `binary_sensor_id` is set, `MomentarySwitchRelease`, `MomentarySwitchLongPress` and `MomentarySwitchMultiPress` are enabled.

## Binding a binary sensor

| Option               | Default | Description |
|----------------------|---------|-------------|
| `binary_sensor_id`   |         | Binary sensor that is the button. Use `inverted`, `filters`, ... on the binary sensor for debouncing and polarity. |
| `long_press_time`    | `500ms` | How long the button must be held for `long_press`. Requires `MomentarySwitchLongPress`. |
| `multi_press_window` | `300ms` | Time after a release in which another press continues a multi-press sequence. Requires `MomentarySwitchMultiPress`. |
| `multi_press_max`    | `2`     | Maximum number of presses of a multi-press sequence (2-255), exposed as the `MultiPressMax` attribute. Requires `MomentarySwitchMultiPress`. |

The event sequences that are emitted (for the enabled features) are:

| Gesture      | Events |
|--------------|--------|
| Short press  | `initial_press`, `short_release`, `multi_press_complete` (count 1) |
| Double press | `initial_press`, `short_release`, `multi_press_ongoing` (count 2), `short_release`, `multi_press_complete` (count 2) |
| Long press   | `initial_press`, `long_press` (after `long_press_time`), `long_release` |

`multi_press_complete` is sent once `multi_press_window` has passed after the last release. Pressing more often than `multi_press_max` ends the sequence with `multi_press_complete` with count 0. Only the first press of a sequence can become a long press (as the Matter certification tests require): holding the second press of a double press is still a double press. A long press is not part of a multi-press sequence, so the next press starts a new one, and `short_release` is not sent after a long press.

The `CurrentPosition` attribute follows the button: it is `1` while the button is pressed and `0` otherwise (`NumberOfPositions` is 2), also when the enabled features have no event for the press or release. For a `LatchingSwitch`, the sensor state is the position and `switch_latched` is sent on each change.

## matter.send_switch_event

The `matter.send_switch_event` action sends a single event. Use it for buttons that are not binary sensors, or to build custom gestures, for example from `on_multi_click`.

```yaml
binary_sensor:
  - id: custom_button
    platform: gpio
    # ...
    on_press:
      matter.send_switch_event:
        endpoint: switch_endpoint
        event: initial_press
    on_release:
      matter.send_switch_event:
        endpoint: switch_endpoint
        event: short_release
```

| Option     | Default | Description |
|------------|---------|-------------|
| `endpoint` |         | ESPHome id or number of the Matter endpoint. |
| `event`    |         | `initial_press`, `short_release`, `long_press`, `long_release`, `multi_press_ongoing`, `multi_press_complete` or `switch_latched`. |
| `position` | `1`     | Switch position. For release events this is the position the switch was released from. Templatable. |
| `count`    | `1`     | Number of presses for `multi_press_ongoing` (at least 2) and `multi_press_complete`. Templatable. |

The action also updates `CurrentPosition`: press and latch events move it to `position`, release events move it to `0`.

Do not combine `binary_sensor_id` with manual events on the same endpoint unless you know what you are doing. Both would produce events.

## Notes

- Events are only sent after Matter has started. A press before that is dropped.
- The Matter stack accepts an event even if it is dropped afterwards (for example, because the feature is not enabled), so a missing event is not reported as an error. The `matter.switch` log tag shows every event sent at debug level, and a warning if the endpoint has no Switch cluster.
- When multiple buttons are exposed as separate generic switch endpoints, controllers cannot tell them apart other than by endpoint. Name them in your controller.

## Choosing features for other device types

The Switch cluster feature choice described above is an instance of a more general rule: a cluster that a device type requires may have a mandatory choice between features, and esp-matter refuses to create the cluster when the choice is missing. The choice is resolved when the configuration is validated. For the Switch cluster (`generic_switch`, `doorbell`, `audio_doorbell`) `MomentarySwitch` is the default and at most one of `LatchingSwitch` and `MomentarySwitch` is accepted. Other choices (`Lift`/`Tilt` of `window_covering`, `Heating`/`Cooling` of `thermostat`, the sensor types of `occupancy_sensor`, ...) are not validated here. They are selected with `features:` or derived from the bound `cover_id`, `climate_id` or binary sensor.
