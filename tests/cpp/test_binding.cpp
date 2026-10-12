// Host tests for the binding framework core (components/matter/matter_binding.*).
#include "fake_platform.h"
#include "test_framework.h"

#include <memory>

using namespace esphome::matter;
using tp::FakePlatform;
using tp::HostRole;
using tp::host_set_role;

namespace {

constexpr uint32_t CL = 0x0006;
constexpr uint32_t AT = 0x0000;

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

// A binding over one int. `entity` is the ESPHome truth.
class TestBinding : public MatterBinding<int, int> {
 public:
  explicit TestBinding(uint16_t ep, BindingPolicy policy = {}) : MatterBinding<int, int>(ep, policy) {}

  // Entity model
  int entity{0};
  bool refuse{false};              // entity ignores controller commands
  bool publish_on_apply{false};    // entity publishes synchronously when accepting
  FakePlatform *publish_later_on{nullptr};  // publish `publish_delay` ms after accepting
  uint32_t publish_delay{0};
  bool watch{false};

  // Observations
  std::vector<int> matter_writes;
  std::vector<int> applied;
  std::vector<int> attr_events;
  bool echo_seen_in_write{false};
  bool other_ep_echo_seen{true};
  uint16_t other_ep{0};

  // Entity-side API used by tests (main loop).
  void entity_changed(int v) {
    entity = v;
    request_push();
  }
  // Controller write (CHIP thread).
  void controller_write(int v) {
    host_set_role(HostRole::CHIP);
    submit_inbound(v);
    host_set_role(HostRole::MAIN);
  }

 protected:
  void setup_() override {
    if (watch)
      watch_attribute(CL, AT);
  }
  int capture_() override { return entity; }
  void write_matter_(const int &s) override {
    matter_writes.push_back(s);
    echo_seen_in_write = MatterBindingBase::is_echo(endpoint_id());
    if (other_ep)
      other_ep_echo_seen = MatterBindingBase::is_echo(other_ep);
    if (on_write)
      on_write();
  }
  void apply_inbound_(const int &in) override {
    applied.push_back(in);
    if (refuse)
      return;
    entity = in;
    if (publish_on_apply)
      request_push();
    if (publish_later_on != nullptr) {
      publish_later_on->main_after(publish_delay, [this]() { request_push(); });
    }
  }
  void on_attribute_(uint32_t, uint32_t, const esp_matter_attr_val_t &v) override {
    attr_events.push_back(v.val.b ? 1 : 0);
  }

 public:
  std::function<void()> on_write;
};

}  // namespace

TEST(burst_last_value_lands) {
  Rig r;
  TestBinding b(1);
  b.initialize();  // ESPHOME_WINS boot push of 0
  r.p.run_chip();
  b.matter_writes.clear();

  for (int i = 1; i <= 50; i++)
    b.entity_changed(i);
  CHECK_EQ(r.p.chip_queue.size(), (size_t) 1);  // one CHIP work item for the whole burst
  r.p.run_chip();
  CHECK_EQ(b.matter_writes, std::vector<int>{50});
  CHECK(b.echo_seen_in_write);
  CHECK(!MatterBindingBase::is_echo(1));
  CHECK_EQ(r.p.legacy_guard_depth, 0);

  // A change racing with the CHIP copy schedules one more item instead of being lost.
  b.entity_changed(51);
  CHECK_EQ(r.p.chip_queue.size(), (size_t) 1);
  b.on_write = [&]() {
    host_set_role(HostRole::MAIN);
    b.entity_changed(52);  // simulates the main loop running during the write
    host_set_role(HostRole::CHIP);
  };
  r.p.run_chip_one();
  b.on_write = nullptr;
  CHECK_EQ(r.p.chip_queue.size(), (size_t) 1);
  r.p.run_chip();
  CHECK_EQ(b.matter_writes.back(), 52);
}

