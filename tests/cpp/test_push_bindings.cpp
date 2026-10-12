// Host tests for the push-mostly bindings built on the framework
// (components/matter/matter_push_bindings.h): the OnOff (switch) binding and the
// attribute push binding used by sensors, binary sensors and flag enums.
#include "fake_platform.h"
#include "matter_push_bindings.h"
#include "test_framework.h"

using namespace esphome::matter;
using tp::FakePlatform;
using tp::HostRole;
using tp::host_set_role;

namespace {

constexpr uint16_t EP = 3;
constexpr uint32_t ONOFF = MatterOnOffBinding::ON_OFF_CLUSTER;
constexpr uint32_t ONOFF_AT = MatterOnOffBinding::ON_OFF_ATTRIBUTE;
constexpr uint32_t TEMP_CL = 0x0402;
constexpr uint32_t TEMP_AT = 0x0000;

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

// A switch entity: `accept` false models a switch that refuses commands.
class TestSwitch : public MatterOnOffBinding {
 public:
  explicit TestSwitch(uint16_t ep) : MatterOnOffBinding(ep) {}

  bool state{false};
  bool accept{true};
  bool publish_on_command{true};
  std::vector<int> commands;

  // ESPHome side: the switch changed (state callback).
  void entity_changed(bool on) {
    state = on;
    request_push();
  }
  // Controller write: the store changed, POST_UPDATE reaches the watch on CHIP.
  void controller_write(FakePlatform &p, bool on) {
    host_set_role(HostRole::CHIP);
    p.store[{endpoint_id(), ONOFF, ONOFF_AT}] = tp::bool_val(on);
    p.fire_attribute(endpoint_id(), ONOFF, ONOFF_AT, tp::bool_val(on));
    host_set_role(HostRole::MAIN);
  }

 protected:
  bool entity_on_() override { return state; }
  void command_on_(bool on) override {
    commands.push_back(on);
    if (state == on || !accept)
      return;
    state = on;
    if (publish_on_command)
      request_push();
  }
};

bool last_bool(FakePlatform &p) { return p.attribute_writes.back().value.val.b; }
bool stored_bool(FakePlatform &p) { return p.store[{EP, ONOFF, ONOFF_AT}].val.b; }
int16_t stored_temp(FakePlatform &p) { return p.store[{EP, TEMP_CL, TEMP_AT}].val.i16; }

// A sensor entity bound through MatterAttributePushBinding.
class TestSensor : public MatterAttributePushBinding {
 public:
  TestSensor(uint16_t ep, bool report = false, uint32_t cl = TEMP_CL, uint32_t at = TEMP_AT)
      : MatterAttributePushBinding(ep, cl, at, report) {}

  bool has_state{false};
  bool reject{false};  // converter returns an invalid value
  int16_t reading{0};
  std::vector<int> custom_writes;
  bool custom{false};
  bool custom_in_scope{false};

  void publish(int16_t v) {
    has_state = true;
    reading = v;
    request_push();
  }

 protected:
  ValueState capture_() override {
    ValueState s;
    if (!has_state)
      return s;
    s.value.type = ESP_MATTER_VAL_TYPE_INT16;
    s.value.val.i16 = reading;
    s.valid = !reject;
    return s;
  }
  void write_attribute_(const ValueState &s) override {
    if (!custom) {
      MatterAttributePushBinding::write_attribute_(s);
      return;
    }
    custom_writes.push_back(s.value.val.i16);
    custom_in_scope = MatterBindingBase::is_echo(endpoint_id());
  }
};

}  // namespace

// ---------------------------------------------------------------------------
// Switch
// ---------------------------------------------------------------------------

TEST(switch_esphome_burst_coalesces_to_latest_report) {
  Rig r;
  TestSwitch sw(EP);
  sw.initialize();  // MATTER_WINS: nothing stored, nothing pushed
  r.p.settle();
  CHECK(r.p.attribute_writes.empty());

  for (int i = 0; i < 20; i++)
    sw.entity_changed(i % 2 == 0); 
  sw.entity_changed(true);
  CHECK_EQ(r.p.chip_queue.size(), (size_t) 1);
  r.p.settle();
  CHECK_EQ(r.p.attribute_writes.size(), (size_t) 1);
  CHECK(last_bool(r.p));
  const auto &w = r.p.attribute_writes.back();
  CHECK(w.report);  // report(): no attribute callbacks, as before
  CHECK_EQ(w.cl, ONOFF);
  CHECK_EQ(w.at, ONOFF_AT);
  CHECK(w.echo);
  CHECK_EQ(r.p.legacy_guard_depth, 0);
  CHECK(sw.commands.empty());  // the push is never fed back as a command
}

