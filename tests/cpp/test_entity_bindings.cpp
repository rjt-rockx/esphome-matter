// Host tests for the lock, cover and closure bindings on the framework
// (matter_lock_binding.h, matter_cover_binding.h, matter_closure_binding.h).
// Entities and cluster writes are faked; the FakePlatform drives the CHIP queue,
// the main loop and the clock.
#include "fake_platform.h"
#include "matter_closure_binding.h"
#include "matter_cover_binding.h"
#include "matter_lock_binding.h"
#include "test_framework.h"

#include <string>

using namespace esphome::matter;
using tp::FakePlatform;
using tp::HostRole;
using tp::host_set_role;

namespace {

constexpr uint16_t EP = 5;

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

// Runs `f` as the CHIP thread (a delegate callback or attribute write).
template<typename F> void as_chip(F &&f) {
  host_set_role(HostRole::CHIP);
  f();
  host_set_role(HostRole::MAIN);
}

// ---------------------------------------------------------------------------
// Lock
// ---------------------------------------------------------------------------

class TestLock : public MatterLockBinding {
 public:
  explicit TestLock(uint16_t ep) : MatterLockBinding(ep) {}

  enum class Reaction { SETTLES, JAMS, REFUSES, DEFERS };

  LockEntityState state{LockEntityState::LOCKED};
  Reaction reaction{Reaction::SETTLES};
  bool door_state{true};
  bool open_supported{false};
  std::vector<LockEntityState> commands;
  std::vector<MatterLockPush> writes;
  bool all_writes_in_scope{true};

  // ESPHome side: the lock published `s`.
  void entity_changed(LockEntityState s) {
    state = s;
    on_entity_state_();
  }
  void remote(MatterLockOperation op, uint8_t fabric = 0, uint64_t node = 0) {
    MatterLockOperationOrigin origin;
    if (fabric != 0) {
      origin.valid = true;
      origin.fabric_index = fabric;
      origin.node_id = node;
    }
    as_chip([&] { handle_operation(op, origin); });
  }

 protected:
  void setup_() override {
    has_door_state_ = door_state;
    MatterLockBinding::setup_();
  }
  LockEntityState entity_state_() override { return state; }
  bool supports_open_() override { return open_supported; }
  void command_(LockEntityState target) override {
    commands.push_back(target);
    switch (reaction) {
      case Reaction::SETTLES:
        entity_changed(LockEntityState::TRANSITIONAL);
        entity_changed(target);
        break;
      case Reaction::JAMS:
        entity_changed(LockEntityState::JAMMED);
        break;
      default:
        break;
    }
  }
  void write_door_lock_(const MatterLockPush &push) override {
    writes.push_back(push);
    all_writes_in_scope = all_writes_in_scope && MatterBindingBase::is_echo(endpoint_id());
  }
};

}  // namespace

TEST(lock_boot_publish_is_not_announced) {
  Rig r;
  TestLock l(EP);
  l.state = LockEntityState::UNLOCKED;
  l.initialize();
  r.p.settle();
  CHECK_EQ(l.writes.size(), (size_t) 1);
  CHECK(l.writes[0].position == MatterLockPosition::UNLOCKED);
  CHECK(!l.writes[0].announce);
  CHECK(!l.writes[0].origin.valid);
  CHECK(l.writes[0].update_door_state);  // DoorState is created as DoorOpen
  CHECK(l.all_writes_in_scope);
  CHECK_EQ(r.p.legacy_guard_depth, 0);

  TestLock no_door(EP);
  no_door.door_state = false;
  no_door.initialize();
  r.p.settle();
  CHECK(!no_door.writes.back().update_door_state);
}

TEST(lock_remote_operation_is_attributed_once) {
  Rig r;
  TestLock l(EP);
  l.initialize();
  r.p.settle();

  l.remote(MatterLockOperation::UNLOCK, 3, 0xABCD);
  r.p.settle();
  CHECK_EQ(l.commands.size(), (size_t) 1);
  CHECK(l.commands[0] == LockEntityState::UNLOCKED);
  CHECK_EQ(l.writes.size(), (size_t) 2);  // transitional states are not published
  CHECK(l.writes[1].position == MatterLockPosition::UNLOCKED);
  CHECK(l.writes[1].announce);
  CHECK(l.writes[1].origin.valid);
  CHECK_EQ(l.writes[1].origin.fabric_index, (uint8_t) 3);
  CHECK(l.writes[1].origin.node_id == 0xABCD);

  l.entity_changed(LockEntityState::LOCKED);  // manual operation afterwards
  r.p.settle();
  CHECK_EQ(l.writes.size(), (size_t) 3);
  CHECK(!l.writes[2].origin.valid);
  CHECK(l.all_writes_in_scope);
}

