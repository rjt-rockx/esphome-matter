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

The Occupancy Sensing cluster requires at least one sensor-type feature. If `features:` doesn't contain one, it is chosen
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
something the specification dictates. Set `features:` explicitly if your sensor is something else.

```yaml
matter:
  endpoints:
    1:
      occupancy_sensor:
        occupancy: desk_mat
        features: [PhysicalContact]    # overrides the device_class based choice
```

Valid sensor-type features: `Other`, `PassiveInfrared`, `Ultrasonic`, `PhysicalContact`, `ActiveInfrared`, `Radar`,
`RFSensing`, `Vision`. Non-sensor-type features (`Prediction`, `OccupancyEvent`) can be listed next to them.

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

## Smoke and CO alarm

`smoke_co_alarm` maps up to two binary sensors to the SmokeState and COState attributes (AlarmStateEnum). Each sensor you
configure enables the matching feature (SmokeAlarm, COAlarm), so configure at least one.

| binary_sensor | SmokeState / COState | ExpressedState |
|---|---|---|
| OFF | Normal (0) | Normal, unless another alarm is active |
| ON | Critical (2) | SmokeAlarm / COAlarm |

ExpressedState is recomputed with the specification's priority order after every change. The Warning level (1) isn't
produced. The cluster's other attributes and commands (battery alert, self test, ...) keep their defaults. Because the
device type has two binary sensors, the polarity overrides are `smoke_state_inverted` and `co_state_inverted`.

```yaml
matter:
  endpoints:
    1:
      smoke_co_alarm:
        smoke_state: kitchen_smoke   # device_class: smoke
        co_state: kitchen_co         # device_class: carbon_monoxide
```

ESPHome `gas` and `vibration` have no Matter equivalent and aren't mapped.

## Configuration warnings

| Situation | Warning |
|---|---|
| device_class doesn't fit the device type, e.g. a `motion` sensor in `contact_sensor` | lists the accepted classes and the device type that fits |
| `contact_sensor` without device_class and without `inverted` | ON is reported as closed |
| `contact_sensor` with an opening class and `inverted: false` | ON (open) would be reported as closed |

A binary_sensor without a device_class never triggers a mismatch warning.
