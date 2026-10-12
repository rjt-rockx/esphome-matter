// Host tests for the two-way sync bindings (components/matter/matter_sync_bindings.h): modes (Mode Select
// and Mode Base derived clusters), integer settings (HoldTime number, laundry selects, sensitivity) and the
// Boolean State Configuration alarm source.
#include "fake_platform.h"
#include "matter_sync_bindings.h"
#include "test_framework.h"

using namespace esphome::matter;
using tp::FakePlatform;
using tp::HostRole;
using tp::host_set_role;

namespace {

constexpr uint16_t EP = 4;
constexpr uint32_t MODE_SELECT = 0x0050;
constexpr uint32_t CURRENT_MODE = 0x0003;
constexpr uint32_t OS = 0x0406;
constexpr uint32_t HOLD_TIME = 0x0003;

struct Rig {
  FakePlatform p;
  Rig() {
    set_binding_platform(&p);
    host_set_role(HostRole::MAIN);
  }
  ~Rig() {
    set_binding_platform(nullptr);
    host_set_role(HostRole::ANY);
  }
};

esp_matter_attr_val_t u8_val(uint8_t v) {
  esp_matter_attr_val_t val;
  val.type = ESP_MATTER_VAL_TYPE_UINT8;
  val.val.u8 = v;
  return val;
}

esp_matter_attr_val_t u16_val(uint16_t v) {
  esp_matter_attr_val_t val;
  val.type = ESP_MATTER_VAL_TYPE_UINT16;
  val.val.u16 = v;
  return val;
}

// ---------------------------------------------------------------------------
// A Mode Select like binding: CurrentMode lives in the attribute store, the SDK writes it when a controller
// changes it, the select is the entity.
// ---------------------------------------------------------------------------
class TestModeSelect : public MatterModeBinding {
 public:
  explicit TestModeSelect(uint16_t ep) : MatterModeBinding(ep) {}

  // The bound select: -1 = no state.
  int select_mode{-1};
  std::vector<int> automation;  // modes the on_change_to_mode automation ran for
  std::vector<int> select_sets;
  FakePlatform *platform{nullptr};

  // ESPHome side: the user (or a lambda) picked an option of the select.
  void user_selects(int mode) {
    select_mode = mode;
    entity_mode_changed_(static_cast<uint8_t>(mode));
  }
  // matter.mode.set
  void action_set(int mode) {
    select_mode = mode;
    set_mode_(static_cast<uint8_t>(mode));
  }
  // A controller changes CurrentMode (ChangeToMode handled by the SDK, or a scene recall): store + callbacks.
  void controller_sets(int mode) {
    host_set_role(HostRole::CHIP);
    platform->store[{endpoint_id(), MODE_SELECT, CURRENT_MODE}] = u8_val(mode);
    platform->fire_attribute(endpoint_id(), MODE_SELECT, CURRENT_MODE, u8_val(mode));
    host_set_role(HostRole::MAIN);
  }
  int stored(FakePlatform &p) { return p.store[{endpoint_id(), MODE_SELECT, CURRENT_MODE}].val.u8; }

 protected:
  void setup_() override {
    watch_attribute(MODE_SELECT, CURRENT_MODE);
    start_boot_sync_();
  }
  void on_attribute_(uint32_t, uint32_t, const esp_matter_attr_val_t &v) override { matter_mode_changed_(v.val.u8); }
  void deliver_(uint8_t mode, bool silent) override {
    if (!silent)
      automation.push_back(mode);
    select_mode = mode;
    select_sets.push_back(mode);
    // The select's own state callback: last_mode_ already equals mode, so nothing is pushed.
    entity_mode_changed_(mode);
  }
  int entity_mode_() override { return select_mode; }
  void write_mode_(uint8_t mode) override {
    esp_matter_attr_val_t v = u8_val(mode);
    binding_platform()->write_attribute(endpoint_id(), MODE_SELECT, CURRENT_MODE, v, false);
  }
  bool read_matter_mode_(uint8_t &mode) override {
    esp_matter_attr_val_t v;
    if (!binding_platform()->read_attribute(endpoint_id(), MODE_SELECT, CURRENT_MODE, v))
      return false;
    mode = v.val.u8;
    return true;
  }
  bool drop_unchanged_inbound_() const override { return true; }
};

// A Mode Base derived cluster: the delegate gets ChangeToMode and the SDK then sets CurrentMode itself;
// ESPHome changes go through the Instance (write_mode_).
class TestModeBase : public MatterModeBinding {
 public:
  explicit TestModeBase(uint16_t ep) : MatterModeBinding(ep) {}

