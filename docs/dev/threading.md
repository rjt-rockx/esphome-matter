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
   - Small captures (at most `CHIP_CONFIG_LAMBDA_EVENT_SIZE`, 24 bytes by default;
     larger lambdas fail to compile with "lambda too large"):
     `chip::DeviceLayer::SystemLayer().ScheduleLambda(...)`, then
     `esp_matter::attribute::report` (not `update`, to avoid echo loops).
   - Larger captures: `run_on_matter_thread(std::function<void()>)`
     (`matter_attributes.h`), which heap-allocates the closure and uses
     `PlatformMgr().ScheduleWork`. It runs in order with `ScheduleLambda` work
     (same CHIP event queue).
   - Payloads handed over as a raw pointer: heap-allocate a request and use
     `ScheduleWork(fn, intptr_t)`; the work function owns and frees it (see
     `set_attribute_value`, `send_client_command`). Bound the number of in-flight
     requests where the producer can be fast.
   - **Pointers stored by esp-matter outlive the work item.**
     `client::cluster_update()` shallow-copies the request handle and keeps
     `request_data` until the bound node's session is up (CASE setup can take
     seconds), so `send_client_command` must be given a pointer that stays valid
     for the firmware lifetime and does not copy it into a request that is freed
     when the work item returns.
3. **Never block the main loop on the CHIP stack lock.** No
   `ScopedChipStackLock(portMAX_DELAY)` or `LockChipStack()` in `loop()` or
   automations; a busy CHIP task would stall ESPHome and trip the watchdog.
   Schedule onto the CHIP task instead. (`setup()` and `dump_config` take the lock once at
   boot; that is the only accepted exception.)
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

8. **The CHIP task does not wait for the main loop by default.** Delegate commands
   (`ChangeToMode`, Operational State `Start`/`Stop`/`Pause`/`Resume`/`GoHome`, see
   [../delegates.md](../delegates.md)) are decided on the CHIP task from state the
   main loop keeps in atomics (`current_state_`) and from constant lists, answered at
   once, and the automations are posted with `defer`. Only with the explicit
   `decide_in_automation: true` does `run_on_main_loop_and_wait`
   (`matter_delegates.h`) block the CHIP task on a condition variable, for at most
   `decide_timeout` (default `MATTER_DELEGATE_TIMEOUT_MS` = 250 ms). The request
   state is shared through a `shared_ptr`; a request the CHIP task gave up on is
   dropped if it did not start, and if it was already running the `late` callback
   (main loop) reconciles it (mode: push the result to Matter when not rejected).
   The active outcome pointer (`active_command_outcome`) is only set and read on the
   main loop. While the CHIP task waits, everything else on it waits too, and
   main-loop code that waits for the CHIP stack lock (`dump_config`, `setup`) can
   make the wait time out; it never deadlocks because the wait is bounded.
9. **Actions never take the CHIP lock on the main loop.** `matter.send_event` is
   scheduled with `PlatformMgr().ScheduleWork` (the event is logged on the CHIP task,
   which already holds the lock) like `send_client_command` and
   `set_attribute_value`. Remaining one-time lock users (`setup`, `dump_config`,
   commissioning window and OTA setup, text sensor refresh) are listed in the audit.

## Audit (current state)

- `send_client_command`: the whole send path (binding table walk and
  `client::cluster_update`) runs on the CHIP task via `ScheduleWork`; the main
  loop only enqueues a small request (max 16 pending, extras dropped with a
  warning). The command data is passed by pointer, not copied (see rule 2). The
  CHIP event loop holds the stack lock while it runs the work, so no extra
  locking is needed. Previously the table was walked on the main loop without the
  lock and the lock was taken with `portMAX_DELAY`.
- `set_attribute_value`, sensor attribute updates, light state push: main loop
  to CHIP via `ScheduleWork`/`ScheduleLambda`. OK.
- `MatterAttributeTrigger::dispatch`: CHIP to main loop via `defer`, with a
  mutex-guarded coalesced pending value. The echo guard
  (`MatterAttributeDispatchGuard`) is only set and read on the main loop. OK.
- `MatterLightMapping::color_update_pending_`: only touched on the CHIP task. OK.
- `synchronizing_from_matter_` (light): only set and read on the main loop,
  so there is no data race. The residual issue is logical: an entity that
  publishes its state after `perform()` returned (asynchronously) is seen as a
  change that originated on the ESPHome side and is pushed back to Matter.
- Fan, climate fan, climate, filter monitoring: binding framework (see
  `binding-framework.md`); one CHIP work item per binding.
- Cover, lock, valve, fan, light, switch, climate: Matter to ESPHome through
  `defer_to_main_loop` with values copied on the CHIP task; ESPHome to Matter
  through `ScheduleLambda` / `run_on_matter_thread` with values copied on the
  main loop. Valve `current_level_` is atomic (read by the CHIP-task delegate).
  Mappings call `esp_matter::attribute::get` / cluster `Get*` from `initialize()`
  on the main loop once, after `esp_matter::start()`; this only reads
  structures that are not modified afterwards.
- Delegate layer (`matter_modes.cpp`, `matter_operational_state.cpp`,
  `matter_laundry_controls.cpp`): delegate callbacks read lists (`modes_`,
  `states_`, `phases_`, ...) that are filled before `esp_matter::start()` and never
  change; the countdown value and the current operational state are atomics; the mode, state and phase pushes use
  `run_on_matter_thread` and the SDK `Instance` is only touched there. Echo loops
  between a bound select and a Matter attribute are cut with `last_*_` values that
  are only used on the main loop (attribute callbacks defer first). See rule 8 for
  the blocking command path.
- Commissioning: `identity_` and the ephemeral passcode belong to the CHIP task
  (`handle_event`, `open_window`'s lambda); `state_` and the callback vectors
  belong to the main loop and are filled before `esp_matter::start()`.
- Matter OTA (`matter_ota.cpp`): the OTA state event handler runs on the CHIP
  task and defers to the main loop; the requestor driver is configured under
  the CHIP stack lock once at boot.
- `MatterComponent::dump_config` and `factory_reset` (`matter_component.cpp`):
  `dump_config` blocks on `LockChipStack()` once; `factory_reset` erases NVS on
  the main loop without the chip lock. Both are outside this change.

## Prior art

Serialising outgoing binding commands towards the CHIP side is informed by the
`feature/binding-command-queue` branch of jamesweakley/esphome-matter, which adds
per-endpoint command queues with pacing and superseding. That pacing layer is
not adopted here; it can be built on top of the `send_client_command` hand-off.
