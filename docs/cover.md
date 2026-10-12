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

Covers with `device_class` `garage`, `gate` or `door` are rejected at config time. These are not window coverings.
Matter models them with the Closure Control and Closure Dimension clusters, which were introduced in Matter 1.5 and
are not supported by this component yet.

## Implementation notes

Inbound commands reach the cover through the `chip::app::Clusters::WindowCovering::Delegate`, which the
component attaches to the cluster when the endpoint is created. connectedhomeip sets the
`TargetPositionLiftPercent100ths`/`TargetPositionTiltPercent100ths` attributes and then calls `HandleMovement`, which
reads the target and defers to the ESPHome main loop. `HandleStopMotion` stops the cover the same way. Clients cannot
write the target attributes, so the delegate is the only inbound path. Outbound state uses
`esp_matter::attribute::report`, so reporting does not trigger attribute callbacks.

The window covering delegate approach and the idea of combining lift and tilt into one call were inspired by
[mrflo97/esphome-matter](https://github.com/mrflo97/esphome-matter).