TEST(switch_controller_write_commands_entity_and_reconciles) {
  Rig r;
  TestSwitch sw(EP);
  sw.initialize();
  r.p.settle();

  sw.controller_write(r.p, true);
  CHECK_EQ(r.p.main_queue.size(), (size_t) 1);
  r.p.settle();
  CHECK_EQ(sw.commands, std::vector<int>{1});
  CHECK(sw.state);
  // Reconcile after the apply: Matter gets ESPHome's state (one coalesced write).
  CHECK_EQ(r.p.attribute_writes.size(), (size_t) 1);
  CHECK(last_bool(r.p));
}

TEST(switch_refused_command_reconciles_matter_back) {
  Rig r;
  TestSwitch sw(EP);
  sw.accept = false;
  sw.state = false;
  sw.initialize();
  r.p.settle();

  sw.controller_write(r.p, true);  // store now says on, the switch refuses
  r.p.settle();
  CHECK_EQ(sw.commands, std::vector<int>{1});
  CHECK(!sw.state);
  CHECK_EQ(r.p.attribute_writes.size(), (size_t) 1);
  CHECK(!last_bool(r.p));  // Matter restored to off
  CHECK(!stored_bool(r.p));
}

TEST(switch_stale_push_does_not_overwrite_controller_write) {
  Rig r;
  TestSwitch sw(EP);
  sw.initialize();
  r.p.settle();

  sw.entity_changed(true);         // push (on) queued
  sw.controller_write(r.p, false);  // controller writes off before the push runs
  r.p.run_chip();                  // the stale push must be skipped
  CHECK(r.p.attribute_writes.empty());
  r.p.settle();
  CHECK_EQ(sw.commands, std::vector<int>{0});
  CHECK(!sw.state);
  CHECK_EQ(r.p.attribute_writes.size(), (size_t) 1);
  CHECK(!last_bool(r.p));
}

TEST(switch_matter_wins_replays_stored_value_at_boot) {
  {
    Rig r;
    TestSwitch sw(EP);
    sw.state = false;
    r.p.store[{EP, ONOFF, ONOFF_AT}] = tp::bool_val(true);
    sw.initialize();
    r.p.settle();
    CHECK_EQ(sw.commands, std::vector<int>{1});  // Matter's value is applied
    CHECK(sw.state);
    CHECK_EQ(r.p.attribute_writes.size(), (size_t) 1);  // then confirmed back
    CHECK(last_bool(r.p));
  }
  {  // Nothing stored: the entity is not pushed and not commanded at boot.
    Rig r;
    TestSwitch sw(EP);
    sw.state = true;
    sw.initialize();
    r.p.settle();
    CHECK(sw.commands.empty());
    CHECK(r.p.attribute_writes.empty());
    // The first state change after boot is pushed.
    sw.entity_changed(false);
    r.p.settle();
    CHECK_EQ(r.p.attribute_writes.size(), (size_t) 1);
    CHECK(!last_bool(r.p));
  }
}

TEST(switch_already_in_state_command_still_reconciles) {
  Rig r;
  TestSwitch sw(EP);
  sw.state = true;
  sw.initialize();
  r.p.settle();
  sw.controller_write(r.p, true);  // store says on, entity already on
  r.p.settle();
  CHECK_EQ(sw.commands, std::vector<int>{1});
  CHECK_EQ(r.p.attribute_writes.size(), (size_t) 1);
  CHECK(last_bool(r.p));
}

TEST(switch_push_in_other_endpoint_scope_is_not_echo) {
  Rig r;
  TestSwitch a(EP);
  TestSwitch b(EP + 1);
  a.initialize();
  b.initialize();
  r.p.settle();
  a.entity_changed(true);
  b.entity_changed(true);
  r.p.settle();
  CHECK_EQ(r.p.attribute_writes.size(), (size_t) 2);
  CHECK(r.p.attribute_writes[0].echo);
  CHECK(r.p.attribute_writes[1].echo);
  // A controller write on b is delivered to b only.
  b.controller_write(r.p, false);
  r.p.settle();
  CHECK(a.commands.empty());
  CHECK_EQ(b.commands, std::vector<int>{0});
}

// ---------------------------------------------------------------------------
// Sensors (attribute push)
// ---------------------------------------------------------------------------

