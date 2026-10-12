# Binary sensors

ESPHome `binary_sensor` entities can be bound to these Matter device types:

| Device type | Config key | Matter attribute | Fitting ESPHome `device_class` |
|---|---|---|---|
| `contact_sensor` | `boolean_state` | BooleanState `StateValue` | `door`, `window`, `opening`, `garage_door` |
| `occupancy_sensor` | `occupancy` | OccupancySensing `Occupancy` | `motion`, `occupancy`, `presence` |
| `water_leak_detector` | `boolean_state` | BooleanState `StateValue` | `moisture` |
| `water_freeze_detector` | `boolean_state` | BooleanState `StateValue` | `cold` |
| `rain_sensor` | `boolean_state` | BooleanState `StateValue` | `moisture` (ESPHome has no `rain` class) |
| `smoke_co_alarm` | `smoke_state` | SmokeCoAlarm `SmokeState` | `smoke` |
| `smoke_co_alarm` | `co_state` | SmokeCoAlarm `COState` | `carbon_monoxide` |
| `smoke_co_alarm` | `hardware_fault`, `end_of_service`, `device_muted`, `test_in_progress` | SmokeCoAlarm `HardwareFaultAlert`, `EndOfServiceAlert`, `DeviceMuted`, `TestInProgress` | `problem` for the first two |

Device types with a single binary sensor accept the short form (`contact_sensor: my_sensor`). See
[`examples/door-and-motion.yaml`](../examples/door-and-motion.yaml).

The `device_class` is read from the bound `binary_sensor` at build time. It selects the polarity of contact sensors and
the sensor type of occupancy sensors. If the class doesn't fit the Matter device type, a warning is printed during
`esphome config` and `esphome compile`. The build is never blocked by it.

## Contact sensors and polarity

The two sides use opposite conventions:

| | Door/window open | Door/window closed |
|---|---|---|
| ESPHome `binary_sensor` with `device_class: door`, `window`, `opening` or `garage_door` | ON | OFF |
| Matter Contact Sensor, BooleanState `StateValue` | `false` | `true` |

Matter defines `StateValue = true` as "contact made / closed" for the Contact Sensor device type. The Matter data model
XML shipped with connectedhomeip does not contain this semantic (the cluster only says the attribute is a `bool`), but
the reference applications do: `examples/contact-sensor-app/telink/src/AppTask.cpp` writes
`StateValue = ContactSensorMgr().IsContactClosed()`.

To make an open door appear open in Matter, `esphome-matter` therefore inverts the state:

- A `contact_sensor` bound to a binary_sensor whose `device_class` is `door`, `window`, `opening` or `garage_door` is
  inverted automatically.
- A `contact_sensor` bound to a binary_sensor without a device_class, or with another one, is **not** inverted:
  ON is reported as closed. A warning is printed when there is no device_class and no `inverted` option, because this is
  the usual source of a "backwards" sensor.
- `inverted: true | false` overrides the automatic behavior. It is the final decision: `inverted: true` always sends
  `!state`, `inverted: false` always sends `state`.

```yaml
matter:
  endpoints:
    1:
      contact_sensor: front_door       # device_class: door -> inverted automatically
    2:
      contact_sensor:
        boolean_state: reed_switch     # no device_class, ON = open
        inverted: true
    3:
      contact_sensor:
        boolean_state: magnet_switch   # no device_class, ON = closed (already Matter's convention)
```

`inverted` is accepted by every binary-sensor device type (leak, freeze, rain, occupancy and the alarm attributes too),
but it is only ever applied automatically to `contact_sensor`.

Notes:

- Matter sees the final state of the ESPHome binary_sensor, after pin inversion and filters. What the entity reports as
  ON is what counts. If you already flipped a sensor in ESPHome so that ON means closed, give it no opening
  device_class (or set `inverted: false`), otherwise it gets flipped back.
- Until the binary_sensor has published its first state, the Matter attribute keeps its default `false`, which a
  contact sensor reports as open.

## Occupancy sensors

The Occupancy Sensing cluster requires at least one sensor-type feature. If `with_features:` doesn't contain one, it is chosen
from the device_class:

| `device_class` | Feature enabled | OccupancySensorType (legacy attribute) |
|---|---|---|
| `motion` | `PassiveInfrared` | PIR |
| `presence` | `Radar` | PIR |
| `occupancy`, none, anything else | `Other` | PIR |

OccupancySensorType and OccupancySensorTypeBitmap are derived from the feature map by connectedhomeip. Only
`PassiveInfrared`, `Ultrasonic` and `PhysicalContact` can be expressed in those two (deprecated) attributes. Every other
feature reports type PIR with an empty bitmap.