TEST(lock_ignored_command_does_not_leak_its_origin) {
  Rig r;
  TestLock l(EP);  // locked
  l.reaction = TestLock::Reaction::REFUSES;
  l.initialize();
  r.p.settle();
  size_t before = l.writes.size();

  l.remote(MatterLockOperation::UNLOCK, 3, 0xABCD);  // ignored by the lock: nothing is published
  r.p.settle();
  CHECK_EQ(l.commands.size(), (size_t) 1);
  CHECK_EQ(l.writes.size(), before);

  r.p.advance(20000);
  l.entity_changed(LockEntityState::UNLOCKED);  // an unrelated, manual change much later
  r.p.settle();
  CHECK_EQ(l.writes.size(), before + 1);
  CHECK(!l.writes.back().origin.valid);
}

TEST(lock_already_in_requested_state_is_acknowledged) {
  Rig r;
  TestLock l(EP);  // locked
  l.initialize();
  r.p.settle();
  l.remote(MatterLockOperation::LOCK, 1, 7);
  r.p.settle();
  CHECK(l.commands.empty());  // no command: the lock would not publish
  CHECK_EQ(l.writes.size(), (size_t) 2);
  CHECK(l.writes[1].position == MatterLockPosition::LOCKED);
  CHECK(l.writes[1].origin.valid);
}

TEST(lock_unbolt_without_open_support_unlocks) {
  Rig r;
  TestLock l(EP);
  l.initialize();
  r.p.settle();
  l.remote(MatterLockOperation::UNBOLT, 1, 1);
  r.p.settle();
  CHECK(l.commands.back() == LockEntityState::UNLOCKED);

  TestLock o(EP);
  o.open_supported = true;
  o.initialize();
  r.p.settle();
  o.remote(MatterLockOperation::UNBOLT, 1, 1);
  r.p.settle();
  CHECK(o.commands.back() == LockEntityState::OPEN);
  CHECK(o.writes.back().position == MatterLockPosition::UNLATCHED);
}

TEST(lock_transitional_states_keep_previous_until_settled) {
  Rig r;
  TestLock l(EP);
  l.reaction = TestLock::Reaction::DEFERS;
  l.initialize();
  r.p.settle();
  l.remote(MatterLockOperation::UNLOCK, 2, 9);
  r.p.settle();
  CHECK_EQ(l.commands.size(), (size_t) 1);

  l.entity_changed(LockEntityState::TRANSITIONAL);
  CHECK(r.p.chip_queue.empty());
  r.p.settle();
  CHECK_EQ(l.writes.size(), (size_t) 1);

  l.entity_changed(LockEntityState::UNLOCKED);
  r.p.settle();
  CHECK_EQ(l.writes.size(), (size_t) 2);
  CHECK(l.writes[1].origin.valid);
}

TEST(lock_jammed_lock_reports_and_clears_door_state) {
  Rig r;
  TestLock l(EP);
  l.reaction = TestLock::Reaction::JAMS;
  l.initialize();
  r.p.settle();
  l.remote(MatterLockOperation::UNLOCK, 1, 5);
  r.p.settle();
  CHECK_EQ(l.writes.size(), (size_t) 2);
  CHECK(l.writes[1].position == MatterLockPosition::NOT_FULLY_LOCKED);
  CHECK(l.writes[1].jammed);
  CHECK(l.writes[1].update_door_state);
  CHECK(l.writes[1].origin.valid);

  l.entity_changed(LockEntityState::LOCKED);  // un-jammed by hand
  r.p.settle();
  CHECK_EQ(l.writes.size(), (size_t) 3);
  CHECK(!l.writes[2].jammed);
  CHECK(l.writes[2].update_door_state);  // DoorState back to null
  CHECK(!l.writes[2].origin.valid);

  l.entity_changed(LockEntityState::UNLOCKED);
  r.p.settle();
  CHECK(!l.writes.back().update_door_state);
}