  int select_mode{-1};
  int instance_mode{0};  // CurrentMode as the SDK instance holds it
  std::vector<int> automation;
  std::vector<int> instance_writes;

  void user_selects(int mode) {
    select_mode = mode;
    entity_mode_changed_(static_cast<uint8_t>(mode));
  }
  void action_set(int mode) {
    select_mode = mode;
    set_mode_(static_cast<uint8_t>(mode));
  }
  // ChangeToMode, default accept path: the delegate (CHIP) submits, the SDK then sets CurrentMode.
  void change_to_mode(int mode) {
    host_set_role(HostRole::CHIP);
    matter_mode_changed_(static_cast<uint8_t>(mode));
    instance_mode = mode;
    host_set_role(HostRole::MAIN);
  }

 protected:
  void setup_() override { start_boot_sync_(); }
  void deliver_(uint8_t mode, bool silent) override {
    if (!silent)
      automation.push_back(mode);
    select_mode = mode;
  }
  int entity_mode_() override { return select_mode; }
  void write_mode_(uint8_t mode) override {
    instance_writes.push_back(mode);
    instance_mode = mode;
  }
  bool read_matter_mode_(uint8_t &mode) override {
    mode = static_cast<uint8_t>(instance_mode);
    return true;
  }
};

// ---------------------------------------------------------------------------
// A number bound to HoldTime (hold time of the occupancy cluster).
// ---------------------------------------------------------------------------
class TestHoldTime : public MatterSettingBinding {
 public:
  explicit TestHoldTime(uint16_t ep) : MatterSettingBinding(ep) {}

  bool has_state{false};
  int number{0};
  std::vector<int> persisted;
  FakePlatform *platform{nullptr};

  // The number changed (state callback).
  void number_changed(int v) {
    has_state = true;
    number = v;
    request_push();
  }
  void controller_writes(int seconds) {
    host_set_role(HostRole::CHIP);
    platform->store[{endpoint_id(), OS, HOLD_TIME}] = u16_val(seconds);
    platform->fire_attribute(endpoint_id(), OS, HOLD_TIME, u16_val(seconds));
    host_set_role(HostRole::MAIN);
  }
  int stored() { return platform->store[{endpoint_id(), OS, HOLD_TIME}].val.u16; }

 protected:
  void setup_() override { watch_attribute(OS, HOLD_TIME); }
  void on_attribute_(uint32_t, uint32_t, const esp_matter_attr_val_t &v) override {
    persisted.push_back(v.val.u16);  // save_()
    matter_changed_(v.val.u16);
  }
  bool entity_value_(int32_t &out) override {
    if (!has_state)
      return false;
    out = number;
    return true;
  }
  void command_value_(int32_t v) override {
    if (has_state && number == v)
      return;
    number = v;
    has_state = true;
    request_push();  // the number publishes
  }
  void write_value_(int32_t v) override {
    esp_matter_attr_val_t val = u16_val(static_cast<uint16_t>(v));
    binding_platform()->write_attribute(endpoint_id(), OS, HOLD_TIME, val, false);
    persisted.push_back(v);
  }
};

// ---------------------------------------------------------------------------
// Boolean State Configuration alarm source.
// ---------------------------------------------------------------------------
class TestAlarmSource : public MatterAlarmSourceBinding {
 public:
  explicit TestAlarmSource(uint16_t ep) : MatterAlarmSourceBinding(ep) {}

  bool has_state{false};
  bool on{false};
  uint8_t enabled{0};
  uint8_t active{0};
  std::vector<int> applied;

