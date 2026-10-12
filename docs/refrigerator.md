# Refrigerator

The `refrigerator` and `temperature_controlled_cabinet` device types can be created under an endpoint (see
[device-types.md](device-types.md#untested--unsupported)). This page covers the two clusters that can be bound to
ESPHome entities: the **Refrigerator And Temperature Controlled Cabinet Mode** cluster (a list of modes such as
"Rapid cool", bound to a `select`) and the **Refrigerator Alarm** cluster (door open, bound to a `binary_sensor`). The
temperature control of the cabinet (Temperature Control cluster) is not bound yet, see [Not supported](#not-supported).

In the Matter specification both clusters are **optional** for the refrigerator and the cabinet. A refrigerator without
them is conformant; they are what controllers show as the fridge's mode and door alarm.

```yaml
select:
  - platform: template
    id: fridge_mode
    options: [Normal, Rapid cool, Away]
    optimistic: true
    initial_option: Normal

binary_sensor:
  - platform: gpio
    id: fridge_door
    pin: GPIO4
    device_class: door

matter:
  endpoints:
    1:
      refrigerator:
        modes:
          - label: Normal
            tags: [auto]
          - label: Rapid cool
            tags: [rapid_cool]
          - label: Vacation
            tags: [vacation, low_energy]
            option: Away              # the select option for this mode (default: the label)
        mode_select: fridge_mode
        door_open: fridge_door
```

## Modes

`modes` creates the mode cluster (`ChangeToMode` and its response, `SupportedModes`, `CurrentMode`). The cluster has no
`OnMode` or `StartUpMode` and no OnOff dependency (the specification disallows them for this cluster).

* `label` (1 to 64 characters), `mode` (0 to 254, default: the position in the list) and `tags` are the
  `ModeOptionStruct` of the specification. Each mode needs at least one and at most 8 tags. Tags: `auto`, `quick`,
  `quiet`, `low_noise`, `low_energy`, `vacation`, `min`, `max`, `night`, `day`, `rapid_cool`, `rapid_freeze` (the
  last two are specific to this cluster) or a number for manufacturer specific tags. Controllers use the tags, not the
  labels, to recognise a mode.
* At least two modes. Mode values and labels must be unique. The first mode is the initial `CurrentMode`.
* `mode_select` is the ESPHome `select` that stands for the mode. Every mode needs an option of the select with the
  same text as the label, or the one named with `option:`. Missing options are an error, extra options are a warning (a
  controller cannot select them).
  * Matter to ESPHome: a `ChangeToMode` command from a controller is always accepted and selects the option of that
    mode in the select (`select.set` semantics, so the select's `set_action` runs and can drive your hardware).
  * ESPHome to Matter: a change of the select (a button, a dashboard, an automation) sets `CurrentMode`.
  * At boot the state of the select (for example restored) wins; a select without a state shows the mode that Matter
    persisted.
  * Without `mode_select` the cluster is just a list: controllers can change `CurrentMode` and nothing else happens
    (use `on_attribute: refrigerator_and_temperature_controlled_cabinet_mode.current_mode` to react).
* The cluster is also available on `temperature_controlled_cabinet` endpoints (one per compartment of a fridge-freezer).
  Listing the cluster under `clusters:` without `modes` is rejected, because a mode cluster without modes is not valid.

## Door alarm

`door_open` is a binary_sensor (ON = door open) that drives the `DoorOpen` bit (bit 0) of the Refrigerator Alarm
`State`. Every change updates `State` and generates the `Notify` event with `Active` (the alarm that became active),
`Inactive` (the alarm that cleared), `State` and `Mask`. `Mask` and `Supported` are fixed to `DoorOpen` (the specification
does not allow `ModifyEnabledAlarms` or `Reset` for this cluster). A door that is open at boot raises the alarm once
the sensor has a state. Delay the alarm like a real fridge ("open for more than a minute") on the ESPHome side, for
example with `delayed_on: 60s` on the binary_sensor; Matter reports exactly what the sensor reports.

## Not supported

* The Temperature Control cluster of the cabinet (setpoint, levels) has no binding and the mode cluster does not
  change it. The Temperature Measurement cluster of a cabinet is available through the `temperature_sensor` device type
  on the same endpoint (the checker tool then reports an additional device type).
* `Refrigerator And Temperature Controlled Cabinet Mode` with the OnOff feature, `StartUpMode` and `OnMode` (not allowed by
  the specification).
* Dynamic mode lists: the list is fixed when the firmware is built.