TEST(inbound_during_pending_push_skips_then_reconciles) {
  Rig r;
  TestBinding b(1);
  b.initialize();
  r.p.run_chip();
  b.matter_writes.clear();

  b.entity_changed(10);        // push queued with the old value 10
  b.controller_write(77);      // controller write lands before the push runs
  CHECK_EQ(b.inbound_seq(), 1u);
  CHECK_EQ(b.applied_seq(), 0u);
  r.p.run_chip();              // push is stale: must not overwrite the controller's 77
  CHECK(b.matter_writes.empty());
  r.p.run_main();              // apply, then reconcile
  CHECK_EQ(b.applied, std::vector<int>{77});
  CHECK_EQ(b.applied_seq(), 1u);
  r.p.run_chip();
  CHECK_EQ(b.matter_writes, std::vector<int>{77});  // fresh state, never the stale 10
}

TEST(skipped_push_reconciles_even_without_policy) {
  Rig r;
  BindingPolicy pol;
  pol.reconcile_after_apply = false;
  TestBinding b(1, pol);
  b.initialize();
  r.p.run_chip();
  b.matter_writes.clear();

  // Applied write, policy off: no push.
  b.controller_write(5);
  r.p.settle();
  CHECK(b.matter_writes.empty());

  // A skipped push must still be re-sent after the apply.
  b.entity_changed(9);
  b.controller_write(6);
  r.p.settle();
  CHECK_EQ(b.matter_writes, std::vector<int>{6});
}

TEST(controller_writes_merge_and_apply_once) {
  Rig r;
  TestBinding b(1);
  b.initialize();
  r.p.settle();
  b.controller_write(1);
  b.controller_write(2);
  b.controller_write(3);
  CHECK_EQ(r.p.main_queue.size(), (size_t) 1);  // one apply for the burst
  r.p.settle();
  CHECK_EQ(b.applied, std::vector<int>{3});
  CHECK_EQ(b.applied_seq(), 3u);
}

TEST(refused_command_reconciles_to_esphome_truth) {
  Rig r;
  TestBinding b(1);
  b.entity = 0;
  b.initialize();
  r.p.settle();
  b.matter_writes.clear();

  b.refuse = true;
  b.controller_write(1);  // controller sets 1 in the store, entity refuses
  r.p.settle();
  CHECK_EQ(b.applied, std::vector<int>{1});
  CHECK_EQ(b.matter_writes, std::vector<int>{0});  // Matter restored to ESPHome's value
}

TEST(settle_waits_for_entity_publish) {
  Rig r;
  BindingPolicy pol;
  pol.settle_ms = 100;
  TestBinding b(1, pol);
  b.initialize();
  r.p.settle();
  b.matter_writes.clear();

  b.publish_later_on = &r.p;
  b.publish_delay = 40;
  b.controller_write(7);
  r.p.settle();
  CHECK(b.matter_writes.empty());  // no immediate reconcile
  CHECK(b.is_settling());
  r.p.advance(39);
  r.p.settle();
  CHECK(b.matter_writes.empty());
  r.p.advance(1);  // entity publishes at 40 ms
  r.p.settle();
  CHECK_EQ(b.matter_writes, std::vector<int>{7});
  CHECK(!b.is_settling());
  r.p.advance(200);  // the settle timer must not push again
  r.p.settle();
  CHECK_EQ(b.matter_writes.size(), (size_t) 1);
}

TEST(settle_expiry_reconciles_when_entity_never_publishes) {
  Rig r;
  BindingPolicy pol;
  pol.settle_ms = 100;
  TestBinding b(1, pol);
  b.entity = 3;
  b.initialize();
  r.p.settle();
  b.matter_writes.clear();

  b.refuse = true;
  b.controller_write(9);
  r.p.settle();
  r.p.advance(99);
  r.p.settle();
  CHECK(b.matter_writes.empty());
  CHECK(b.is_settling());
  r.p.advance(1);
  r.p.settle();
  CHECK_EQ(b.matter_writes, std::vector<int>{3});
  CHECK(!b.is_settling());
}