TEST(lock_refused_command_leaves_matter_equal_to_esphome) {
  Rig r;
  TestLock l(EP);
  l.reaction = TestLock::Reaction::REFUSES;
  l.state = LockEntityState::UNLOCKED;
  l.initialize();
  r.p.settle();
  CHECK_EQ(l.writes.size(), (size_t) 1);

  // A publish is waiting on the CHIP task when the controller's LOCK arrives:
  // the push is skipped (stale), the lock refuses, and the reconcile after the
  // apply still delivers the state.
  l.entity_changed(LockEntityState::UNLOCKED);
  l.remote(MatterLockOperation::LOCK, 1, 5);
  CHECK_EQ(r.p.run_chip(), 1);
  CHECK_EQ(l.writes.size(), (size_t) 1);  // skipped
  r.p.settle();
  CHECK_EQ(l.commands.size(), (size_t) 1);
  CHECK_EQ(l.writes.size(), (size_t) 2);
  CHECK(l.writes[1].position == MatterLockPosition::UNLOCKED);
  CHECK(!l.writes[1].origin.valid);

  // Without anything pending a refusal needs no write at all.
  l.remote(MatterLockOperation::LOCK, 1, 5);
  r.p.settle();
  CHECK_EQ(l.commands.size(), (size_t) 2);
  CHECK_EQ(l.writes.size(), (size_t) 2);
}

TEST(lock_stale_publish_does_not_overwrite_a_remote_operation) {
  Rig r;
  TestLock l(EP);
  l.state = LockEntityState::UNLOCKED;
  l.initialize();
  r.p.settle();

  l.entity_changed(LockEntityState::UNLOCKED);  // queued, not written yet
  l.remote(MatterLockOperation::LOCK, 4, 11);
  r.p.run_chip();  // skipped
  r.p.settle();
  CHECK_EQ(l.writes.size(), (size_t) 2);
  CHECK(l.writes[1].position == MatterLockPosition::LOCKED);
  CHECK(l.writes[1].origin.valid);
  CHECK_EQ(l.writes[1].origin.fabric_index, (uint8_t) 4);
}

TEST(lock_publish_burst_keeps_origin_and_last_state) {
  Rig r;
  TestLock l(EP);
  l.reaction = TestLock::Reaction::DEFERS;
  l.initialize();
  r.p.settle();
  l.remote(MatterLockOperation::UNLOCK, 6, 66);
  r.p.settle();

  l.entity_changed(LockEntityState::UNLOCKED);  // carries the origin
  l.entity_changed(LockEntityState::LOCKED);    // replaces it before the CHIP task ran
  CHECK_EQ(r.p.chip_queue.size(), (size_t) 1);
  r.p.settle();
  CHECK_EQ(l.writes.size(), (size_t) 2);
  CHECK(l.writes[1].position == MatterLockPosition::LOCKED);
  CHECK(l.writes[1].origin.valid);
  CHECK_EQ(l.writes[1].origin.fabric_index, (uint8_t) 6);
}

TEST(lock_boot_door_state_fixup_survives_an_early_publish) {
  Rig r;
  TestLock l(EP);
  l.initialize();
  l.entity_changed(LockEntityState::UNLOCKED);  // before the boot write ran
  r.p.settle();
  CHECK_EQ(l.writes.size(), (size_t) 1);
  CHECK(l.writes[0].update_door_state);
  CHECK(l.writes[0].announce);
}

namespace {

// ---------------------------------------------------------------------------
// Cover
// ---------------------------------------------------------------------------

constexpr uint32_t WC = MatterCoverBinding::WC_CLUSTER;

class TestCover : public MatterCoverBinding {
 public:
  explicit TestCover(uint16_t ep, bool tilt = false) : MatterCoverBinding(ep), with_tilt(tilt) {}

  struct Cmd {
    bool stop;
    bool has_lift;
    uint16_t lift;
    bool has_tilt;
    uint16_t tilt;
  };

  bool with_tilt;
  bool follow{true};  // the cover starts / stops and publishes at once
  CoverEntityView view;
  std::vector<Cmd> cmds;

  void publish(float position, CoverEntityOperation op = CoverEntityOperation::IDLE) {
    view.position = position;
    view.operation = op;
    on_entity_state_();
  }

