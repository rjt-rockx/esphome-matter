The `door_lock` and `water_valve` device types can be bound to an ESPHome [lock](https://esphome.io/components/lock/) and [valve](https://esphome.io/components/valve/). Matter controllers can then lock, unlock and open/close the ESPHome entity, and the state of the entity is reported back to Matter.

## Door lock

```yaml
matter:
  endpoints:
    1:
      door_lock:
        lock_id: front_door
        # Optional, the Matter LockType attribute. Defaults to DEAD_BOLT.
        # lock_type: DEAD_BOLT

lock:
  - platform: output
    id: front_door
    name: Front door
    output: lock_relay
```

`lock_id` is required. Without a bound lock the Door Lock cluster has nothing to answer commands with.

### Commands

| Matter command | ESPHome |
|---|---|
| LockDoor | `lock.lock()` |
| UnlockDoor | `lock.unlock()` |
| UnboltDoor | `lock.open()` if the lock supports it (`supports_open`), otherwise `unlock()` |

Commands are accepted immediately (the Matter response only says "accepted") and executed on the ESPHome main loop. The resulting state is reported when the ESPHome lock publishes it. No PIN is required: PIN codes are ignored by default. The door lock server only validates a PIN when the `PIN Credential` and `User` features are added with `features:`, which is not needed for normal use.

A lock that is already in the requested state acknowledges the operation right away.

### State

| ESPHome lock state | Matter LockState |
|---|---|
| `LOCKED` | Locked |
| `UNLOCKED` | Unlocked |
| `OPEN` | Unlatched |
| `JAMMED` | NotFullyLocked (and DoorState DoorJammed if the `DoorPositionSensor` feature is enabled) |
| `NONE` | NotFullyLocked |
| `LOCKING`, `UNLOCKING`, `OPENING` | Not reported. Matter has no transitional states, so the previous state is kept until the lock settles |

LockOperation events are emitted with the source `Remote` for commands from a controller and `Manual` for everything else. `ActuatorEnabled` is always true.

To report jams to controllers, enable the door position sensor feature:

```yaml
      door_lock:
        lock_id: front_door
        features:
          - DoorPositionSensor
```

DoorState is then set to `DoorJammed` while the ESPHome lock is jammed and cleared afterwards. Other door states are not reported because ESPHome locks have no door sensor. Use a `matter.set_attribute` action to publish your own.

## Water valve

```yaml
matter:
  endpoints:
    1:
      water_valve:
        valve_id: garden_valve
        # Needed to control the position of a valve that supports it.
        features:
          - Level

valve:
  - platform: template
    id: garden_valve
    name: Garden valve
    has_position: true
    open_action:
      - switch.turn_on: valve_relay
    close_action:
      - switch.turn_off: valve_relay
```

`valve_id` is optional, but without it nothing handles the Open and Close commands.

### Commands

| Matter command | ESPHome |
|---|---|
| Open (no level) | `set_command_open()` |
| Open with TargetLevel (`Level` feature) | `set_position(level / 100)` if the valve supports position, otherwise open |
| Close | `set_command_close()` |

Matter has no stop command, so ESPHome's `stop` is not exposed. `OpenDuration` is handled by the Matter cluster itself, which sends a Close when the duration ends. `DefaultOpenDuration`, `DefaultOpenLevel` and `LevelStep` can be changed by controllers as usual.

### State

The Valve Configuration and Control cluster sets `CurrentState` to `Transitioning` when it receives Open or Close and never settles it itself. The state is therefore updated from the ESPHome valve:

| ESPHome valve | Matter |
|---|---|
| operation `OPENING` or `CLOSING` | CurrentState Transitioning |
| idle, position 0 | CurrentState Closed, CurrentLevel 0 |
| idle, position above 0 | CurrentState Open, CurrentLevel `round(position * 100)` |

CurrentLevel is only reported when the `Level` feature is enabled. A valve that is already at the requested position acknowledges the command right away, so the state never gets stuck at `Transitioning`.

ESPHome valves have no fault reporting, so `ValveFault` is never set.

## Ecosystem support

Controller support differs a lot between ecosystems and has not been tested by this project.

* **Apple Home** has no known support for the Water Valve device type (0x0042). Developers report that Apple Home refuses to add such accessories with a "not supported" error ([Apple Developer Forums thread 844622](https://developer.apple.com/forums/thread/844622)), while other controllers accept them. This is a single source and no statement from Apple has been found. If you rely on Apple Home, expose the valve as an `on_off_plug_in_unit` instead, which loses the valve semantics. Apple Home support for the Door Lock device type is unverified.
* **Google Home** lists the Door Lock and exposes a Valve Configuration and Control trait for its Water Valve type. Google's documentation notes that remote unlock is restricted for locks without a PIN, so unlocking from Google Home may be disabled with the default no-PIN setup.
* **Alexa**: the Door Lock is listed. The Water Valve is not listed.

## Notes

* The Door Lock server callbacks (`emberAfPluginDoorLockOn*Command` and `emberAfDoorLockClusterInitCallback`) are defined once by this component in `matter_lock.cpp` and are compiled only when a `door_lock` endpoint has a `lock_id`. Do not define them yourself in a custom component.
* The `Unbolt` and Aliro features of the Door Lock cluster are not supported by the config generator.