TEST(settle_window_restarts_on_new_apply) {
  Rig r;
  BindingPolicy pol;
  pol.settle_ms = 100;
  TestBinding b(1, pol);
  b.initialize();
  r.p.settle();
  b.matter_writes.clear();
  b.refuse = true;

  b.controller_write(1);
  r.p.settle();
  r.p.advance(60);
  b.controller_write(2);  // second apply opens a new window; the first timer is stale
  r.p.settle();
  r.p.advance(60);        // first window's timer fires here and must be ignored
  r.p.settle();
  CHECK(b.matter_writes.empty());
  r.p.advance(40);
  r.p.settle();
  CHECK_EQ(b.matter_writes.size(), (size_t) 1);
}

TEST(echo_is_scoped_per_endpoint) {
  Rig r;
  TestBinding a(1), c(2);
  a.watch = c.watch = true;
  a.other_ep = 2;
  a.initialize();
  c.initialize();
  r.p.settle();
  a.matter_writes.clear();

  // While `a` writes endpoint 1, a callback for endpoint 1 is an echo and one
  // for endpoint 2 (a scene / group side effect) is a real update.
  a.on_write = [&]() {
    r.p.fire_attribute(1, CL, AT, tp::bool_val(true));
    r.p.fire_attribute(2, CL, AT, tp::bool_val(true));
  };
  a.entity_changed(4);
  r.p.run_chip();
  CHECK(a.echo_seen_in_write);
  CHECK(!a.other_ep_echo_seen);
  CHECK(a.attr_events.empty());
  CHECK_EQ(c.attr_events, std::vector<int>{1});

  // Outside the scope both are delivered.
  a.on_write = nullptr;
  r.p.fire_attribute(1, CL, AT, tp::bool_val(false));
  host_set_role(HostRole::MAIN);
  CHECK_EQ(a.attr_events, std::vector<int>{0});
}

TEST(nested_scopes_and_overflow_stay_balanced) {
  Rig r;
  host_set_role(HostRole::CHIP);
  {
    MatterBindingBase::PushScope s1(1);
    {
      MatterBindingBase::PushScope s2(1);
      MatterBindingBase::PushScope s3(2);
      CHECK(MatterBindingBase::is_echo(1));
      CHECK(MatterBindingBase::is_echo(2));
    }
    CHECK(MatterBindingBase::is_echo(1));
    CHECK(!MatterBindingBase::is_echo(2));
  }
  CHECK(!MatterBindingBase::is_echo(1));
  {
    std::vector<std::unique_ptr<MatterBindingBase::PushScope>> many;
    for (uint16_t ep = 10; ep < 20; ep++)
      many.push_back(std::make_unique<MatterBindingBase::PushScope>(ep));
    CHECK(MatterBindingBase::is_echo(99));  // overflow: everything counts as echo
    while (!many.empty())
      many.pop_back();
  }
  CHECK(!MatterBindingBase::is_echo(10));
  CHECK(!MatterBindingBase::is_echo(99));
  CHECK_EQ(r.p.legacy_guard_depth, 0);
}

TEST(schedule_failure_retries_and_keeps_latest) {
  Rig r;
  TestBinding b(1);
  b.initialize();
  r.p.settle();
  b.matter_writes.clear();

  r.p.fail_schedule = 3;
  b.entity_changed(1);
  CHECK(r.p.chip_queue.empty());
  CHECK(b.push_pending());          // not dropped
  b.entity_changed(2);              // coalesces into the retry
  CHECK_EQ(r.p.timers.size(), (size_t) 1);
  r.p.advance(50);
  r.p.advance(50);
  CHECK(r.p.chip_queue.empty());
  r.p.advance(50);                  // third retry succeeds
  CHECK_EQ(r.p.chip_queue.size(), (size_t) 1);
  r.p.run_chip();
  CHECK_EQ(b.matter_writes, std::vector<int>{2});
  CHECK(!b.push_pending());
  CHECK_EQ(r.p.schedule_failures, 3);
  bool warned = false;
  for (auto &l : host_stub::log_lines())
    warned |= l.level == 'W' && l.text.find("ScheduleWork failed") != std::string::npos;
  CHECK(warned);
}