 protected:
  void setup_() override {
    has_tilt_ = with_tilt;
    has_position_ = true;
    has_stop_ = true;
    MatterCoverBinding::setup_();
  }
  CoverEntityView entity_() override { return view; }
  void command_stop_() override {
    cmds.push_back({true, false, 0, false, 0});
    if (follow)
      publish(view.position, CoverEntityOperation::IDLE);
  }
  void command_move_(bool has_lift, uint16_t lift, bool has_tilt, uint16_t tilt) override {
    cmds.push_back({false, has_lift, lift, has_tilt, tilt});
    if (follow && has_lift)
      publish(view.position < 0.5f ? 0.55f : 0.45f,
              lift == 0 ? CoverEntityOperation::OPENING : CoverEntityOperation::CLOSING);
  }
};

bool find_write(FakePlatform &p, uint32_t at, esp_matter_attr_val_t &out, size_t from = 0) {
  for (size_t i = p.attribute_writes.size(); i > from; i--) {
    const auto &w = p.attribute_writes[i - 1];
    if (w.cl == WC && w.at == at) {
      out = w.value;
      return true;
    }
  }
  return false;
}
uint16_t last_u16(FakePlatform &p, uint32_t at) {
  esp_matter_attr_val_t v;
  CHECK(find_write(p, at, v));
  return v.val.u16;
}
bool last_null(FakePlatform &p, uint32_t at) {
  esp_matter_attr_val_t v;
  CHECK(find_write(p, at, v));
  return v.is_null();
}
uint8_t status(FakePlatform &p) {
  esp_matter_attr_val_t v;
  CHECK(find_write(p, MatterCoverBinding::WC_OPERATIONAL_STATUS, v));
  return v.val.u8;
}

constexpr uint32_t TARGET_LIFT = MatterCoverBinding::WC_TARGET_LIFT;
constexpr uint32_t CURRENT_LIFT = MatterCoverBinding::WC_CURRENT_LIFT;
constexpr uint32_t TARGET_TILT = MatterCoverBinding::WC_TARGET_TILT;
constexpr uint32_t CURRENT_TILT = MatterCoverBinding::WC_CURRENT_TILT;

}  // namespace

TEST(cover_boot_pushes_positions_and_status_with_report) {
  Rig r;
  TestCover c(EP);
  c.view.position = 1.0f;
  c.initialize();
  r.p.settle();
  CHECK_EQ(r.p.attribute_writes.size(), (size_t) 3);  // target, current, status
  for (const auto &w : r.p.attribute_writes) {
    CHECK(w.report);
    CHECK(w.echo);
    CHECK_EQ(w.ep, EP);
  }
  CHECK_EQ(last_u16(r.p, CURRENT_LIFT), (uint16_t) 0);  // fully open
  CHECK_EQ(last_u16(r.p, TARGET_LIFT), (uint16_t) 0);
  CHECK_EQ(status(r.p), (uint8_t) 0);
  CHECK_EQ(r.p.legacy_guard_depth, 0);
}

TEST(cover_unknown_position_is_written_as_null) {
  Rig r;
  TestCover c(EP, true);
  c.initialize();  // position and tilt NAN
  r.p.settle();
  CHECK_EQ(r.p.attribute_writes.size(), (size_t) 5);
  CHECK(last_null(r.p, CURRENT_LIFT));
  CHECK(last_null(r.p, TARGET_LIFT));
  CHECK(last_null(r.p, CURRENT_TILT));
}

TEST(cover_position_burst_coalesces_to_the_newest_state) {
  Rig r;
  TestCover c(EP, true);
  c.view.position = 1.0f;
  c.view.tilt = 1.0f;
  c.initialize();
  r.p.settle();
  size_t before = r.p.attribute_writes.size();

  for (int i = 0; i < 30; i++)
    c.publish(1.0f - 0.01f * (float) i, CoverEntityOperation::CLOSING);
  CHECK_EQ(r.p.chip_queue.size(), (size_t) 1);
  c.view.tilt = 0.5f;
  c.publish(0.5f, CoverEntityOperation::CLOSING);
  CHECK_EQ(r.p.chip_queue.size(), (size_t) 1);
  r.p.settle();
  CHECK_EQ(r.p.attribute_writes.size() - before, (size_t) 5);  // one state: lift x2, tilt x2, status
  CHECK_EQ(last_u16(r.p, CURRENT_LIFT), (uint16_t) 5000);
  CHECK_EQ(last_u16(r.p, CURRENT_TILT), (uint16_t) 5000);
  CHECK_EQ(status(r.p), (uint8_t) 10);  // MovingDownOrClose, global + lift
  // ESPHome started the movement: the target is the end of travel.
  CHECK_EQ(last_u16(r.p, TARGET_LIFT), (uint16_t) 10000);
}

