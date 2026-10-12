# Threading model

Two execution contexts share this component. Mixing them up causes races that
only show up on hardware.

| Context | What runs there | May touch |
|---|---|---|
| ESPHome main loop | `setup()`, `loop()`, automations, entity callbacks, `defer()`ed work | ESPHome entities, `MatterComponent` members |
| CHIP task | Matter stack, attribute `POST_UPDATE` callback (`endpoint_attribute_update_cb`), anything run by `PlatformMgr().ScheduleWork` / `SystemLayer().ScheduleLambda` | CHIP/esp-matter state (attribute store, binding table, fabric table) |

The CHIP event loop holds the CHIP stack lock while it runs scheduled work and
callbacks, so code on the CHIP task can use CHIP APIs without locking again.

## Rules

1. **Matter to ESPHome**: never touch an ESPHome entity from the CHIP task. Copy
   the values you need and call `global_matter_component->defer_to_main_loop(...)`
   (or `defer_to_main_loop(parent, ...)` from `matter_attributes.h`).
2. **ESPHome to Matter**: never call esp-matter/CHIP APIs from the main loop.
   - Small captures (at most `CHIP_CONFIG_LAMBDA_EVENT_SIZE`, 24 bytes by default):
     `chip::DeviceLayer::SystemLayer().ScheduleLambda(...)`, then
     `esp_matter::attribute::report` (not `update`, to avoid echo loops).
   - Larger payloads: heap-allocate a request and use
     `chip::DeviceLayer::PlatformMgr().ScheduleWork(fn, intptr_t)`; the work
     function owns and frees it (see `set_attribute_value`,
     `send_client_command`). Bound the number of in-flight requests.
3. **Never block the main loop on the CHIP stack lock.** No
   `ScopedChipStackLock(portMAX_DELAY)` or `LockChipStack()` in `loop()` or
   automations; a busy CHIP task would stall ESPHome and trip the watchdog.
   Schedule onto the CHIP task instead. (`dump_config` takes the lock once at
   boot and is the only accepted exception.)
4. **Shared containers need a lock.** `MatterComponent::attribute_callbacks_` is
   appended to from the main loop (`register_attribute_callback`, normally from
   a mapping's `initialize()`, which runs after `esp_matter::start()`) and read
   by the CHIP task. Both sides take a mutex; the dispatcher copies matching
   callbacks and invokes them outside the lock. `register_attribute_callback`
   is therefore safe to call at any time.
5. **Containers filled only before `esp_matter::start()`** (`mappings_`,
   `attribute_triggers_`, endpoint registrations) are effectively frozen
   afterwards. Do not add to them after `setup()` returns.
6. **Attribute callbacks run on the CHIP task**: keep them short, copy the value,
   defer. Do not call ESPHome APIs or block.
7. State used by both contexts must be an atomic or mutex-guarded value, or be
   passed by value through the hand-off.

## Audit (current state)

- `send_client_command`: the whole send path (binding table walk and
  `client::cluster_update`) runs on the CHIP task via `ScheduleWork`; the main
  loop only enqueues a copy of the request (max 16 pending, extras dropped with a
  warning). Previously the table was walked on the main loop without the lock
  and the lock was taken with `portMAX_DELAY`.
- `set_attribute_value`, sensor attribute updates, light state push: main loop
  to CHIP via `ScheduleWork`/`ScheduleLambda`. OK.
- `MatterAttributeTrigger::dispatch`: CHIP to main loop via `defer`, with a
  mutex-guarded coalesced pending value. The echo guard
  (`MatterAttributeDispatchGuard`) is only set and read on the main loop. OK.
- `MatterLightMapping::color_update_pending_`: only touched on the CHIP task. OK.
- `MatterLightMapping::synchronizing_from_matter_`: documented TODO race in
  `matter_lights.cpp` (not changed here).
- `MatterComponent::dump_config` and `factory_reset` (`matter_component.cpp`):
  `dump_config` blocks on `LockChipStack()` once; `factory_reset` erases NVS on
  the main loop without the chip lock. Both are outside this change.

## Prior art

Serialising outgoing binding commands towards the CHIP side is informed by the
`feature/binding-command-queue` branch of jamesweakley/esphome-matter, which adds
per-endpoint command queues with pacing and superseding. That pacing layer is
not adopted here; it can be built on top of the `send_client_command` hand-off.
