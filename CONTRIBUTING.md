# Contributing

## Layout

```
components/matter/
  __init__.py        # top-level matter: schema, sdkconfig, partition injection
  const.py           # device-type tables, conf keys, ns
  matter.h/cpp       # MatterComponent (node create + esp_matter::start)
  matter_entity.h    # MatterEntity base
  switch/            # platform: matter under switch:
    __init__.py
    matter_switch.h/cpp
  partitions.csv     # 4MB layout with separate nvs_chip
examples/            # YAML examples, one per device type
scripts/             # mfg-tool wrapper + factory flasher
```

Each entity type lives in its own subdirectory under `components/matter/`.
That mirrors how ESPHome ships per-platform code (`sensor/`, `binary_sensor/`,
...) and keeps each device-type's Python + C++ glue together.

## Adding a device type

1. Create `components/matter/<domain>/__init__.py` extending the base
   ESPHome platform schema (e.g. `switch_.switch_schema(...)`).
2. Add the codegen call to `parent.register_entity(var)` so the
   `MatterComponent` picks it up at setup.
3. Implement `MatterEntity::register_with_node` (create the esp-matter
   endpoint) and `on_matter_attribute_update` (handle inbound writes).
4. Add an example YAML under `examples/`.
5. Add the device-type ID to `const.DEVICE_TYPE_NAMES`.

## Style

- Match ESPHome's own component conventions: lowercase keys, `cv.Schema`,
  `cv.GenerateID`, `cg.new_Pvariable`.
- Keep Matter SDK headers out of public headers when possible; include them
  only in `.cpp` files to keep compile times sane.
- All cross-task entity writes from Matter to ESPHome state must defer via
  `chip::DeviceLayer::SystemLayer().ScheduleWork(...)`; never call
  `publish_state` directly from a CHIP callback.
- ESPHome to Matter writes go inside a `::esp_matter::lock::chip_stack_lock`
  scope.

## Prior art

This component is closely modeled on
[`esphome/components/zigbee/`](https://github.com/esphome/esphome/tree/dev/esphome/components/zigbee)
(luar123, PR #11553). When in doubt, read that first.