TEST(cover_controller_up_and_down_requests_become_one_call) {
  Rig r;
  TestCover c(EP, true);
  c.view.position = 0.5f;
  c.view.tilt = 0.5f;
  c.initialize();
  r.p.settle();
  size_t before = r.p.attribute_writes.size();

  // UpOrOpen requests lift and tilt (CHIP delegate), same loop iteration.
  as_chip([&] {
    c.submit_movement(true, 0);
    c.submit_movement(false, 0);
  });
  r.p.settle();
  CHECK_EQ(c.cmds.size(), (size_t) 1);
  CHECK(c.cmds[0].has_lift && c.cmds[0].has_tilt);
  CHECK_EQ(c.cmds[0].lift, (uint16_t) 0);
  CHECK_EQ(c.cmds[0].tilt, (uint16_t) 0);
  CHECK(r.p.attribute_writes.size() > before);
  // While moving the target is the controller's request.
  CHECK_EQ(last_u16(r.p, TARGET_LIFT), (uint16_t) 0);
  CHECK_EQ(last_u16(r.p, TARGET_TILT), (uint16_t) 0);
  CHECK_EQ(status(r.p), (uint8_t) 5);  // MovingUpOrOpen
}

TEST(cover_request_during_movement_replaces_target_and_is_not_overwritten) {
  Rig r;
  TestCover c(EP);
  c.view.position = 0.6f;
  c.initialize();
  r.p.settle();
  as_chip([&] { c.submit_movement(true, 0); });
  r.p.settle();
  CHECK_EQ(last_u16(r.p, TARGET_LIFT), (uint16_t) 0);
  r.p.advance(250);
  r.p.settle();

  // A position update is waiting on the CHIP task when the controller reverses.
  c.publish(0.7f, CoverEntityOperation::OPENING);
  as_chip([&] { c.submit_movement(true, 10000); });
  CHECK_EQ(r.p.run_chip(), 1);
  size_t after_skip = r.p.attribute_writes.size();
  r.p.settle();
  CHECK_EQ(c.cmds.size(), (size_t) 2);
  CHECK_EQ(c.cmds[1].lift, (uint16_t) 10000);
  CHECK(r.p.attribute_writes.size() > after_skip);  // fresh state after the apply
  CHECK_EQ(last_u16(r.p, TARGET_LIFT), (uint16_t) 10000);
  CHECK_EQ(status(r.p), (uint8_t) 10);
  // The settle window expiring pushes the same state again, not a stale one.
  r.p.advance(250);
  r.p.settle();
  CHECK_EQ(last_u16(r.p, TARGET_LIFT), (uint16_t) 10000);
  CHECK_EQ(status(r.p), (uint8_t) 10);
}

TEST(cover_target_resets_when_the_cover_comes_to_rest) {
  Rig r;
  TestCover c(EP);
  c.view.position = 0.6f;
  c.initialize();
  r.p.settle();
  as_chip([&] { c.submit_movement(true, 0); });
  r.p.settle();
  c.publish(1.0f, CoverEntityOperation::IDLE);
  r.p.settle();
  CHECK_EQ(last_u16(r.p, TARGET_LIFT), (uint16_t) 0);
  CHECK_EQ(last_u16(r.p, CURRENT_LIFT), (uint16_t) 0);
  CHECK_EQ(status(r.p), (uint8_t) 0);
  // ESPHome moves it on its own now: end of travel, not the old request.
  c.publish(0.9f, CoverEntityOperation::CLOSING);
  r.p.settle();
  CHECK_EQ(last_u16(r.p, TARGET_LIFT), (uint16_t) 10000);
}

TEST(cover_stop_cancels_pending_movement_and_pushes_at_once) {
  Rig r;
  TestCover c(EP);
  c.view.position = 0.4f;
  c.initialize();
  r.p.settle();

  // Move then stop before the main loop runs: only the stop is performed.
  as_chip([&] {
    c.submit_movement(true, 0);
    c.submit_stop();
  });
  r.p.settle();
  CHECK_EQ(c.cmds.size(), (size_t) 1);
  CHECK(c.cmds[0].stop);

  // Stop during a movement.
  as_chip([&] { c.submit_movement(true, 10000); });
  r.p.settle();
  CHECK_EQ(c.cmds.size(), (size_t) 2);
  CHECK_EQ(status(r.p), (uint8_t) 10);
  as_chip([&] { c.submit_stop(); });
  r.p.run_main();  // no timers advanced: the stop push is immediate
  r.p.run_chip();
  CHECK_EQ(c.cmds.size(), (size_t) 3);
  CHECK(c.cmds[2].stop);
  CHECK_EQ(status(r.p), (uint8_t) 0);
  // At rest the target is the current position (the stop forgot the request).
  CHECK_EQ(last_u16(r.p, TARGET_LIFT), last_u16(r.p, CURRENT_LIFT));
}