  void source_changed(bool v) {
    has_state = true;
    on = v;
    request_push();
  }
  // EnableDisableAlarm: AlarmsEnabled is updated, then the delegate is called (inside the command).
  void enable_alarms(uint8_t mask) {
    host_set_role(HostRole::CHIP);
    enabled = mask;
    active &= mask;
    matter_state_changed_();
    host_set_role(HostRole::MAIN);
  }

 protected:
  bool source_state_(bool &state) override {
    if (!has_state)
      return false;
    state = on;
    return true;
  }
  void apply_source_(bool v) override {
    applied.push_back(v);
    active = v ? enabled : 0;
  }
};

}  // namespace

// ---------------------------------------------------------------------------
// Modes: Mode Select
// ---------------------------------------------------------------------------

TEST(mode_select_own_pushes_are_never_echoed_back) {
  Rig r;
  TestModeSelect m(EP);
  m.platform = &r.p;
  m.select_mode = 0;
  m.initialize();
  r.p.settle();
  r.p.advance(1);
  r.p.settle();
  m.user_selects(1);
  m.user_selects(2);
  m.user_selects(3);
  CHECK_EQ(r.p.chip_queue.size(), (size_t) 1);
  r.p.settle();
  // One write with the last mode, issued with attribute::update() (callbacks fire) inside the PushScope.
  int writes_of_3 = 0;
  for (auto &w : r.p.attribute_writes) {
    CHECK(w.echo);
    CHECK(!w.report);
    writes_of_3 += w.value.val.u8 == 3;
  }
  CHECK_EQ(writes_of_3, 1);
  CHECK_EQ(m.stored(r.p), 3);
  // The POST_UPDATE of our own writes did not reach the select or the automation.
  CHECK(m.automation.empty());
  CHECK(m.select_sets.empty());
  CHECK_EQ(m.last_mode(), 3);
  CHECK_EQ(r.p.legacy_guard_depth, 0);
}

TEST(mode_select_stale_push_does_not_clobber_controller_mode) {
  Rig r;
  TestModeSelect m(EP);
  m.platform = &r.p;
  m.select_mode = 0;
  m.initialize();
  r.p.settle();
  r.p.advance(1);
  r.p.settle();
  size_t before = r.p.attribute_writes.size();

  m.user_selects(2);          // push of 2 queued
  m.controller_sets(5);       // a controller (or scene recall) sets 5 before the push ran
  r.p.run_chip();             // the stale push must be skipped
  CHECK_EQ(r.p.attribute_writes.size(), before);
  CHECK_EQ(m.stored(r.p), 5);
  r.p.settle();
  // The controller's mode reaches the select and the automation exactly once ...
  CHECK_EQ(m.automation, std::vector<int>{5});
  CHECK_EQ(m.select_mode, 5);
  CHECK_EQ(m.last_mode(), 5);
  // ... and Matter ends up where ESPHome is.
  CHECK_EQ(m.stored(r.p), 5);
}

TEST(mode_select_controller_change_runs_automation_once_and_dedupes) {
  Rig r;
  TestModeSelect m(EP);
  m.platform = &r.p;
  m.select_mode = 1;
  m.initialize();
  r.p.settle();
  r.p.advance(1);
  r.p.settle();
  m.controller_sets(4);
  r.p.settle();
  CHECK_EQ(m.automation, std::vector<int>{4});
  CHECK_EQ(m.select_mode, 4);
  // The select was changed by the user to the same mode before the callback arrived: no automation.
  m.user_selects(6);
  r.p.settle();
  m.controller_sets(6);
  r.p.settle();
  CHECK_EQ(m.automation, std::vector<int>{4});
}