`presence` maps to `Radar` because that is what mmWave presence sensors usually are. This is a judgement call, not
something the specification dictates. Set `with_features:` explicitly if your sensor is something else.

```yaml
matter:
  endpoints:
    1:
      occupancy_sensor:
        occupancy: desk_mat
        with_features: [PhysicalContact]    # overrides the device_class based choice
```

Valid sensor-type features: `Other`, `PassiveInfrared`, `Ultrasonic`, `PhysicalContact`, `ActiveInfrared`, `Radar`,
`RFSensing`, `Vision`. The `Prediction` and `OccupancyEvent` features are not available in esp-matter 1.6 and are
rejected.

### Hold time

`hold_time` adds the HoldTime and HoldTimeLimits attributes. Controllers (Home Assistant, Aqara/Apple Home style
"clear delay" settings) can then read and change how long the sensor stays occupied after the last motion.

```yaml
matter:
  endpoints:
    1:
      occupancy_sensor:
        occupancy: hallway_motion
        hold_time:
          default: 45s      # initial HoldTime (default 30s)
          min: 5s           # HoldTimeLimits.HoldTimeMin (default 1s)
          max: 10min        # HoldTimeLimits.HoldTimeMax (default 300s, or the default if that is larger)
          number: hold_number   # optional ESPHome number, see below
          legacy_attributes: true   # default
    2:
      occupancy_sensor:
        occupancy: office_presence
        hold_time: 20s      # short form: only the default
```

Design: **the hold is applied by the Matter cluster, not by an ESPHome filter.** The `binary_sensor` stays raw in
ESPHome. When it turns ON the Occupancy attribute becomes occupied immediately (and a pending hold is cancelled); when
it turns OFF the attribute stays occupied for HoldTime seconds and then becomes unoccupied. This matches what the
specification defines for HoldTime, and a controller that writes a new HoldTime takes effect without a reflash. Do not
add your own `delayed_off` to the same sensor unless you want both delays to add up.

* Limits: `1 <= min <= default <= max <= 65534` seconds, and `max` must be at least 10 s (a Matter constraint). The
  configuration is rejected otherwise. HoldTime is whole seconds.
* **Persistence and boot rule.** The SDK would lose a HoldTime above 300 s at every reboot (its storage check uses
  the fixed limits 1/300 s before esphome-matter can raise them), so esphome-matter persists HoldTime itself in the
  Matter key value store, next to the `min`/`max`/`default` it was stored for. The Matter factory reset erases that
  store, which also resets HoldTime. At boot the first of these wins:
  1. the persisted Matter value, if it was stored for the same `min`/`max`/`default` (a reboot keeps what a
     controller or the number wrote last),
  2. the state of the `number`, if there is no such record (first boot after flashing, a changed configuration, a
     factory reset) and the number has a state (for example restored),
  3. `default`.

  The value is clamped into the limits. Changing `min`, `max` or `default` therefore applies the new setup once.
* `number`: an ESPHome `number` that mirrors HoldTime in both directions. A write from Matter (HoldTime or a legacy
  alias) sets the number; a change of the number sets HoldTime, clamped into the limits, and the clamped value is shown
  in the number again. A stale value never wins: a number change that was captured before a controller write is dropped, and a controller write that arrives before a number push ran is applied to the number first. The Matter value wins at boot as
  above, so a restored number only matters when Matter has no value; use `restore_value: true` on it anyway so that it
  shows something at boot. The number's `min_value`/`max_value` should equal the limits: a narrower range is a warning
  (some values cannot be set from ESPHome) and a wider one too (values outside are clamped).
* `legacy_attributes` (default on): also creates the deprecated `PIROccupiedToUnoccupiedDelay`,
  `UltrasonicOccupiedToUnoccupiedDelay` or `PhysicalContactOccupiedToUnoccupiedDelay` attribute that matches the
  sensor-type feature. They are aliases of HoldTime that older controllers read. Sensors of type Radar, Other, ... have no
  such attribute. The `*UnoccupiedToOccupied*` delay and threshold attributes (a delay before reporting occupancy) are
  not supported.
* Automations: `on_attribute: occupancy_sensing.hold_time` triggers on writes from Matter. The deprecated aliases are
  accepted for writes and persisted like HoldTime.

## Water leak, freeze and rain

These device types use the same BooleanState cluster as the contact sensor, but with the natural polarity: `true` means
the condition is detected (leak, freeze, rain). Nothing is inverted by default.