TEST(cover_stop_on_an_idle_cover_still_pushes) {
  Rig r;
  TestCover c(EP);
  c.follow = false;
  c.view.position = 0.5f;
  c.initialize();
  r.p.settle();
  size_t before = r.p.attribute_writes.size();
  as_chip([&] { c.submit_stop(); });
  r.p.run_main();
  r.p.run_chip();
  CHECK(r.p.attribute_writes.size() > before);  // no cover publish needed
}

TEST(cover_move_then_stop_then_move_runs_stop_before_the_move) {
  Rig r;
  TestCover c(EP);
  c.view.position = 0.5f;
  c.initialize();
  r.p.settle();
  as_chip([&] {
    c.submit_movement(true, 0);
    c.submit_stop();
    c.submit_movement(true, 10000);
  });
  r.p.settle();
  CHECK_EQ(c.cmds.size(), (size_t) 2);
  CHECK(c.cmds[0].stop);
  CHECK(!c.cmds[1].stop);
  CHECK_EQ(c.cmds[1].lift, (uint16_t) 10000);
}

TEST(cover_refused_command_is_reconciled_after_the_settle_window) {
  Rig r;
  TestCover c(EP);
  c.follow = false;  // the cover ignores the command and never publishes
  c.view.position = 0.5f;
  c.initialize();
  r.p.settle();
  size_t before = r.p.attribute_writes.size();

  as_chip([&] { c.submit_movement(true, 0); });
  r.p.settle();
  CHECK_EQ(c.cmds.size(), (size_t) 1);
  r.p.advance(199);
  r.p.settle();
  CHECK_EQ(r.p.attribute_writes.size(), before);
  r.p.advance(1);
  r.p.settle();
  CHECK(r.p.attribute_writes.size() > before);
  CHECK_EQ(status(r.p), (uint8_t) 0);
  // The cluster set Target to the request; Matter now shows the real state.
  CHECK_EQ(last_u16(r.p, TARGET_LIFT), (uint16_t) 5000);
}

TEST(cover_publish_ends_the_settle_wait_early) {
  Rig r;
  TestCover c(EP);
  c.view.position = 0.5f;
  c.initialize();
  r.p.settle();
  as_chip([&] { c.submit_movement(true, 0); });
  r.p.settle();  // the cover published while applying
  size_t n = r.p.attribute_writes.size();
  r.p.advance(250);
  r.p.settle();
  // The expiry of an ended window does not duplicate the work beyond one push.
  CHECK(r.p.attribute_writes.size() <= n + 3);
}

TEST(cover_push_survives_a_full_chip_queue) {
  Rig r;
  TestCover c(EP);
  c.view.position = 0.5f;
  c.initialize();
  r.p.settle();
  r.p.fail_schedule = 2;
  c.publish(0.2f, CoverEntityOperation::CLOSING);
  c.publish(0.1f, CoverEntityOperation::CLOSING);
  r.p.advance(200);
  r.p.settle();
  CHECK_EQ(last_u16(r.p, CURRENT_LIFT), (uint16_t) 9000);
}

namespace {

// ---------------------------------------------------------------------------
// Closure
// ---------------------------------------------------------------------------

class TestClosure : public MatterClosureBinding {
 public:
  explicit TestClosure(uint16_t ep) : MatterClosureBinding(ep) {}

  bool follow{true};  // the cover starts moving / stops at once
  bool cluster_ready{true};
  bool has_stop{true};
  ClosureEntityView view;
  std::vector<std::string> cmds;
  std::vector<MatterClosurePush> writes;
  int completions{0};
  int attempts{0};
  bool writes_in_scope{true};

  void publish(float position, ClosureEntityOperation op) {
    view.position = position;
    view.operation = op;
    on_entity_state_();
  }
  void command(ClosureCommand c) {
    as_chip([&] { submit_command(c); });
  }

