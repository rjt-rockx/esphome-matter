# Binding framework

`components/matter/matter_binding.h` / `.cpp` is the shared core for entity
bindings. It replaces five hand-rolled echo mechanisms and the per-change heap
allocations in the push paths. Migrated so far: switch, valve, sensors, binary
sensors, the power-source flag enums, fan, climate fan, climate, filter monitoring, lock, cover, closure, modes, Mode Select, operational state,
laundry controls and the sensor-extras delegates; see [Migration order](#migration-order). Thread names follow
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

## Fan, climate fan, climate, filter monitoring

Pure decision code lives in `matter_fan_logic.h` and `matter_climate_logic.h`
(no ESPHome or Matter dependencies, host-tested by
`tests/cpp/test_hvac_bindings.cpp`); the `.cpp` files are thin glue.

- **Fan** (`MatterFanMapping`, `MatterBinding<MatterFanOutbound, MatterFanInbound>`,
  `NONE` boot sync plus a one-time submit of the persisted cluster state, 200 ms settle). FanMode / PercentSetting / SpeedSetting /
  RockSetting / AirflowDirection watches and the Step command read the settled
  cluster state on CHIP and submit it; WindSetting changes (delegate) submit a
  wind request. The merge keeps the newest settings snapshot and the newest wind
  request. The old `FanSnapshot`/`last_` compare is replaced by the stale-push
  gate, and `update_settings` by `fan_settings_need_write()`: the cluster
  settings are written only when the cluster no longer maps to the fan (refused
  or clamped request, ESPHome-side change). A cluster in Auto stays in Auto until
  the fan changes (`keep_auto`). The Current attributes, WindSetting, RockSetting
  and AirflowDirection always follow the fan. `OnWindSettingChanged` starts with
  `is_echo()`; `HandleStep` submits the settled state instead of calling the
  mapping directly.
- **Climate fan** (`ESPHOME_WINS`, 300 ms settle). `OnFanDriveStateChanged` and
  `OnRockSettingChanged` start with `is_echo()` and submit; the two fields merge
  independently. The `pushed_/last_*` dedupe is gone (the push coalesces and only
  writes values that differ from the cluster). The Step level table is built at
  setup so CHIP does not read the climate.
- **Climate** (`ESPHOME_WINS`, 500 ms settle). SystemMode, the two occupied
  setpoints and OnOff are watched; a burst merges per field and is applied in the
  fixed order mode, on/off, heating, cooling (so a setpoint of the same burst is
  judged against the new mode). A refused or clamped request is no longer
  followed by an explicit `push_state_to_matter()`: the reconcile after the apply
  restores Matter to the climate's state. The attribute reports go through
  `BindingPlatform::write_attribute()` inside the PushScope.
- **Filter monitoring** (`MatterBinding<MatterFilterState, NoInbound>`): sensor
  callbacks call `request_push()`; Condition/ChangeIndication are written by the
  cluster setters on CHIP. `PostResetCondition` still defers the button/trigger
  to the main loop.

Settle windows exist because these entities may publish later than they accept a
command: without it the reconcile would push the old state back first and the
controller would see the value flip.
Lock, cover and closure each split into a header-only state machine
(`matter_lock_binding.h`, `matter_cover_binding.h`, `matter_closure_binding.h`;
no entity or cluster headers, host-tested in `tests/cpp/test_entity_bindings.cpp`)
and the existing `Matter*Mapping` class, which only supplies the entity hooks and
the cluster writes.

- Lock: `BootSync::ESPHOME_WINS`, `reconcile_after_apply = false`. Only settled
  states are published; transitional ones keep the previous Matter state. The
  state carries a publish `seq` so reconcile pushes and skipped pushes resend the
  newest publish without a second `SetLockState`; the remote origin and the boot
  DoorState fix-up of a replaced publish are inherited by the one that replaces it.
- Cover: `settle_ms = 200`. A burst of Matter requests merges into one cover call
  (lift and tilt together; a stop discards earlier movement and runs before later
  movement). A stop pushes at once. If the cover never publishes after a command,
  the window expiry pushes the unchanged state (target back to the current
  position). Targets are forgotten when the cover reports idle.
- Closure: no settle. `MovementCompleted` is counted on the main loop
  (`completed_count`) and emitted on CHIP when the count moves, so coalesced
  snapshots cannot lose or duplicate it. A missing cluster at startup is retried
  every 500 ms (20 times) from `write_matter_()`.
### Sync bindings

`matter_sync_bindings.h` (host-tested in `tests/cpp/test_sync_bindings.cpp`)
holds the two-way bindings built for step 7:

- `MatterSettingBinding`: one integer setting between an entity (select, number)
  and Matter. Hooks: `entity_value_()` (main), `command_value_()` (main),
  `write_value_()` (CHIP, in the scope); `matter_changed_()` (CHIP) submits a
  controller write. Used for the laundry selects (SpinSpeedCurrent,
  NumberOfRinses), the HoldTime number and the Boolean State Configuration
  sensitivity number. A push captured before a controller write is skipped and
  the reconcile after the apply carries the converged state.
- `MatterModeBinding`: CurrentMode of Mode Base derived clusters and Mode Select.
  The main loop keeps `last_mode_` (what Matter holds); the select and
  `matter.mode.set` push it. Controller changes queue (newest 4 kept) because
  every accepted change runs `on_change_to_mode`; `drop_unchanged_inbound_()`
  lets Mode Select skip a change the select already shows. Boot: the select
  wins when it has a state, otherwise the stored CurrentMode is shown without
  running the automation. The old `pending_echoes_` sequence queue is replaced
  by `PushScope`/`is_echo()`.
- `MatterAlarmSourceBinding`: a boolean source that drives a code-driven alarm
  state. `matter_state_changed_()` (CHIP) re-pushes the source state after the
  command that invalidated it (EnableDisableAlarm while the source is ON).

Other step 7 users: operational state is a `MatterBinding<snapshot, NoInbound>`
(state, phase and countdown coalesce) plus a `MatterEventQueue` for
OperationalError and OperationCompletion; the Refrigerator Alarm door
transitions go through a `MatterEventQueue<bool>` so each one raises its own
Notify event; the Smoke CO self test counts requests as inbound (`merge` adds)
and completes through an idempotent state push; the air quality level is a
`MatterAttributePushBinding`. The delegate command decisions (`accept`,
`allowed_states`, `decide_in_automation` and its blocking bridge) are unchanged.

`DeviceBindingPlatform::write_attribute()` adopts the stored attribute type when
only nullability differs, so nullable attributes can be written with a plain
value.

## Migration order

Each step is one change; codegen snapshots stay unchanged.

1. Core and host tests (done).
2. Switch (done).
3. Sensors, binary sensors, valve, power-source flag enums (done; removes the
   `new AttributeUpdate` per reading).
4. Lock, cover, closure (done; `request_push_`/`push_done_`/`schedule_drain_` are gone).
5. Fan and climate fan (done; `pushing_` is gone, wind and step hooks use `is_echo()`).
6. Climate (done), filter monitoring (done).
7. Modes, operational state, Mode Select (`pending_echoes_` dropped), laundry
   controls and the sensor-extras delegates (done).
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