```yaml
matter:
  endpoints:
    1:
      water_leak_detector: basement_leak     # device_class: moisture
    2:
      water_freeze_detector: pipe_temp       # device_class: cold
    3:
      rain_sensor: roof_rain                 # device_class: moisture
```

ESPHome's `cold` class means "temperature is below the threshold", which is what a freeze detector reports.

## Alarms and sensitivity (Boolean State Configuration)

`contact_sensor`, `water_leak_detector`, `water_freeze_detector`, `rain_sensor` and `occupancy_sensor` can carry the
optional Boolean State Configuration cluster. It lets a controller choose which alarms (visual, audible) the device
signals, suppress them and set the sensitivity of the sensor. Configure it with `alarms` and/or `sensitivity`:

```yaml
matter:
  endpoints:
    1:
      water_leak_detector:
        boolean_state: basement_leak
        alarms:
          visual: true          # the device has a visual alarm (e.g. an LED), AlarmsSupported bit
          audible: true         # and an audible one (buzzer)
          suppress: true        # AlarmSuppress feature: a controller may silence active alarms
          active: leak_alarm    # optional binary_sensor, default: the sensor of the device type
          on_suppressed:        # AlarmsSuppressed changed (needs suppress: true): silence the siren
            - switch.turn_off: buzzer
          on_active:            # AlarmsActive changed; visual and audible are booleans
            - if:
                condition:
                  lambda: return audible;
                then:
                  - switch.turn_on: buzzer
                else:
                  - switch.turn_off: buzzer
        sensitivity:
          levels: 3             # 2 to 10; level 0 is the lowest sensitivity
          default: 1            # initial CurrentSensitivityLevel (also DefaultSensitivityLevel)
          number: leak_sensitivity   # optional ESPHome number, 0 to levels-1
```

* `alarms` needs `visual` and/or `audible`. It creates AlarmsSupported, AlarmsActive, AlarmsEnabled and the
  `EnableDisableAlarm` command; `suppress` adds AlarmsSuppressed and `SuppressAlarm`. `alarms` also emits the
  `AlarmsStateChanged` event.
* **AlarmsEnabled starts empty** and is set by the controller with `EnableDisableAlarm` (persisted by the Matter stack).
  The binary_sensor turning ON makes every *enabled* alarm active (AlarmsActive), turning it OFF clears all alarms. So
  nothing becomes active until a controller enabled an alarm (the SDK offers no way to preset AlarmsEnabled). When a
  controller enables alarms while the sensor is already ON they become active right away. "ON" is the ESPHome state:
  leak, freeze or rain detected, contact open, occupied. Use `active:` to drive it from another binary_sensor.
* `on_suppressed` (`suppress: true`) runs with `visual`/`audible` when AlarmsSuppressed changes, that is when a
  controller sends `SuppressAlarm` (the cluster hands over the alarms named in the command; an empty mask means all
  suppressions were cleared). Use it to silence a siren that `on_active` started.
* `on_active` runs on the ESPHome main loop whenever AlarmsActive changes, including changes caused by controllers
  (they can disable an active alarm). Use it to drive the buzzer or LED of the device.
  `on_attribute` triggers on AlarmsActive treat the changes made by the alarm source itself as ESPHome's own writes and
  do not fire for them; `on_active` does.
* `sensitivity` creates CurrentSensitivityLevel (writable by controllers, persisted), SupportedSensitivityLevels and,
  with `default`, DefaultSensitivityLevel. With `number` the ESPHome number mirrors CurrentSensitivityLevel in both
  directions (the number wins at boot when it has a state; use `restore_value: true`). How a level changes the detection
  threshold is up to your automations: trigger on the number with `on_value`.
* The sensor-fault attributes (SensorFault, FaultEvents) are not supported.

`with_features: [Visual, ...]` alone is rejected, because the leak detector does not require the cluster: use the
`alarms`/`sensitivity` options, which create it. (`FaultEvents` and the SensorFault attribute are not available in
esp-matter.)

## Smoke and CO alarm

`smoke_co_alarm` maps up to two binary sensors to the SmokeState and COState attributes (AlarmStateEnum). Each sensor you
configure enables the matching feature (SmokeAlarm, COAlarm), so configure at least one.

| binary_sensor | SmokeState / COState | ExpressedState |
|---|---|---|
| OFF | Normal (0) | Normal, unless another alarm is active |
| ON | Critical (2) | SmokeAlarm / COAlarm |