 protected:
  ClosureEntityView entity_() override { return view; }
  void command_open_() override {
    cmds.push_back("open");
    if (follow)
      publish(0.3f, ClosureEntityOperation::OPENING);
  }
  void command_close_() override {
    cmds.push_back("close");
    if (follow)
      publish(0.7f, ClosureEntityOperation::CLOSING);
  }
  void command_stop_() override {
    cmds.push_back("stop");
    if (follow)
      publish(view.position, ClosureEntityOperation::IDLE);
  }
  bool can_stop_() override { return has_stop; }
  bool write_cluster_(const MatterClosurePush &s) override {
    attempts++;
    if (!cluster_ready)
      return false;
    writes.push_back(s);
    writes_in_scope = writes_in_scope && MatterBindingBase::is_echo(endpoint_id());
    if (take_completion(s))
      completions++;
    return true;
  }
};

}  // namespace

TEST(closure_boot_state_and_move_to_open_completes_once) {
  Rig r;
  TestClosure c(EP);
  c.view.position = 0.0f;
  c.initialize();
  r.p.settle();
  CHECK_EQ(c.writes.size(), (size_t) 1);
  CHECK(c.writes[0].current == ClosurePosition::FULLY_CLOSED);
  CHECK(!c.writes[0].moving);
  CHECK(c.writes[0].known);
  CHECK_EQ(c.completions, 0);
  CHECK(c.writes_in_scope);

  c.command(ClosureCommand::OPEN);
  r.p.settle();
  CHECK_EQ(c.cmds.size(), (size_t) 1);
  CHECK(c.writes.back().moving);
  CHECK_EQ(c.writes.back().target, (int8_t) 1);
  CHECK(c.writes.back().current == ClosurePosition::PARTIALLY_OPENED);

  // The movement ends; both publishes coalesce into one write, the completion
  // must survive that.
  c.publish(1.0f, ClosureEntityOperation::IDLE);
  c.publish(1.0f, ClosureEntityOperation::IDLE);
  CHECK_EQ(r.p.chip_queue.size(), (size_t) 1);
  r.p.settle();
  CHECK(!c.writes.back().moving);
  CHECK(c.writes.back().current == ClosurePosition::FULLY_OPENED);
  CHECK_EQ(c.completions, 1);
  c.publish(1.0f, ClosureEntityOperation::IDLE);
  r.p.settle();
  CHECK_EQ(c.completions, 1);
}

TEST(closure_completion_survives_coalescing_with_the_moving_state) {
  Rig r;
  TestClosure c(EP);
  c.follow = false;
  c.view.position = 0.0f;
  c.initialize();
  r.p.settle();
  c.publish(0.5f, ClosureEntityOperation::OPENING);  // ESPHome started it itself
  c.publish(1.0f, ClosureEntityOperation::IDLE);     // and it finished, same CHIP window
  CHECK_EQ(r.p.chip_queue.size(), (size_t) 1);
  r.p.settle();
  CHECK_EQ(c.completions, 1);
  CHECK(!c.writes.back().moving);
}

TEST(closure_stop_interrupts_without_completion) {
  Rig r;
  TestClosure c(EP);
  c.view.position = 0.0f;
  c.initialize();
  r.p.settle();
  c.command(ClosureCommand::OPEN);
  r.p.settle();
  CHECK(c.writes.back().moving);

  c.command(ClosureCommand::STOP);
  r.p.settle();
  CHECK_EQ(c.cmds.size(), (size_t) 2);
  CHECK(c.cmds[1] == "stop");
  CHECK(!c.writes.back().moving);
  CHECK_EQ(c.writes.back().target, (int8_t) -1);
  CHECK(c.writes.back().current == ClosurePosition::PARTIALLY_OPENED);
  CHECK_EQ(c.completions, 0);

  // A stopped cover that later reaches an end on its own has no target left.
  c.publish(1.0f, ClosureEntityOperation::IDLE);
  r.p.settle();
  CHECK_EQ(c.completions, 0);
}

TEST(closure_stop_without_stop_action_still_pushes_the_real_state) {
  Rig r;
  TestClosure c(EP);
  c.has_stop = false;
  c.follow = false;
  c.view.position = 0.0f;
  c.initialize();
  r.p.settle();
  c.publish(0.4f, ClosureEntityOperation::OPENING);
  r.p.settle();
  size_t n = c.writes.size();
  c.command(ClosureCommand::STOP);
  r.p.settle();
  CHECK(c.cmds.empty());  // no stop action
  CHECK(c.writes.size() > n);
  CHECK(c.writes.back().moving);  // the cover keeps moving, Matter says so
}

