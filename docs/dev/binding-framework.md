# Binding framework

`components/matter/matter_binding.h` / `.cpp` is the shared core for entity
bindings. It replaces five hand-rolled echo mechanisms and the per-change heap
allocations in the push paths. Migrated so far: switch, valve, sensors, binary
sensors and the power-source flag enums; see [Migration order](#migration-order). Thread names follow
[threading.md](threading.md): "main" is the ESPHome main loop, "CHIP" is the CHIP
task.

## Contract

A binding derives from `MatterBinding<State, Inbound>` and implements:

| Method | Thread | Purpose |
|---|---|---|
| `setup_()` | main, after `esp_matter::start()` | register entity callbacks (which call `request_push()`), `watch_attribute()` |
| `State capture_()` | main | read the entity |
| `write_matter_(const State&)` | CHIP, inside a `PushScope` | write Matter (store or code-driven setters) |
| `merge_inbound_(Inbound& acc, const Inbound& in)` | CHIP | combine controller writes before the apply (default: last wins) |
| `apply_inbound_(const Inbound&)` | main | command the entity |
| `on_attribute_(cluster, attr, value)` | CHIP | called for non-echo store updates of watched attributes; usually ends in `submit_inbound()` |

Call `request_push()` (main) whenever the entity state changes. Call
`submit_inbound(in)` (CHIP) when a controller write or delegate command arrives.

```cpp
struct BindingPolicy {
  BootSync boot{BootSync::ESPHOME_WINS};  // MATTER_WINS | ESPHOME_WINS | NONE
  uint16_t settle_ms{0};
  bool reconcile_after_apply{true};
};
```

`MatterEventQueue<Event, N>` is for discrete events that must not be coalesced
(switch presses, OperationCompletion): `post(ep, ev)` on main, the sink runs on
CHIP.

### Guarantees

- **One CHIP work item per binding.** `request_push()` overwrites the outbound
  slot and schedules only if none is outstanding (`ChipWorkSlot`). The CHIP side
  clears the flag before copying the slot, so a change racing with the copy
  schedules once more. A burst yields one or two writes and the last value
  always lands. No heap allocation per change.
- **ScheduleWork failure does not drop.** The flag stays set and the main loop
  retries every 50 ms; later `request_push()` calls coalesce into the retry.
  A warning is logged on the first failure and every 20th.
- **No stale overwrite.** `inbound_seq_` is bumped on CHIP when a controller
  write is queued; `applied_seq_` is set on main after `apply_inbound_()`. A push
  that runs while they differ is skipped (the snapshot predates the write) and
  remembered; the apply then always reconciles, even with
  `reconcile_after_apply = false`.
- **Reconcile.** After each apply the binding calls `request_push()` so an entity
  that refused or clamped the command ends with Matter equal to ESPHome.
- **Settle.** With `settle_ms > 0` the reconcile waits: the entity's own
  `request_push()` (it published) ends the wait and is the reconcile; otherwise
  the push happens when the window expires. Each apply starts a new window; a
  stale timer is ignored. Use it where the entity publishes later than it accepts
  (fan, cover, light transitions).
- **Boot sync.** `ESPHOME_WINS` pushes at `initialize()`. `MATTER_WINS` reads
  every `watch_attribute()`d attribute from the store on CHIP and feeds it to
  `on_attribute_()`. `NONE` does nothing. Mode Select's "select wins, else
  CurrentMode" is `ESPHOME_WINS` plus a `has_state()` check in `capture_()`.
- **Event queue.** Bounded (default 8), drop-oldest with a warning, one CHIP work
  item per drain, retry on ScheduleWork failure.

### Echo suppression

`MatterBindingBase::PushScope(ep)` (CHIP) marks endpoint `ep` as being written
by ESPHome and arms the legacy `MatterPushGuard`. `is_echo(ep)` is true while any
scope on `ep` is alive. It is consulted by:

- `endpoint_attribute_update_cb` (`matter_attributes.cpp`): the POST_UPDATE of a
  PushScope write is dropped. This is the only change to an existing file, and it
  is a no-op when no binding uses the framework.
- `watch_attribute()` callbacks.
- Code-driven delegate hooks, which should start with
  `if (MatterBindingBase::is_echo(endpoint_id())) return;` instead of a
  `pushing_` flag.

Echo is per endpoint: a write on endpoint X that triggers a callback on endpoint
Y (scenes, groups) is delivered. The table holds 8 endpoints; on overflow every
endpoint counts as echo, the safe direction.

### Platform seam

`BindingPlatform` hides everything outside the state machines: `schedule_chip`
(`PlatformMgr().ScheduleWork`, no allocation), `defer_main`, `main_after`,
`now_ms`, the legacy guard, attribute watches, store reads and
`write_attribute()` (`attribute::report` / `attribute::update`, CHIP thread,
inside a `PushScope`; the base implementation fails so older fakes still compile). The device build
installs `DeviceBindingPlatform` lazily; host tests install `FakePlatform`
(`tests/cpp/fake_platform.h`) and drive the CHIP queue, the main queue and the
clock by hand. `ChipWorkSlot::request()` and `main_after` are main-loop only.

### Thread asserts

`matter_thread.h` provides `MATTER_ASSERT_CHIP_THREAD()` and
`MATTER_ASSERT_MAIN_LOOP()`, active only with `USE_MATTER_THREAD_ASSERTS`
(planned: on in QEMU and test configs). A violation logs the call site and
aborts. The CHIP check is `IsChipStackLockedByCurrentThread()`; the main-loop
task is captured by `thread::capture_main_loop()`, called from
`MatterBindingBase::initialize()`. Only `matter_binding.cpp` uses them so far.

## Tests

`tests/cpp/` is built with the host `g++` by `tests/unit/test_cpp_host.py`
(skipped when there is no compiler; `-std=gnu++20` and `c++17`, `-Werror`).
Stubs for esp-matter and ESPHome headers are in `tests/cpp/stubs/`; keep them
minimal. Scenarios in `test_binding.cpp`: burst, inbound during pending push,
refused command, settle (publish / expiry / restart), per-endpoint echo, nested
scopes and overflow, ScheduleWork failure, boot sync, event queue overflow and
retry, thread asserts. Run one by hand:

```bash
g++ -std=gnu++20 -DMATTER_HOST_TEST -pthread -Itests/cpp/stubs -Itests/cpp \
  -Icomponents/matter tests/cpp/*.cpp components/matter/matter_{binding,thread,conversions}.cpp \
  -o /tmp/matter-host-tests && /tmp/matter-host-tests [name-filter]
```

## Reusable bindings

`matter_push_bindings.h` has no entity or cluster dependencies, so the state
machines are host-tested (`tests/cpp/test_push_bindings.cpp`):

- `MatterAttributePushBinding`: ESPHome -> Matter mirror of one attribute value
  (`ValueState{valid, value}`). Sensors, binary sensors and `MatterFlagEnumMapping`
  derive from it; code-driven clusters override `write_attribute_()` (the old
  `SensorAttributeUpdater`). The newest reading wins; there is no allocation per
  reading and no dropped final value. `valid == false` (no state, or a converter
  that rejects the reading) writes nothing.
- `MatterOnOffBinding`: OnOff.OnOff with `MATTER_WINS` boot replay, `report()` pushes
  and reconcile after every applied write (switch).

Valve uses `MatterBinding<MatterValveState, MatterValveCommand>` with a 200 ms
settle window: a valve that is already at the target never publishes, so the
window expiry pushes the unchanged state and `CurrentState` leaves
`Transitioning`. The electrical power/energy sensors use
`MatterBinding<ElectricalValue, NoInbound>`; the concentration cluster is
created on the CHIP task (queued ahead of the boot push) instead of under the
CHIP lock on the main loop.

## Migration order

Each step is one change; codegen snapshots stay unchanged.

1. Core and host tests (done).
2. Switch (done).
3. Sensors, binary sensors, valve, power-source flag enums (done; removes the
   `new AttributeUpdate` per reading).
4. Lock, cover, closure (`request_push_`/`push_done_`/`schedule_drain_` go away).
5. Fan and climate fan (delete `pushing_`; wind and step hooks use `is_echo()`).
6. Climate.
7. Modes, operational state, Mode Select (drop `pending_echoes_`), sensor-extras
   delegates.
8. Lights, last, behind the runtime golden test.
9. Retire `MatterPushGuard` as a public API and `MatterAttributeDispatchGuard`;
   `test_attribute_writes.py` then checks "writes only inside PushScope".

### Migration notes

- A binding is a `MatterEndpointMappingBase`; `register_*` and generated code do
  not change. Construct it with `BindingPolicy` and keep the old class name.
- Call `request_push()` from the entity callback instead of scheduling work.
  Do not capture on the CHIP task.
- Never read an entity in `write_matter_`; put what you need in `State`.
- If an entity state is not representable in one `State` value, split into
  several bindings on the same endpoint (each gets its own slot and work item).
- `MatterEndpointMappingBase::initialize()` is `final` in the binding base; put
  setup in `setup_()`.