TEST(mode_select_boot_select_wins_else_matter_is_shown) {
  {  // The select has a restored state: it is pushed to Matter.
    Rig r;
    TestModeSelect m(EP);
    m.platform = &r.p;
    m.select_mode = 2;
    r.p.store[{EP, MODE_SELECT, CURRENT_MODE}] = u8_val(7);
    m.initialize();
    r.p.settle();
    r.p.advance(1);
    r.p.settle();
    CHECK_EQ(m.stored(r.p), 2);
    CHECK(m.automation.empty());
  }
  {  // No select state: the stored CurrentMode is shown in the select, without the automation.
    Rig r;
    TestModeSelect m(EP);
    m.platform = &r.p;
    r.p.store[{EP, MODE_SELECT, CURRENT_MODE}] = u8_val(7);
    m.initialize();
    r.p.settle();
    CHECK_EQ(m.select_mode, 7);
    CHECK(m.automation.empty());
    CHECK_EQ(m.last_mode(), 7);
    CHECK_EQ(m.stored(r.p), 7);
  }
}

// ---------------------------------------------------------------------------
// Modes: Mode Base derived cluster (ChangeToMode)
// ---------------------------------------------------------------------------

TEST(mode_base_change_to_mode_races_select_publish) {
  Rig r;
  TestModeBase m(EP);
  m.select_mode = 0;
  m.initialize();
  r.p.settle();
  r.p.advance(1);
  r.p.settle();
  m.instance_writes.clear();

  // The select moves to 3 and its push is captured; a controller's ChangeToMode(2) is accepted before the
  // push runs (the SDK then sets CurrentMode = 2).
  m.user_selects(3);
  m.change_to_mode(2);
  r.p.run_chip();
  CHECK(m.instance_writes.empty());  // the stale 3 did not overwrite the accepted 2
  CHECK_EQ(m.instance_mode, 2);
  r.p.settle();
  // The accepted command wins on both sides.
  CHECK_EQ(m.automation, std::vector<int>{2});
  CHECK_EQ(m.select_mode, 2);
  CHECK_EQ(m.last_mode(), 2);
  CHECK_EQ(m.instance_mode, 2);
}

TEST(mode_base_every_accepted_command_runs_the_automation) {
  Rig r;
  TestModeBase m(EP);
  m.select_mode = 0;
  m.initialize();
  r.p.settle();
  r.p.advance(1);
  r.p.settle();
  m.change_to_mode(1);
  m.change_to_mode(2);
  m.change_to_mode(2);
  r.p.settle();
  CHECK_EQ(m.automation, (std::vector<int>{1, 2, 2}));
  CHECK_EQ(m.select_mode, 2);
  // More than the queue holds: the newest ones are kept, in order.
  for (int i = 10; i < 16; i++)
    m.change_to_mode(i);
  r.p.settle();
  CHECK_EQ(m.automation.size(), (size_t) 3 + ModeChanges::MAX_PENDING);
  CHECK_EQ(m.automation.back(), 15);
}

TEST(mode_base_select_change_is_pushed_through_the_instance) {
  Rig r;
  TestModeBase m(EP);
  m.select_mode = 0;
  m.initialize();
  r.p.settle();
  r.p.advance(1);
  r.p.settle();
  m.instance_writes.clear();
  m.user_selects(4);
  m.user_selects(4);  // no change: no second push
  r.p.settle();
  CHECK_EQ(m.instance_writes, std::vector<int>{4});
  CHECK(m.automation.empty());
}

TEST(mode_base_action_set_always_pushes) {
  Rig r;
  TestModeBase m(EP);
  m.select_mode = 0;
  m.initialize();
  r.p.settle();
  r.p.advance(1);
  r.p.settle();
  m.instance_writes.clear();
  m.instance_mode = 9;  // Matter drifted
  m.action_set(0);
  r.p.settle();
  CHECK_EQ(m.instance_writes, std::vector<int>{0});
}

// ---------------------------------------------------------------------------
// Settings: HoldTime number
// ---------------------------------------------------------------------------

TEST(hold_time_number_race_controller_write_wins_and_converges) {
  Rig r;
  TestHoldTime h(EP);
  h.platform = &r.p;
  h.has_state = true;
  h.number = 30;
  h.initialize();
  r.p.settle();
  CHECK_EQ(h.stored(), 30);
  h.persisted.clear();

  h.number_changed(60);       // push of 60 queued
  h.controller_writes(120);   // a controller writes HoldTime before the push ran
  r.p.run_chip();
  CHECK_EQ(h.stored(), 120);  // the stale 60 was skipped
  r.p.settle();
  CHECK_EQ(h.number, 120);    // the number shows the controller's value
  CHECK_EQ(h.stored(), 120);
  // The controller write was persisted once; ESPHome's later push of the same value does not diverge.
  CHECK_EQ(h.persisted.front(), 120);
  CHECK_EQ(h.persisted.back(), 120);
}