TEST(closure_move_to_current_end_does_nothing_but_settles_state) {
  Rig r;
  TestClosure c(EP);
  c.view.position = 1.0f;
  c.initialize();
  r.p.settle();
  size_t n = c.writes.size();
  c.command(ClosureCommand::OPEN);
  r.p.settle();
  CHECK(c.cmds.empty());
  CHECK(c.writes.size() > n);  // the cluster set Moving; this pushes Stopped
  CHECK(!c.writes.back().moving);
  CHECK(c.writes.back().current == ClosurePosition::FULLY_OPENED);
  CHECK_EQ(c.completions, 0);
}

TEST(closure_move_to_close_from_open_targets_closed) {
  Rig r;
  TestClosure c(EP);
  c.view.position = 1.0f;
  c.initialize();
  r.p.settle();
  c.command(ClosureCommand::CLOSE);
  r.p.settle();
  CHECK_EQ(c.cmds.size(), (size_t) 1);
  CHECK(c.cmds[0] == "close");
  CHECK_EQ(c.writes.back().target, (int8_t) 0);
  c.publish(0.0f, ClosureEntityOperation::IDLE);
  r.p.settle();
  CHECK(c.writes.back().current == ClosurePosition::FULLY_CLOSED);
  CHECK_EQ(c.completions, 1);
}

TEST(closure_move_then_stop_in_one_window_only_stops) {
  Rig r;
  TestClosure c(EP);
  c.view.position = 0.0f;
  c.initialize();
  r.p.settle();
  as_chip([&] {
    c.submit_command(ClosureCommand::OPEN);
    c.submit_command(ClosureCommand::STOP);
  });
  r.p.settle();
  CHECK_EQ(c.cmds.size(), (size_t) 1);
  CHECK(c.cmds[0] == "stop");
  CHECK_EQ(c.completions, 0);
}

TEST(closure_stale_push_does_not_overwrite_a_move_to) {
  Rig r;
  TestClosure c(EP);
  c.view.position = 1.0f;
  c.initialize();
  r.p.settle();
  size_t n = c.writes.size();

  // "Stopped at open" is waiting on the CHIP task when MoveTo(close) arrives.
  c.publish(1.0f, ClosureEntityOperation::IDLE);
  c.command(ClosureCommand::CLOSE);
  CHECK_EQ(r.p.run_chip(), 1);
  CHECK_EQ(c.writes.size(), n);  // skipped: older than the command
  r.p.settle();
  CHECK_EQ(c.cmds.size(), (size_t) 1);
  CHECK(c.writes.size() > n);
  CHECK(c.writes.back().moving);
  CHECK_EQ(c.writes.back().target, (int8_t) 0);
  for (size_t i = n; i < c.writes.size(); i++)
    CHECK(c.writes[i].moving);  // nothing stale slipped in between
}

TEST(closure_cluster_missing_at_startup_is_retried) {
  Rig r;
  TestClosure c(EP);
  c.cluster_ready = false;
  c.view.position = 0.0f;
  c.initialize();
  r.p.settle();
  CHECK_EQ(c.attempts, 1);
  CHECK(c.writes.empty());

  for (int i = 0; i < 3; i++) {
    r.p.advance(MatterClosureBinding::PUSH_RETRY_MS);
    r.p.settle();
  }
  CHECK_EQ(c.attempts, 4);
  c.cluster_ready = true;
  r.p.advance(MatterClosureBinding::PUSH_RETRY_MS);
  r.p.settle();
  CHECK_EQ(c.writes.size(), (size_t) 1);
  CHECK(c.writes[0].current == ClosurePosition::FULLY_CLOSED);
  // Done: no more attempts.
  int attempts = c.attempts;
  r.p.advance(5000);
  r.p.settle();
  CHECK_EQ(c.attempts, attempts);
}

TEST(closure_retry_gives_up_after_the_limit) {
  Rig r;
  TestClosure c(EP);
  c.cluster_ready = false;
  c.initialize();
  r.p.settle();
  for (int i = 0; i < 40; i++) {
    r.p.advance(MatterClosureBinding::PUSH_RETRY_MS);
    r.p.settle();
  }
  CHECK_EQ(c.attempts, 1 + MatterClosureBinding::MAX_PUSH_RETRIES);
  bool warned = false;
  for (const auto &l : host_stub::log_lines())
    if (l.level == 'W' && l.text.find("never became available") != std::string::npos)
      warned = true;
  CHECK(warned);
}

TEST(closure_unknown_position_is_reported_as_null_position) {
  Rig r;
  TestClosure c(EP);
  c.initialize();  // NAN
  r.p.settle();
  CHECK(!c.writes[0].known);
  CHECK(c.writes[0].current == ClosurePosition::PARTIALLY_OPENED);
}