TEST(sensor_burst_keeps_one_work_item_and_the_last_value) {
  Rig r;
  TestSensor s(EP);
  s.initialize();  // no state yet
  r.p.settle();
  CHECK(r.p.attribute_writes.empty());

  for (int i = 1; i <= 200; i++)
    s.publish(static_cast<int16_t>(i));
  CHECK_EQ(r.p.chip_queue.size(), (size_t) 1);  // no work item (or allocation) per reading
  r.p.settle();
  CHECK_EQ(r.p.attribute_writes.size(), (size_t) 1);
  const auto &w = r.p.attribute_writes.back();
  CHECK_EQ(w.value.val.i16, (int16_t) 200);
  CHECK(!w.report);
  CHECK(w.echo);
  CHECK_EQ(w.cl, TEMP_CL);
}

TEST(sensor_boot_pushes_existing_state_only) {
  {
    Rig r;
    TestSensor s(EP);
    s.has_state = true;
    s.reading = 21;
    s.initialize();
    r.p.settle();
    CHECK_EQ(r.p.attribute_writes.size(), (size_t) 1);
    CHECK_EQ(r.p.attribute_writes.back().value.val.i16, (int16_t) 21);
  }
  {
    Rig r;
    TestSensor s(EP);
    s.initialize();
    r.p.settle();
    CHECK(r.p.attribute_writes.empty());
  }
}

TEST(sensor_rejected_reading_is_not_written) {
  Rig r;
  TestSensor s(EP);
  s.initialize();
  s.reject = true;
  s.publish(5);
  r.p.settle();
  CHECK(r.p.attribute_writes.empty());
  s.reject = false;
  s.publish(6);
  r.p.settle();
  CHECK_EQ(r.p.attribute_writes.size(), (size_t) 1);
  CHECK_EQ(r.p.attribute_writes.back().value.val.i16, (int16_t) 6);
}

TEST(sensor_final_value_survives_a_full_chip_queue) {
  Rig r;
  TestSensor s(EP);
  s.initialize();
  r.p.settle();

  r.p.fail_schedule = 3;  // the CHIP event queue is full for a while
  s.publish(1);
  s.publish(2);
  s.publish(3);
  CHECK(r.p.chip_queue.empty());
  r.p.advance(ChipWorkSlot::RETRY_MS);  // retry 1: still full
  r.p.advance(ChipWorkSlot::RETRY_MS);  // retry 2: still full
  s.publish(4);                         // coalesces into the retry
  r.p.advance(ChipWorkSlot::RETRY_MS);  // retry 3: scheduled
  r.p.settle();
  CHECK_EQ(r.p.attribute_writes.size(), (size_t) 1);
  CHECK_EQ(r.p.attribute_writes.back().value.val.i16, (int16_t) 4);  // never dropped, never stale
}

TEST(sensor_write_error_is_logged_not_fatal) {
  Rig r;
  TestSensor s(EP);
  s.initialize();
  r.p.write_result = ESP_FAIL;
  s.publish(1);
  r.p.settle();
  bool logged = false;
  for (auto &l : host_stub::log_lines())
    logged = logged || (l.level == 'E' && l.text.find("Failed to update attribute") != std::string::npos);
  CHECK(logged);
  // Recovers on the next reading.
  r.p.write_result = ESP_OK;
  s.publish(2);
  r.p.settle();
  CHECK_EQ(stored_temp(r.p), (int16_t) 2);
}

TEST(sensor_code_driven_writer_runs_in_push_scope) {
  Rig r;
  TestSensor s(EP);
  s.custom = true;  // stands in for update_code_driven_sensor_attribute()
  s.initialize();
  s.publish(7);
  s.publish(8);
  r.p.settle();
  CHECK_EQ(s.custom_writes, std::vector<int>{8});
  CHECK(s.custom_in_scope);
  CHECK(r.p.attribute_writes.empty());
  CHECK(!MatterBindingBase::is_echo(EP));
}

TEST(sensor_update_does_not_echo_into_watchers) {
  Rig r;
  // The sensor writes the attribute the switch binding watches (same endpoint) with attribute::update(),
  // which fires POST_UPDATE. The PushScope makes the watch drop it.
  TestSensor s(EP, false, ONOFF, ONOFF_AT);
  TestSwitch watcher(EP);
  watcher.initialize();
  s.initialize();
  r.p.settle();
  s.publish(1);
  r.p.settle();
  CHECK_EQ(r.p.attribute_writes.size(), (size_t) 1);
  CHECK(r.p.attribute_writes.back().echo);
  CHECK(watcher.commands.empty());

  // The same store update from a controller (no PushScope) is delivered.
  watcher.controller_write(r.p, true);
  r.p.settle();
  CHECK_EQ(watcher.commands, std::vector<int>{1});
}
