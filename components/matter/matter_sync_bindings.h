#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "matter_binding.h"
#include "matter_push_bindings.h"

#include <array>
#include <cstdint>

// Reusable framework bindings for two-way "entity <-> small Matter setting"
// sync: modes, selects and numbers that mirror an enum or integer attribute,
// and the alarm source of the Boolean State Configuration cluster. Like
// matter_push_bindings.h this file has no entity or CHIP cluster dependencies,
// so the state machines are host-tested (tests/cpp/test_sync_bindings.cpp).
namespace esphome::matter {

// ---------------------------------------------------------------------------
// MatterSettingBinding: one integer setting.
//
// ESPHome -> Matter: the entity (select, number) changed, the binding calls
// request_push() and write_value_() runs on the CHIP task inside a PushScope.
// Matter -> ESPHome: a controller write (or a delegate callback) calls
// matter_changed_(); command_value_() applies it to the entity on the main
// loop. A push that was captured before the controller write is dropped by the
// stale-push gate, the reconcile after the apply pushes the entity's state
// (which now includes the write, or the entity's refusal of it).
// ---------------------------------------------------------------------------
struct SettingValue {
  bool valid{false};
  int32_t value{0};
};

class MatterSettingBinding : public MatterBinding<SettingValue, int32_t> {
public:
  explicit MatterSettingBinding(
      uint16_t endpoint_id,
      BindingPolicy policy = BindingPolicy{BootSync::ESPHOME_WINS, 0, true})
      : MatterBinding<SettingValue, int32_t>(endpoint_id, policy) {}

protected:
  void setup_() override {}

  // Main loop. False while the entity has no (valid) state.
  virtual bool entity_value_(int32_t &out) = 0;
  // Main loop. Apply a value that Matter holds to the entity. Must tolerate
  // the entity already being at the value.
  virtual void command_value_(int32_t value) = 0;
  // CHIP, inside the PushScope.
  virtual void write_value_(int32_t value) = 0;

  // CHIP: Matter holds a new value (controller write, scene recall, delegate).
  void matter_changed_(int32_t value) { this->submit_inbound(value); }

  SettingValue capture_() final {
    SettingValue s;
    s.valid = this->entity_value_(s.value);
    return s;
  }
  void write_matter_(const SettingValue &s) final {
    if (s.valid)
      this->write_value_(s.value);
  }
  void apply_inbound_(const int32_t &value) final {
    this->command_value_(value);
  }
};

// ---------------------------------------------------------------------------
// MatterModeBinding: CurrentMode of a Mode Base derived cluster or Mode Select.
//
// Unlike a plain setting, every accepted mode change is an event for the user
// automation (`on_change_to_mode`), so inbound changes queue up (the newest
// MAX_PENDING are kept) instead of collapsing to the last one. The main loop
// keeps `last_mode_`, the mode it believes Matter holds; that is what is pushed.
//
//   * entity (select, matter.mode.set) changed: last_mode_ = mode, request_push()
//   * Matter changed (ChangeToMode, scene recall): matter_mode_changed_()
//   * boot: the entity wins when it has a state, otherwise the stored
//     CurrentMode is shown in the entity without running the automation.
// ---------------------------------------------------------------------------
struct ModeChange {
  uint8_t mode{0};
  // True for the boot replay: show the mode, do not run the automation.
  bool silent{false};
};

struct ModeChanges {
  static constexpr size_t MAX_PENDING = 4;
  std::array<ModeChange, MAX_PENDING> items{};
  uint8_t count{0};
};

class MatterModeBinding : public MatterBinding<int16_t, ModeChanges> {
public:
  explicit MatterModeBinding(uint16_t endpoint_id)
      : MatterBinding<int16_t, ModeChanges>(
            endpoint_id,
            // The mode is pushed by explicit request_push(). No reconcile after
            // a ChangeToMode (the SDK has written the mode itself), only after
            // a push that the stale gate skipped.
            BindingPolicy{BootSync::ESPHOME_WINS, 0, false}),
        boot_slot_(&MatterModeBinding::boot_trampoline_,
                   reinterpret_cast<intptr_t>(this)) {}

  // Main loop. The mode the main loop believes Matter holds (-1 unknown).
  int last_mode() const { return this->last_mode_; }

protected:
  void setup_() override {}

  // ---- hooks
  // Main loop. Show `mode` in the entity (select) and, unless `silent`, run
  // the user automation. last_mode_ is already set.
  virtual void deliver_(uint8_t mode, bool silent) = 0;
  // Main loop. The mode the entity shows, -1 if it has no state.
  virtual int entity_mode_() = 0;
  // CHIP, inside the PushScope. Write CurrentMode.
  virtual void write_mode_(uint8_t mode) = 0;
  // CHIP. Read the stored CurrentMode, false if unavailable.
  virtual bool read_matter_mode_(uint8_t &mode) = 0;
  // Inbound changes equal to last_mode_ are dropped (Mode Select: the select
  // is already there, so there is no command to run).
  virtual bool drop_unchanged_inbound_() const { return false; }

