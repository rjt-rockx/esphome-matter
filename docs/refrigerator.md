# Refrigerator

The `refrigerator` and `temperature_controlled_cabinet` device types can be created under an endpoint (see
[device-types.md](device-types.md#untested--unsupported)). Two optional clusters can be bound to ESPHome entities:

* the **Refrigerator And Temperature Controlled Cabinet Mode** cluster, a list of modes such as "Rapid cool" bound to
  a `select`. It is one of the delegate layer clusters, configured under `clusters:` exactly like the mode clusters of
  the other appliances, see [delegates.md](delegates.md#mode-clusters);
* the **Refrigerator Alarm** cluster (door open), bound to a `binary_sensor` with `door_open` on the `refrigerator`.

In the Matter specification both clusters are **optional** for the refrigerator and the cabinet. A refrigerator without
them is conformant; they are what controllers show as the fridge's mode and door alarm. The temperature control of
the cabinet (Temperature Control cluster) is not bound yet.

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
        door_open: fridge_door
      clusters:
        refrigerator_and_temperature_controlled_cabinet_mode:
          modes:
            - label: Normal
              tags: [auto]
            - label: Rapid cool
              tags: [rapid_cool]
            - label: Vacation
              tags: [vacation, low_energy]
              option: Away              # the select option for this mode (default: the label)
          select: fridge_mode
```

## Modes

The mode cluster is described in [delegates.md](delegates.md#mode-clusters) (mode list, tags, `select:` two-way binding,
the boot rule, `on_change_to_mode`). The tags specific to this cluster are `rapid_cool` and `rapid_freeze`, next to
the common ones (`auto`, `quick`, `quiet`, `low_noise`, `low_energy`, `vacation`, `min`, `max`, `night`, `day`). The
cluster has no `OnMode`, `StartUpMode` or OnOff dependency (the specification disallows them). It is also available on
`temperature_controlled_cabinet` endpoints (one per compartment of a fridge-freezer).

## Door alarm

`door_open` is a binary_sensor (ON = door open) that drives the `DoorOpen` bit (bit 0) of the Refrigerator Alarm
`State`. The cluster is created with `Mask` and `Supported` set to `DoorOpen` (the specification does not allow
`ModifyEnabledAlarms` or `Reset` for this cluster). Every change updates `State` and generates the `Notify` event with
`Active` (the alarm that became active), `Inactive` (the alarm that cleared), `State` and `Mask`; the update is done
through the SDK's `RefrigeratorAlarmServer`. A door that is open at boot raises the alarm once the sensor has a state.
Delay the alarm like a real fridge ("open for more than a minute") on the ESPHome side, for example with
`delayed_on: 60s` on the binary_sensor; Matter reports exactly what the sensor reports.

## Not supported

* The Temperature Control cluster of the cabinet (setpoint, levels) has no binding and the mode cluster does not
  change it. The Temperature Measurement cluster of a cabinet is available through the `temperature_sensor` device type
  on the same endpoint (the checker tool then reports an additional device type).
* The mode cluster with the OnOff feature, `StartUpMode` and `OnMode` (not allowed by the specification).