TEST(boot_sync_policies) {
  {  // ESPHOME_WINS (default): entity state pushed at boot
    Rig r;
    TestBinding b(1);
    b.entity = 5;
    b.initialize();
    r.p.settle();
    CHECK_EQ(b.matter_writes, std::vector<int>{5});
  }
  {  // MATTER_WINS: stored attribute replayed through on_attribute_, nothing pushed
    Rig r;
    BindingPolicy pol;
    pol.boot = BootSync::MATTER_WINS;
    TestBinding b(1, pol);
    b.watch = true;
    r.p.store[{1, CL, AT}] = tp::bool_val(true);
    b.initialize();
    r.p.settle();
    CHECK_EQ(b.attr_events, std::vector<int>{1});
    CHECK(b.matter_writes.empty());
  }
  {  // NONE
    Rig r;
    BindingPolicy pol;
    pol.boot = BootSync::NONE;
    TestBinding b(1, pol);
    b.watch = true;
    r.p.store[{1, CL, AT}] = tp::bool_val(true);
    b.initialize();
    r.p.settle();
    CHECK(b.attr_events.empty());
    CHECK(b.matter_writes.empty());
  }
}

TEST(event_queue_overflow_drops_oldest_with_warning) {
  Rig r;
  std::vector<std::pair<int, int>> got;
  MatterEventQueue<int, 4> q([&](uint16_t ep, const int &e) { got.push_back({ep, e}); });
  for (int i = 1; i <= 6; i++)
    q.post(3, i);
  CHECK_EQ(q.dropped(), 2u);
  CHECK_EQ(q.size(), (size_t) 4);
  CHECK_EQ(r.p.chip_queue.size(), (size_t) 1);  // one drain item for six events
  bool warned = false;
  for (auto &l : host_stub::log_lines())
    warned |= l.level == 'W' && l.text.find("dropped oldest") != std::string::npos;
  CHECK(warned);
  r.p.run_chip();
  CHECK_EQ(got.size(), (size_t) 4);
  for (size_t i = 0; i < got.size(); i++) {
    CHECK_EQ(got[i].first, 3);
    CHECK_EQ(got[i].second, (int) i + 3);  // 3,4,5,6 in order; 1 and 2 were dropped
  }
  q.post(3, 7);  // reusable after a drain
  CHECK_EQ(r.p.chip_queue.size(), (size_t) 1);
  r.p.run_chip();
  CHECK_EQ(got.back().second, 7);
}

TEST(event_queue_retries_when_schedule_fails) {
  Rig r;
  int count = 0;
  MatterEventQueue<int, 4> q([&](uint16_t, const int &) { count++; });
  r.p.fail_schedule = 2;
  q.post(1, 1);
  q.post(1, 2);
  CHECK(r.p.chip_queue.empty());
  r.p.advance(50);
  r.p.advance(50);
  CHECK_EQ(r.p.chip_queue.size(), (size_t) 1);
  r.p.run_chip();
  CHECK_EQ(count, 2);  // neither event was lost
}

TEST(thread_asserts_flag_wrong_thread) {
  Rig r;
  TestBinding b(1);
  b.initialize();
  r.p.settle();
  uint32_t before = esphome::matter::thread::host_violation_count();
  host_set_role(HostRole::CHIP);
  b.entity_changed(1);  // request_push on the CHIP thread
  CHECK_EQ(esphome::matter::thread::host_violation_count(), before + 1);
  host_set_role(HostRole::MAIN);
  MatterBindingBase::PushScope s(1);  // PushScope on the main loop
  CHECK_EQ(esphome::matter::thread::host_violation_count(), before + 2);
}