  // ---- for subclasses
  // CHIP. Matter holds `mode`: a controller command or a scene recall.
  void matter_mode_changed_(uint8_t mode) {
    ModeChanges in;
    in.items[0] = ModeChange{mode, false};
    in.count = 1;
    this->submit_inbound(in);
  }
  // Main loop. The entity selected `mode`.
  void entity_mode_changed_(uint8_t mode) {
    if (static_cast<int>(mode) == this->last_mode_)
      return;
    this->last_mode_ = mode;
    this->request_push();
  }
  // Main loop. `mode` was set by an automation (matter.mode.set) or accepted
  // outside the inbound slot (the blocking decide_in_automation path): remember
  // it and refresh the outbound slot, so a snapshot captured earlier cannot
  // overwrite it.
  void set_mode_(uint8_t mode) {
    this->last_mode_ = mode;
    this->request_push();
  }
  // Main loop, from setup_(): boot sync. Deferred one loop iteration so a
  // restored entity state is read safely.
  void start_boot_sync_() {
    binding_platform()->defer_main([this]() {
      int mode = this->entity_mode_();
      if (mode >= 0) {
        this->last_mode_ = mode;
        this->request_push();
      } else {
        this->boot_slot_.request();
      }
    });
  }

  int16_t capture_() final { return static_cast<int16_t>(this->last_mode_); }
  void write_matter_(const int16_t &mode) final {
    if (mode >= 0)
      this->write_mode_(static_cast<uint8_t>(mode));
  }
  void merge_inbound_(ModeChanges &acc, const ModeChanges &in) final {
    for (uint8_t i = 0; i < in.count; i++) {
      if (acc.count == ModeChanges::MAX_PENDING) {
        for (size_t k = 1; k < ModeChanges::MAX_PENDING; k++)
          acc.items[k - 1] = acc.items[k];
        acc.count--;
      }
      acc.items[acc.count++] = in.items[i];
    }
  }
  void apply_inbound_(const ModeChanges &in) final {
    for (uint8_t i = 0; i < in.count; i++) {
      const ModeChange &c = in.items[i];
      if (this->drop_unchanged_inbound_() &&
          static_cast<int>(c.mode) == this->last_mode_)
        continue;
      this->last_mode_ = c.mode;
      this->deliver_(c.mode, c.silent);
    }
  }

  int last_mode_{-1};

private:
  static void boot_trampoline_(intptr_t ctx) {
    auto *self = reinterpret_cast<MatterModeBinding *>(ctx);
    self->boot_slot_.begin_run();
    uint8_t mode;
    if (self->read_matter_mode_(mode)) {
      ModeChanges in;
      in.items[0] = ModeChange{mode, true};
      in.count = 1;
      self->submit_inbound(in);
    }
  }

  ChipWorkSlot boot_slot_;
};

// ---------------------------------------------------------------------------
// MatterAlarmSourceBinding: a boolean source (binary_sensor) that drives a
// code-driven alarm state in Matter (Boolean State Configuration: ON makes every
// enabled alarm active, OFF clears them).
//
// The state is idempotent, so bursts coalesce to the latest value. A Matter
// side event that invalidates the applied state without the source changing
// (EnableDisableAlarm while the source is ON) calls matter_state_changed_():
// the apply is a no-op on the main loop and the reconcile re-pushes the source
// state, after the command that caused it has completed.
// ---------------------------------------------------------------------------
struct AlarmSourceState {
  bool valid{false};
  bool on{false};
};

class MatterAlarmSourceBinding
    : public MatterBinding<AlarmSourceState, bool> {
public:
  explicit MatterAlarmSourceBinding(uint16_t endpoint_id)
      : MatterBinding<AlarmSourceState, bool>(
            endpoint_id, BindingPolicy{BootSync::ESPHOME_WINS, 0, true}) {}

protected:
  void setup_() override {}

  // Main loop. False while the source has no state.
  virtual bool source_state_(bool &on) = 0;
  // CHIP, inside the PushScope. ON: SetAllEnabledAlarmsActive, OFF: ClearAllAlarms.
  virtual void apply_source_(bool on) = 0;

  // CHIP. The enabled alarms changed: re-apply the source state if it is ON.
  void matter_state_changed_() {
    if (this->source_on_.load())
      this->submit_inbound(true);
  }

  AlarmSourceState capture_() final {
    AlarmSourceState s;
    s.valid = this->source_state_(s.on);
    this->source_on_.store(s.valid && s.on);
    return s;
  }
  void write_matter_(const AlarmSourceState &s) final {
    if (s.valid)
      this->apply_source_(s.on);
  }
  void apply_inbound_(const bool &) final {}

private:
  // Mirror of the source for the CHIP task (written by capture_()).
  std::atomic<bool> source_on_{false};
};

} // namespace esphome::matter

#endif // USE_MATTER