ExpressedState is recomputed with the specification's priority order after every change. The Warning level (1) isn't
produced. The battery alert, interconnect, contamination, sensitivity and expiry attributes keep their defaults.
Because the device type has several binary sensors, the polarity overrides are `<option>_inverted` (`smoke_state_inverted`,
`co_state_inverted`, `hardware_fault_inverted`, ...).

```yaml
matter:
  endpoints:
    1:
      smoke_co_alarm:
        smoke_state: kitchen_smoke   # device_class: smoke
        co_state: kitchen_co         # device_class: carbon_monoxide
```

ESPHome `gas` and `vibration` have no Matter equivalent and aren't mapped.

### Faults, end of service, mute and self test

| Option | Matter | binary_sensor ON means |
|---|---|---|
| `hardware_fault` | HardwareFaultAlert (and the `HardwareFault` event) | the alarm has a hardware fault (ExpressedState HardwareFault) |
| `end_of_service` | EndOfServiceAlert (and the `EndOfService` event) | the alarm reached its end of life (Expired) |
| `device_muted` | DeviceMuted, `AlarmMuted`/`MuteEnded` events (optional attribute, created when bound) | the alarm is muted locally. The cluster refuses to mute while an alarm is critical and unmutes automatically on a new critical alarm |
| `test_in_progress` | TestInProgress (and the `SelfTestComplete` event when it ends) | a self test is running; OFF completes it |

The interplay: a self test does not clear a hardware fault, and the priority order is
Inoperative > Smoke > interconnect smoke > CO > interconnect CO > HardwareFault > Testing > EndOfService >
BatteryAlert. ExpressedState shows `HardwareFault` while `hardware_fault` is ON even during a test (the test
still runs and `SelfTestComplete` is generated), and `SelfTestRequest` is refused with `Busy` while ExpressedState is
a smoke or CO alarm, `Testing`, or an interconnect alarm (a hardware fault does not block it).

Mute limitation: Matter has no command to mute, the mute button is on the device. The cluster unmutes
by itself when an alarm turns critical (and when `device_muted` is bound, its binary_sensor stays ON). Turn your own
mute state OFF from the same triggers (smoke, CO, battery) or publish OFF when the alarm binary_sensors turn ON.
`device_muted` ON is refused while an alarm is critical (an error is logged).

ExpressedState follows from all of them (`Testing`, `HardwareFault`, `EndOfService`, ...). `hardware_fault` and
`end_of_service` expect a `problem` class binary_sensor (others only warn).

The `SelfTestRequest` command (Home Assistant has a button for it, the Apple Home app uses it) is always accepted unless
an alarm or another test is active (then the answer is `Busy`). The cluster then sets TestInProgress and ExpressedState
`Testing` and **ends the test only when the application says so**, so a test would stay active forever without help. esphome-matter
therefore supplies the missing end:

```yaml
matter:
  endpoints:
    1:
      smoke_co_alarm:
        smoke_state: kitchen_smoke
        self_test_duration: 8s      # default 5s: the test ends by itself after this time
        on_self_test:               # a self test was accepted: blink the LED, beep, ...
          - script.execute: beep
    2:
      smoke_co_alarm:
        smoke_state: hall_smoke
        test_in_progress: hall_testing   # the application ends the test (self_test_duration is not used)
        on_self_test:
          - binary_sensor.template.publish: {id: hall_testing, state: true}
          - script.execute: run_hardware_test   # ... then publish state: false when done
```

`on_self_test` runs on the main loop. With `test_in_progress` bound, publish ON at the start of the test (Matter already set
TestInProgress, so this keeps it consistent) and OFF at the end; OFF completes the test, generates `SelfTestComplete` and
recomputes ExpressedState. The binary_sensor only reports changes, so a sensor that never turned ON never reports OFF.
A smoke_co_alarm with a binding other than `smoke_state`/`co_state` but no alarm type needs `with_features: [SmokeAlarm]` or
`[COAlarm]`.

## Configuration warnings

| Situation | Warning |
|---|---|
| device_class doesn't fit the device type, e.g. a `motion` sensor in `contact_sensor` | lists the accepted classes and the device type that fits |
| `contact_sensor` without device_class and without `inverted` | ON is reported as closed |
| `contact_sensor` with an opening class and `inverted: false` | ON (open) would be reported as closed |
| `hold_time.number` / `sensitivity.number` limits don't fit the values Matter allows | names the number and the allowed range |
| select option of a `mode_select` without a mode (refrigerator) | the option is ignored by Matter |

A binary_sensor without a device_class never triggers a mismatch warning.