TEST(hold_time_number_changes_win_when_last) {
  Rig r;
  TestHoldTime h(EP);
  h.platform = &r.p;
  h.initialize();  // number has no state: nothing is pushed
  r.p.settle();
  CHECK(r.p.attribute_writes.empty());
  h.controller_writes(45);
  r.p.settle();
  CHECK_EQ(h.number, 45);  // the controller value is shown
  for (int i = 1; i <= 30; i++)
    h.number_changed(i);
  CHECK_EQ(r.p.chip_queue.size(), (size_t) 1);
  r.p.settle();
  CHECK_EQ(h.stored(), 30);
  CHECK(r.p.attribute_writes.back().echo);
}

TEST(setting_echo_of_own_write_is_not_applied_to_the_entity) {
  Rig r;
  TestHoldTime h(EP);
  h.platform = &r.p;
  h.initialize();
  h.number_changed(10);
  r.p.settle();
  // attribute::update() fired POST_UPDATE for our write; the watch dropped it (no persisted controller write,
  // no inbound).
  CHECK_EQ(h.inbound_seq(), (uint32_t) 0);
  CHECK_EQ(h.persisted, std::vector<int>{10});
}

// ---------------------------------------------------------------------------
// Alarm source (Boolean State Configuration)
// ---------------------------------------------------------------------------

TEST(bsc_alarm_enabled_while_source_active_becomes_active) {
  Rig r;
  TestAlarmSource a(EP);
  a.initialize();
  r.p.settle();
  CHECK(a.applied.empty());  // the source has no state yet

  a.source_changed(true);    // no alarm enabled yet: nothing becomes active
  r.p.settle();
  CHECK_EQ(a.applied, std::vector<int>{1});
  CHECK_EQ(a.active, 0);

  a.enable_alarms(0x01);     // EnableDisableAlarm(visual) while the source is active
  CHECK_EQ(a.active, 0);     // not yet: the command is still running
  r.p.settle();
  CHECK_EQ(a.active, 0x01);  // re-applied after the command
  CHECK_EQ(a.applied.back(), 1);
}

TEST(bsc_alarm_enabled_while_source_off_stays_inactive) {
  Rig r;
  TestAlarmSource a(EP);
  a.has_state = true;
  a.on = false;
  a.initialize();
  r.p.settle();
  CHECK_EQ(a.applied, std::vector<int>{0});
  a.enable_alarms(0x03);
  r.p.settle();
  CHECK_EQ(a.active, 0);
  CHECK_EQ(a.applied.size(), (size_t) 1);  // nothing was re-applied
}

TEST(bsc_alarm_source_burst_coalesces_to_latest) {
  Rig r;
  TestAlarmSource a(EP);
  a.initialize();
  a.enabled = 0x03;
  for (int i = 0; i < 9; i++)
    a.source_changed(i % 2 == 0);  // ends ON
  CHECK_EQ(r.p.chip_queue.size(), (size_t) 1);
  r.p.settle();
  CHECK_EQ(a.applied, std::vector<int>{1});
  CHECK_EQ(a.active, 0x03);
  a.source_changed(false);
  r.p.settle();
  CHECK_EQ(a.active, 0);
}

TEST(bsc_enable_command_during_pending_push_is_not_lost) {
  Rig r;
  TestAlarmSource a(EP);
  a.initialize();
  r.p.settle();
  a.source_changed(true);   // push queued
  a.enable_alarms(0x02);    // command arrives before the push ran: the push is skipped ...
  r.p.run_chip();
  CHECK(a.applied.empty());
  r.p.settle();
  CHECK_EQ(a.active, 0x02);  // ... and the reconcile applies the source state afterwards
}
