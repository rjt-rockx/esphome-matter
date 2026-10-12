# Covers

The `window_covering` device type can expose an existing ESPHome [cover](https://esphome.io/components/cover/) to
Matter with the `cover_id` option. The cover keeps doing the actual motor control; Matter commands are forwarded to it
and its state is reported back.

```yaml
matter:
  endpoints:
    1:
      window_covering:
        cover_id: living_room_shade
        # end_product_type: roller_shade

cover:
  - platform: time_based
    id: living_room_shade
    name: Living Room Shade
    device_class: shade
    # ...
```

See [blinds.yaml](../examples/blinds.yaml) for a complete example.

### Configuration variables

- **cover_id** (*Optional*, ID): The ESPHome cover to expose. Without it the endpoint is a plain Window Covering
  endpoint that has to be driven with `on_attribute` triggers and `matter.set_attribute`.
- **end_product_type** (*Optional*, string): Describes the physical product to controllers, for example
  `roller_shade`, `interior_venetian_blind`, `exterior_venetian_blind`, `central_curtain` or `roller_shutter`. See
  the Matter `EndProductType` enum for all values. The default is derived from the cover's `device_class`.

- **closure_compat** (*Optional*, boolean, default `false`): Acknowledges that a `garage`, `gate` or `door` cover is exposed
  as a Window Covering instead of a native closure, see [below](#compatibility-mode-window-covering-default-works-today).
  Only valid for those device classes.

The `with_features` option must not be used together with `cover_id`. The features are selected from the cover.

## What gets exposed

| ESPHome cover | Matter |
|---|---|
| always | Lift and PositionAwareLift features, `UpOrOpen`, `DownOrClose`, `StopMotion`, `GoToLiftPercentage` |
| `supports_tilt` | Tilt and PositionAwareTilt features, `GoToTiltPercentage` |
| `position` | `CurrentPositionLiftPercent100ths` |
| `tilt` | `CurrentPositionTiltPercent100ths` |
| `current_operation` | `OperationalStatus` (stopped, opening, closing) |
| `device_class` | `Type` and `EndProductType` |

Device classes map as follows:

| `device_class` | Type | EndProductType |
|---|---|---|
| none, `shade`, `window`, `damper` | RollerShade | RollerShade |
| `blind` | RollerShade | InteriorBlind |
| `curtain` | Drapery | CentralCurtain |
| `awning` | Awning | AwningTerracePatio |
| `shutter` | Shutter | RollerShutter |
| none, `shade`, `window`, `damper`, `blind`, with tilt | TiltBlindLiftAndTilt | InteriorVenetianBlind |

### Positions are inverted

ESPHome uses `1.0` for fully open and `0.0` for fully closed. Matter uses `Percent100ths` where `0` is fully open
and `10000` is fully closed. Both lift and tilt are converted automatically.

ESPHome has no target position. When the cover is idle, the Matter target equals the current position. While it
moves, the target is the position requested through Matter, or the end of the direction of travel when the
movement was started from ESPHome.

### Covers without position support

The Matter implementation in connectedhomeip only calls the application for position aware covers. Covers
that only support open, close and stop are therefore exposed as position aware as well. They report only fully
open (`0`) and fully closed (`10000`), and `GoToLiftPercentage` values are rounded to the closest end.
A cover without a stop action reports `StopMotion` as not implemented.

### Lift and tilt

`UpOrOpen` and `DownOrClose` request both lift and tilt from the cover in the same loop iteration. Both targets are
combined in a single ESPHome cover call.

## Garage doors, gates and doors

Covers with `device_class` `garage`, `gate` or `door` are Matter *closures*. Matter 1.5 added the Closure
(0x0230), Closure Panel (0x0231) and Closure Controller device types with the Closure Control (0x0104) and Closure
Dimension (0x0105) clusters. esp-matter 1.6 ships them, but no controller (Apple Home, Google Home, Alexa, SmartThings,
Home Assistant) documents support for them yet, and Apple appears to reject an accessory with an unknown device type.
Shipping garage openers (Konnected, Meross, ...) therefore advertise Window Covering.

So there are two paths. The default one is the compatibility mode.

### Compatibility mode: Window Covering (default, works today)

Bind the cover to a `window_covering` endpoint as for any other cover. A garage-class cover is accepted and a config
time note explains the choice; `closure_compat: true` acknowledges it and silences the note.

```yaml
matter:
  endpoints:
    1:
      window_covering:
        cover_id: garage_door
        closure_compat: true      # only valid for garage, gate and door covers
        # end_product_type: roller_shutter

cover:
  - platform: template   # or ratgdo, ...
    id: garage_door
    name: Garage Door
    device_class: garage
    # ...
```

Controllers show a shutter or blind. `device_class` selects Type Shutter and EndProductType: `garage` RollerShutter,
`gate` SlidingShutter, `door` SwingingShutter (override with `end_product_type`). Open, close, stop and
position behave as described above. Take care with voice control: "open the shutter" opens the garage door.

### Native Closure (experimental, opt-in)

```yaml
matter:
  endpoints:
    1:
      closure:
        cover_id: garage_door
```

This creates a Closure device type with the Closure Control cluster (Positioning feature) driven by the cover:

| ESPHome cover | Closure Control |
|---|---|
| `current_operation` opening or closing | `MainState` Moving, otherwise Stopped; `MovementCompleted` event at the end |
| `position` | `OverallCurrentState.Position` FullyClosed, FullyOpened, PartiallyOpened (null while unknown) |
| direction of travel | `OverallTargetState.Position` MoveToFullyOpen / MoveToFullyClosed while moving, otherwise null |
| `MoveTo` command | cover open or close (other target positions are rejected with ConstraintError) |
| `Stop` command | cover stop (Success, but a no-op, when the cover has no stop action) |

`cover_id` is required: without a cover there is no delegate and the Closure Control cluster would not exist. A cover can
be bound to one `window_covering` or `closure` endpoint only. `Stop` on a cover without a stop action returns
Success and does nothing; the cluster state is then re-synchronised from the cover, which keeps reporting Moving until
it stops by itself. `MovementCompleted` is only emitted when a movement ends at its target end, not after an
interrupted stop or a `MoveTo` toward the end the cover is already at. FullyClosed and FullyOpened are only reported
when the cover is idle at an end (within 1%); while moving the position is PartiallyOpened.

Limitations: only the Positioning feature is served (no latching, speed, ventilation, pedestrian, calibration), so
`with_features` may only contain `Positioning`. `SecureState` is reported as unknown. No Closure Panel child endpoint
(Closure Dimension) is created, so the Closure has no panel; the `closure_panel` device type can be added manually but is
not bound to the cover. Which controllers accept a Closure without a panel is unverified. Setting `device_class` of the
bound cover to a window covering class is an error. Because of the missing controller support this device type is
experimental and logs a warning at config time.

Absolute position (the `ABS` feature of Window Covering) is not available: esp-matter 1.6 has no feature or attributes
(PhysicalClosedLimit, CurrentPosition in steps) for it.

## Implementation notes

Inbound commands reach the cover through the `chip::app::Clusters::WindowCovering::Delegate`, which the
component attaches to the cluster when the endpoint is created. connectedhomeip sets the
`TargetPositionLiftPercent100ths`/`TargetPositionTiltPercent100ths` attributes and then calls `HandleMovement`, which
reads the target and defers to the ESPHome main loop. `HandleStopMotion` stops the cover the same way. Clients cannot
write the target attributes, so the delegate is the only inbound path. Outbound state uses
`esp_matter::attribute::report`, so reporting does not trigger attribute callbacks.

The window covering delegate approach and the idea of combining lift and tilt into one call were inspired by
[mrflo97/esphome-matter](https://github.com/mrflo97/esphome-matter).
