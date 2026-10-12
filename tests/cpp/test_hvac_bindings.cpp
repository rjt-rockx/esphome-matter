// Host tests for the fan / climate bindings' pure logic on the binding framework
// (components/matter/matter_fan_logic.h, matter_climate_logic.h). The test
// bindings below mirror the structure of MatterFanMapping, MatterClimateMapping
// and MatterClimateFanMapping (same state types, merge, plan and gate functions)
// with a cluster model in place of esp-matter, so the scenarios exercise the
// real decision code and the real framework state machine.
#include "fake_platform.h"
#include "matter_climate_logic.h"
#include "matter_fan_logic.h"
#include "test_framework.h"

using namespace esphome::matter;
using tp::FakePlatform;
using tp::HostRole;
using tp::host_set_role;

namespace {

constexpr uint16_t EP = 1;

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

// ---------------------------------------------------------------------------
// Fan
// ---------------------------------------------------------------------------

// Stand-in for the FanControlCluster object: derives the settings like the real
// cluster does (a speed makes the percent, off is percent 0).
struct FanClusterModel {
  bool automatic{false};
  bool off{true};
  uint8_t percent{0};
  uint8_t speed{0};
  bool rocking{false};
  bool reverse{false};
  uint8_t wind{0};
  uint8_t current_percent{0};
  uint8_t current_speed{0};
};

class TestFan : public MatterBinding<MatterFanOutbound, MatterFanInbound> {
 public:
  static constexpr uint16_t SETTLE_MS = 200;

  TestFan(bool has_speed, int speed_count)
      : MatterBinding<MatterFanOutbound, MatterFanInbound>(
            EP, BindingPolicy{BootSync::NONE, SETTLE_MS, true}),
        has_speed_(has_speed), speed_count_(speed_count) {}

  // ESPHome fan
  bool on{false};
  int speed{1};
  bool oscillating{false};
  bool reverse{false};
  std::string preset;
  std::string wind_natural{"natural"};
  std::string wind_sleep{"sleep"};
  bool has_speed_traits{true};

  // Fan behaviour
  bool refuse{false};
  bool publish_on_perform{true};
  uint32_t publish_delay{0};
  FakePlatform *platform{nullptr};

  FanClusterModel cluster;
  int settings_writes{0};
  std::vector<std::string> log;
  int applies{0};

  bool auto_active{false}, auto_on{false};
  int auto_speed{0};

  // ESPHome side: the fan changed and publishes.
  void esphome_changed() { request_push(); }

  // Controller write: mutate the cluster (CHIP thread), then submit its settled
  // state like the attribute watch does.
  void controller(const std::function<void(FanClusterModel &)> &mutate) {
    host_set_role(HostRole::CHIP);
    mutate(cluster);
    submit_cluster_state();
    host_set_role(HostRole::MAIN);
  }
  void submit_cluster_state() {
    MatterFanInbound in;
    in.has_settings = true;
    in.settings = read_cluster_state();
    submit_inbound(in);
  }
  MatterFanState read_cluster_state() const {
    MatterFanState s;
    s.has_speed_info = !cluster.automatic;
    if (s.has_speed_info) {
      s.percent = cluster.percent;
      s.off = cluster.percent == 0 || cluster.off;
      if (has_speed_)
        s.speed = cluster.speed;
    }
    return s;
  }
  // Step command (CHIP, controller's command: not an echo).
  void step(bool increase, bool wrap, bool lowest_off) {
    host_set_role(HostRole::CHIP);
    int current = (!cluster.automatic && !cluster.off) ? cluster.speed : 0;
    int target = fan_step_target(current, speed_count_, increase, wrap, lowest_off);
    if (target != current) {
      cluster.speed = static_cast<uint8_t>(target);
      cluster.off = target == 0;
      cluster.percent = static_cast<uint8_t>(target * 100 / speed_count_);
      submit_cluster_state();
    }
    host_set_role(HostRole::MAIN);
  }
  // WindSetting delegate (CHIP).
  void wind_changed(uint8_t bits) {
    host_set_role(HostRole::CHIP);
    if (!MatterBindingBase::is_echo(EP)) {
      MatterFanInbound in;
      in.has_wind = true;
      in.wind_natural = bits & 1;
      in.wind_sleep = bits & 2;
      submit_inbound(in);
    }
    host_set_role(HostRole::MAIN);
  }

 protected:
  void setup_() override {}

  MatterFanOutbound capture_() override {
    MatterFanOutbound o;
    o.on = on;
    o.speed = speed;
    o.oscillating = oscillating;
    o.reverse = reverse;
    o.wind = fan_wind_bits(preset, wind_natural, wind_sleep);
    o.keep_auto = auto_active && o.on == auto_on && o.speed == auto_speed;
    if (!o.keep_auto)
      auto_active = false;
    return o;
  }

  void write_matter_(const MatterFanOutbound &s) override {
    auto cur = fan_current(s, has_speed_, speed_count_);
    if (fan_settings_need_write(read_cluster_state(), s, has_speed_, speed_count_)) {
      settings_writes++;
      cluster.automatic = false;
      if (has_speed_) {
        cluster.speed = cur.speed;
        cluster.percent = static_cast<uint8_t>(cur.speed * 100 / speed_count_);
      } else {
        cluster.percent = cur.percent;
      }
      cluster.off = cluster.percent == 0;
    }
    cluster.current_percent = cur.percent;
    cluster.current_speed = cur.speed;
    if (cluster.wind != s.wind) {
      cluster.wind = s.wind;
      wind_changed(s.wind);  // the real cluster calls the delegate synchronously
    }
    cluster.rocking = s.oscillating;
    cluster.reverse = s.reverse;
  }

  void apply_inbound_(const MatterFanInbound &in) override {
    applies++;
    if (in.has_settings)
      apply_settings(in.settings);
    if (in.has_wind)
      apply_wind(in.wind_natural, in.wind_sleep);
  }
  void merge_inbound_(MatterFanInbound &acc, const MatterFanInbound &in) override {
    fan_merge_inbound(acc, in);
  }

  void publish() {
    if (!publish_on_perform) {
      platform->main_after(publish_delay, [this]() { request_push(); });
      return;
    }
    request_push();
  }

  void apply_settings(const MatterFanState &state) {
    auto plan = fan_plan_settings(state, on, speed, oscillating, reverse, has_speed_);
    if (plan.changed && !refuse) {
      if (plan.set_state)
        on = plan.state;
      if (plan.set_speed)
        speed = plan.speed;
      if (plan.set_oscillating)
        oscillating = plan.oscillating;
      if (plan.set_direction)
        reverse = plan.reverse;
      if (plan.set_state && plan.state)
        preset.clear();  // a speed change clears the preset in ESPHome
      publish();
    }
    auto_active = !state.has_speed_info;
    auto_on = on;
    auto_speed = speed;
  }

  void apply_wind(bool natural, bool sleep) {
    auto plan = fan_plan_wind(natural, sleep, preset, wind_natural, wind_sleep, on, has_speed_traits);
    switch (plan.action) {
      case MatterWindAction::Preset:
        log.push_back("preset:" + plan.preset);
        preset = plan.preset;
        publish();
        break;
      case MatterWindAction::ClearBySpeed:
        log.push_back("clear");
        preset.clear();
        publish();
        break;
      case MatterWindAction::NoMatch:
        log.push_back("nomatch");
        break;
      case MatterWindAction::FanOff:
        log.push_back("fanoff");
        break;
      case MatterWindAction::NoSpeed:
        log.push_back("nospeed");
        break;
      case MatterWindAction::None:
        break;
    }
  }

  bool has_speed_;
  int speed_count_;
};

}  // namespace

TEST(fan_controller_fanmode_write_while_esphome_publishes) {
  Rig r;
  TestFan f(true, 3);
  f.platform = &r.p;
  f.on = true;
  f.speed = 1;
  f.initialize();
  f.esphome_changed();
  r.p.settle();
  CHECK_EQ(f.cluster.speed, 1);
  f.settings_writes = 0;

  // ESPHome publishes speed 2 (push queued) ...
  f.speed = 2;
  f.esphome_changed();
  // ... and before it runs the controller writes FanMode High (speed 3).
  f.controller([](FanClusterModel &c) {
    c.off = false;
    c.speed = 3;
    c.percent = 100;
  });
  r.p.run_chip();  // the stale push (speed 2) must not overwrite the controller's 3
  CHECK_EQ(f.cluster.speed, 3);
  CHECK_EQ(f.settings_writes, 0);
  r.p.settle();
  CHECK_EQ(f.speed, 3);  // applied to the fan
  CHECK_EQ(f.cluster.speed, 3);
  CHECK_EQ(f.cluster.current_speed, 3);
  CHECK_EQ(f.cluster.percent, 100);
  CHECK_EQ(f.settings_writes, 0);  // the cluster already mapped to the fan: untouched
  r.p.advance(1000);
  r.p.settle();
  CHECK_EQ(f.cluster.speed, 3);
}

TEST(fan_repeated_state_publish_does_not_clobber_pending_write) {
  Rig r;
  TestFan f(true, 3);
  f.platform = &r.p;
  f.on = true;
  f.speed = 2;
  f.initialize();
  f.esphome_changed();
  r.p.settle();

  // A state callback that repeats the old state (the old snapshot gate's case).
  f.esphome_changed();
  f.controller([](FanClusterModel &c) {
    c.off = false;
    c.speed = 1;
    c.percent = 33;
  });
  r.p.settle();
  CHECK_EQ(f.speed, 1);
  CHECK_EQ(f.cluster.speed, 1);
  CHECK_EQ(f.cluster.current_speed, 1);
}

TEST(fan_refused_request_reverts_cluster_after_settle) {
  Rig r;
  TestFan f(true, 3);
  f.platform = &r.p;
  f.on = true;
  f.speed = 1;
  f.initialize();
  f.esphome_changed();
  r.p.settle();
  f.settings_writes = 0;

  f.refuse = true;
  f.controller([](FanClusterModel &c) {
    c.speed = 3;
    c.percent = 100;
  });
  r.p.settle();
  CHECK_EQ(f.settings_writes, 0);  // waiting for the fan to publish
  CHECK(f.is_settling());
  r.p.advance(TestFan::SETTLE_MS);
  r.p.settle();
  CHECK_EQ(f.settings_writes, 1);
  CHECK_EQ(f.cluster.speed, 1);  // back to what the fan does
  CHECK_EQ(f.cluster.current_speed, 1);
}

TEST(fan_slow_publish_is_not_preempted_by_the_old_state) {
  Rig r;
  TestFan f(true, 3);
  f.platform = &r.p;
  f.on = false;
  f.initialize();
  f.esphome_changed();
  r.p.settle();
  f.settings_writes = 0;

  f.publish_on_perform = false;
  f.publish_delay = 150;  // under the settle window
  f.controller([](FanClusterModel &c) {
    c.off = false;
    c.speed = 2;
    c.percent = 66;
  });
  r.p.settle();
  r.p.advance(149);
  r.p.settle();
  CHECK_EQ(f.settings_writes, 0);  // the old (off) state never reached the cluster
  CHECK_EQ(f.cluster.speed, 2);
  r.p.advance(1);
  r.p.settle();
  CHECK_EQ(f.cluster.current_speed, 2);
  CHECK_EQ(f.settings_writes, 0);
  r.p.advance(1000);
  r.p.settle();
  CHECK_EQ(f.cluster.speed, 2);
  CHECK_EQ(f.settings_writes, 0);
}

TEST(fan_percent_only_fan_keeps_the_controllers_percent) {
  Rig r;
  TestFan f(false, 1);
  f.platform = &r.p;
  f.initialize();
  f.esphome_changed();
  r.p.settle();

  f.controller([](FanClusterModel &c) {
    c.off = false;
    c.percent = 50;
  });
  r.p.settle();
  CHECK(f.on);
  CHECK_EQ(f.cluster.percent, 50);  // not renormalised to 100
  CHECK_EQ(f.cluster.current_percent, 100);

  // ESPHome turns the fan off: the setting follows.
  f.on = false;
  f.esphome_changed();
  r.p.settle();
  CHECK_EQ(f.cluster.percent, 0);
}

TEST(fan_auto_is_left_alone_until_the_fan_changes) {
  Rig r;
  TestFan f(true, 3);
  f.platform = &r.p;
  f.on = true;
  f.speed = 2;
  f.initialize();
  f.esphome_changed();
  r.p.settle();
  f.settings_writes = 0;

  f.controller([](FanClusterModel &c) { c.automatic = true; });
  r.p.settle();
  r.p.advance(1000);
  r.p.settle();
  CHECK(f.cluster.automatic);  // ESPHome has no Auto: Matter keeps it
  CHECK_EQ(f.settings_writes, 0);
  CHECK_EQ(f.speed, 2);

  f.speed = 3;  // the fan changes on its own
  f.esphome_changed();
  r.p.settle();
  CHECK(!f.cluster.automatic);
  CHECK_EQ(f.cluster.speed, 3);
}

TEST(fan_step_applies_and_wraps) {
  Rig r;
  TestFan f(true, 3);
  f.platform = &r.p;
  f.on = true;
  f.speed = 2;
  f.initialize();
  f.esphome_changed();
  r.p.settle();

  f.step(true, false, true);
  r.p.settle();
  CHECK_EQ(f.speed, 3);
  CHECK_EQ(f.cluster.current_speed, 3);

  f.step(true, false, true);  // at the top without wrap: nothing to do
  r.p.settle();
  CHECK_EQ(f.speed, 3);

  f.step(true, true, true);  // wrap to Off
  r.p.settle();
  CHECK(!f.on);
  CHECK(f.cluster.off);

  f.step(false, false, false);  // off + decrease, lowest not off: stays off
  r.p.settle();
  CHECK(!f.on);

  // Two steps of one burst merge to the newest settled cluster state.
  f.step(true, false, true);
  f.step(true, false, true);
  CHECK_EQ(f.applies > 0, true);
  r.p.settle();
  CHECK(f.on);
  CHECK_EQ(f.speed, 2);
}

TEST(fan_wind_echo_is_dropped_but_controller_wind_is_applied) {
  Rig r;
  TestFan f(true, 3);
  f.platform = &r.p;
  f.on = true;
  f.initialize();
  f.esphome_changed();
  r.p.settle();

  // ESPHome activates the natural preset: the push sets WindSetting, whose
  // delegate callback runs inside the PushScope and is an echo.
  f.preset = "natural";
  f.esphome_changed();
  r.p.settle();
  CHECK_EQ(f.cluster.wind, 1);
  CHECK_EQ(f.applies, 0);

  // A controller sets sleep wind: the preset switches, WindSetting follows.
  f.wind_changed(2);
  r.p.settle();
  CHECK_EQ(f.preset, std::string("sleep"));
  CHECK_EQ(f.cluster.wind, 2);

  // Both bits: the one that is not active wins.
  f.wind_changed(3);
  r.p.settle();
  CHECK_EQ(f.preset, std::string("natural"));

  // Clearing resets the preset (through a speed change in ESPHome).
  f.wind_changed(0);
  r.p.settle();
  CHECK(f.preset.empty());
  CHECK_EQ(f.cluster.wind, 0);
}

TEST(fan_wind_requests_the_fan_cannot_take_revert) {
  Rig r;
  TestFan f(true, 3);
  f.platform = &r.p;
  f.on = false;
  f.initialize();
  f.esphome_changed();
  r.p.settle();

  // Fan off: presets are only set on a running fan.
  f.cluster.wind = 1;  // the controller's write is in the cluster
  f.wind_changed(1);
  r.p.settle();
  r.p.advance(TestFan::SETTLE_MS);
  r.p.settle();
  CHECK_EQ(f.log.back(), std::string("fanoff"));
  CHECK_EQ(f.cluster.wind, 0);  // reconciled back from the fan

  f.on = true;
  f.wind_sleep.clear();  // no sleep preset on this fan
  f.cluster.wind = 2;
  f.wind_changed(2);
  r.p.settle();
  CHECK_EQ(f.log.back(), std::string("nomatch"));
  r.p.advance(TestFan::SETTLE_MS);
  r.p.settle();
  CHECK_EQ(f.cluster.wind, 0);
}

TEST(fan_inbound_merge_keeps_settings_and_wind) {
  MatterFanInbound acc, a, b, c;
  a.has_settings = true;
  a.settings.speed = 1;
  b.has_wind = true;
  b.wind_natural = true;
  c.has_settings = true;
  c.settings.speed = 3;
  fan_merge_inbound(acc, a);
  fan_merge_inbound(acc, b);
  fan_merge_inbound(acc, c);
  CHECK(acc.has_settings);
  CHECK(acc.has_wind);
  CHECK_EQ(acc.settings.speed, 3);
  CHECK(acc.wind_natural);
}

TEST(fan_settings_gate_and_plan_tables) {
  MatterFanOutbound on2;
  on2.on = true;
  on2.speed = 2;
  MatterFanState cs;
  cs.has_speed_info = true;
  cs.off = false;
  cs.speed = 2;
  CHECK(!fan_settings_need_write(cs, on2, true, 3));
  cs.speed = 3;
  CHECK(fan_settings_need_write(cs, on2, true, 3));
  cs.off = true;
  CHECK(fan_settings_need_write(cs, on2, true, 3));
  // Auto
  cs.has_speed_info = false;
  CHECK(fan_settings_need_write(cs, on2, true, 3));
  on2.keep_auto = true;
  CHECK(!fan_settings_need_write(cs, on2, true, 3));

  // Plans: off never uses set_speed(0), on always passes the speed.
  MatterFanState want_off;
  want_off.has_speed_info = true;
  want_off.off = true;
  auto p = fan_plan_settings(want_off, true, 2, false, false, true);
  CHECK(p.changed && p.set_state && !p.state && !p.set_speed);
  MatterFanState want_on;
  want_on.has_speed_info = true;
  want_on.off = false;
  want_on.speed = 3;
  p = fan_plan_settings(want_on, false, 1, false, false, true);
  CHECK(p.changed && p.state && p.set_speed && p.speed == 3);
  p = fan_plan_settings(want_on, true, 3, false, false, true);
  CHECK(!p.changed);
  MatterFanState automatic;  // no speed info: only rock / direction can change
  automatic.has_rock = true;
  automatic.rocking = true;
  p = fan_plan_settings(automatic, true, 2, false, false, true);
  CHECK(p.changed && !p.set_state && p.set_oscillating && p.oscillating);

  CHECK_EQ(fan_current(on2, true, 3).percent, 66);
  MatterFanOutbound off;
  CHECK_EQ(fan_current(off, true, 3).percent, 0);
  CHECK_EQ(fan_current(on2, false, 1).percent, 100);
}

// ---------------------------------------------------------------------------
// Climate
// ---------------------------------------------------------------------------

namespace {

constexpr uint32_t THERMOSTAT = 0x0201;
constexpr uint32_t AT_MODE = 0x001C;
constexpr uint32_t AT_HEAT = 0x0012;
constexpr uint32_t AT_COOL = 0x0011;

constexpr uint8_t MODE_OFF = 0;
constexpr uint8_t MODE_AUTO = 1;
constexpr uint8_t MODE_COOL = 3;
constexpr uint8_t MODE_HEAT = 4;

struct ClimateOut {
  uint8_t mode{0};
  int16_t heating{0};
  int16_t cooling{0};
};

esp_matter_attr_val_t i16_val(int16_t v) {
  esp_matter_attr_val_t a;
  a.type = ESP_MATTER_VAL_TYPE_INT16;
  a.val.i16 = v;
  return a;
}
esp_matter_attr_val_t u8_val(uint8_t v) {
  esp_matter_attr_val_t a;
  a.type = ESP_MATTER_VAL_TYPE_ENUM8;
  a.val.u8 = v;
  return a;
}

// A single point climate with the real merge / accept / clamp functions.
class TestClimate : public MatterBinding<ClimateOut, MatterClimateInbound> {
 public:
  static constexpr uint16_t SETTLE_MS = 500;

  explicit TestClimate(SetpointLimits limits)
      : MatterBinding<ClimateOut, MatterClimateInbound>(
            EP, BindingPolicy{BootSync::ESPHOME_WINS, SETTLE_MS, true}),
        limits_(limits) {}

  // ESPHome climate (Heat/Cool, single target)
  uint8_t mode{MODE_HEAT};
  int16_t target{2000};
  bool refuse_mode{false};
  bool device_clamps{false};  // the device clamps a target to its own range
  int16_t device_max{2800};
  FakePlatform *platform{nullptr};
  uint32_t publish_delay{0};
  bool spec_heating{true}, spec_cooling{true};

  // Observations
  int applies{0};
  int echoes_dropped_check{0};
  std::vector<std::string> events;

  void controller_write(FakePlatform &p, uint32_t attr, const esp_matter_attr_val_t &v) {
    host_set_role(HostRole::CHIP);
    p.store[{EP, THERMOSTAT, attr}] = v;
    p.fire_attribute(EP, THERMOSTAT, attr, v);
    host_set_role(HostRole::MAIN);
  }
  void esphome_changed() { request_push(); }

 protected:
  void setup_() override {
    watch_attribute(THERMOSTAT, AT_MODE);
    watch_attribute(THERMOSTAT, AT_HEAT);
    watch_attribute(THERMOSTAT, AT_COOL);
  }
  ClimateOut capture_() override {
    ClimateOut o;
    o.mode = mode;
    // Clamped to the absolute limits (the cluster rejects reports outside).
    o.heating = limits_.clamp_heating(target);
    o.cooling = limits_.clamp_cooling(target);
    return o;
  }
  void write_matter_(const ClimateOut &s) override {
    auto *pf = binding_platform();
    auto v = u8_val(s.mode);
    pf->write_attribute(EP, THERMOSTAT, AT_MODE, v, true);
    auto h = i16_val(s.heating);
    pf->write_attribute(EP, THERMOSTAT, AT_HEAT, h, false);  // update(): runs the callbacks
    auto c = i16_val(s.cooling);
    pf->write_attribute(EP, THERMOSTAT, AT_COOL, c, false);
  }
  void on_attribute_(uint32_t, uint32_t attr, const esp_matter_attr_val_t &v) override {
    MatterClimateInbound in;
    if (attr == AT_MODE) {
      in.has_mode = true;
      in.system_mode = v.val.u8;
    } else if (attr == AT_HEAT) {
      in.has_heating = true;
      in.heating_centi = v.val.i16;
    } else if (attr == AT_COOL) {
      in.has_cooling = true;
      in.cooling_centi = v.val.i16;
    } else {
      return;
    }
    submit_inbound(in);
  }
  void merge_inbound_(MatterClimateInbound &acc, const MatterClimateInbound &in) override {
    climate_merge_inbound(acc, in);
  }
  void apply_inbound_(const MatterClimateInbound &in) override {
    applies++;
    bool changed = false;
    if (in.has_mode && !refuse_mode && in.system_mode != mode) {
      mode = in.system_mode;
      events.push_back("mode");
      changed = true;
    }
    for (int i = 0; i < 2; i++) {
      bool heating = i == 0;
      if (!(heating ? in.has_heating : in.has_cooling))
        continue;
      SetpointTarget active = mode == MODE_HEAT   ? SetpointTarget::Heat
                              : mode == MODE_COOL ? SetpointTarget::Cool
                              : mode == MODE_AUTO ? SetpointTarget::None
                                                  : SetpointTarget::Neutral;
      if (!climate_accepts_setpoint(heating, active, spec_heating, spec_cooling)) {
        events.push_back("refused");
        continue;
      }
      int16_t centi = heating ? in.heating_centi : in.cooling_centi;
      if (device_clamps && centi > device_max)
        centi = device_max;
      if (centi != target) {
        target = centi;
        events.push_back("target");
        changed = true;
      }
    }
    if (changed) {
      if (publish_delay == 0)
        request_push();
      else
        platform->main_after(publish_delay, [this]() { request_push(); });
    }
  }

  SetpointLimits limits_;
};

SetpointLimits limits() {
  SetpointLimits l;
  l.heat_min = l.cool_min = 1000;
  l.heat_max = l.cool_max = 3000;
  return l;
}

uint8_t stored_mode(FakePlatform &p) { return p.store[{EP, THERMOSTAT, AT_MODE}].val.u8; }

int16_t stored(FakePlatform &p, uint32_t attr) {
  return p.store[{EP, THERMOSTAT, attr}].val.i16;
}

}  // namespace

TEST(climate_mode_and_setpoint_burst_both_apply_in_order) {
  Rig r;
  TestClimate c(limits());
  c.mode = MODE_OFF;
  c.target = 2000;
  c.initialize();
  r.p.settle();

  // A client writes SystemMode then the heating setpoint back to back.
  c.controller_write(r.p, AT_MODE, u8_val(MODE_HEAT));
  c.controller_write(r.p, AT_HEAT, i16_val(2300));
  CHECK_EQ(r.p.main_queue.size(), (size_t) 1);  // one apply for the burst
  r.p.settle();
  CHECK_EQ(c.applies, 1);
  CHECK_EQ(c.mode, MODE_HEAT);
  CHECK_EQ(c.target, 2300);  // judged against the new mode, not the old one
  CHECK_EQ(stored(r.p, AT_HEAT), 2300);
}

TEST(climate_setpoint_refused_in_auto_reconciles_to_climate_state) {
  Rig r;
  TestClimate c(limits());
  c.mode = MODE_AUTO;
  c.target = 2100;
  c.initialize();
  r.p.settle();

  c.controller_write(r.p, AT_HEAT, i16_val(2500));  // not adjustable in Auto
  r.p.settle();
  CHECK(c.target == 2100);
  CHECK_EQ(stored(r.p, AT_HEAT), 2500);  // the controller's value, until the settle ends
  r.p.advance(TestClimate::SETTLE_MS);
  r.p.settle();
  CHECK_EQ(stored(r.p, AT_HEAT), 2100);  // reverted to the climate's own state
  CHECK_EQ(c.events.back(), std::string("refused"));
}

TEST(climate_setpoint_clamped_by_the_device_is_reconciled_on_publish) {
  Rig r;
  TestClimate c(limits());
  c.platform = &r.p;
  c.mode = MODE_COOL;
  c.target = 2400;
  c.device_clamps = true;
  c.device_max = 2800;
  c.initialize();
  r.p.settle();

  c.controller_write(r.p, AT_COOL, i16_val(3000));
  r.p.settle();
  CHECK_EQ(c.target, 2800);
  CHECK_EQ(stored(r.p, AT_COOL), 2800);  // the climate published the clamped value
  r.p.advance(2000);
  r.p.settle();
  CHECK_EQ(stored(r.p, AT_COOL), 2800);
}

TEST(climate_slow_publish_is_waited_for_before_reverting) {
  Rig r;
  TestClimate c(limits());
  c.platform = &r.p;
  c.mode = MODE_HEAT;
  c.target = 2000;
  c.publish_delay = 300;  // under the settle window
  c.initialize();
  r.p.settle();

  c.controller_write(r.p, AT_HEAT, i16_val(2400));
  r.p.settle();
  r.p.advance(299);
  r.p.settle();
  CHECK_EQ(stored(r.p, AT_HEAT), 2400);  // never flipped back to 2000
  r.p.advance(1);
  r.p.settle();
  CHECK_EQ(stored(r.p, AT_HEAT), 2400);
  r.p.advance(2000);
  r.p.settle();
  CHECK_EQ(stored(r.p, AT_HEAT), 2400);
}

TEST(climate_pushed_setpoint_is_clamped_and_its_callbacks_are_echoes) {
  Rig r;
  TestClimate c(limits());
  c.mode = MODE_HEAT;
  c.target = 5000;  // above the absolute maximum
  c.initialize();
  r.p.settle();
  // write_matter_() uses update() for the setpoints, which fires the watches;
  // they are inside the PushScope and must not become controller requests.
  CHECK_EQ(c.applies, 0);
  CHECK_EQ(stored(r.p, AT_HEAT), 3000);
  CHECK_EQ(stored(r.p, AT_COOL), 3000);

  c.target = 500;
  c.esphome_changed();
  r.p.settle();
  CHECK_EQ(c.applies, 0);
  CHECK_EQ(stored(r.p, AT_HEAT), 1000);
}

TEST(climate_refused_mode_reverts) {
  Rig r;
  TestClimate c(limits());
  c.mode = MODE_HEAT;
  c.refuse_mode = true;
  c.initialize();
  r.p.settle();
  c.controller_write(r.p, AT_MODE, u8_val(MODE_COOL));
  r.p.settle();
  r.p.advance(TestClimate::SETTLE_MS);
  r.p.settle();
  CHECK_EQ(stored_mode(r.p), MODE_HEAT);
}

TEST(climate_inbound_merge_and_limits_tables) {
  MatterClimateInbound acc, a, b, c;
  a.has_mode = true;
  a.system_mode = 4;
  b.has_heating = true;
  b.heating_centi = 2000;
  c.has_mode = true;
  c.system_mode = 3;
  climate_merge_inbound(acc, a);
  climate_merge_inbound(acc, b);
  climate_merge_inbound(acc, c);
  CHECK(acc.has_mode && acc.has_heating && !acc.has_cooling && !acc.has_on_off);
  CHECK_EQ(acc.system_mode, 3);
  CHECK_EQ(acc.heating_centi, 2000);

  // AutoMode limits: heating max a dead band below the cooling max.
  SetpointLimits l;
  l.heat_min = 1000;
  l.heat_max = 2800;
  l.cool_min = 1200;
  l.cool_max = 3000;
  CHECK_EQ(l.clamp_heating(3000), 2800);
  CHECK_EQ(l.clamp_cooling(1000), 1200);
  CHECK_EQ(l.clamp_cooling(2000), 2000);

  // Single point climate: which setpoint belongs to the active mode.
  CHECK(climate_accepts_setpoint(true, SetpointTarget::Heat, true, true));
  CHECK(!climate_accepts_setpoint(false, SetpointTarget::Heat, true, true));
  CHECK(climate_accepts_setpoint(false, SetpointTarget::Cool, true, true));
  CHECK(!climate_accepts_setpoint(true, SetpointTarget::None, true, true));
  CHECK(climate_accepts_setpoint(true, SetpointTarget::Neutral, true, false));
  CHECK(!climate_accepts_setpoint(true, SetpointTarget::Neutral, true, true));
  CHECK(climate_accepts_setpoint(false, SetpointTarget::Neutral, false, true));
}

// ---------------------------------------------------------------------------
// Climate fan: RockSetting fallback
// ---------------------------------------------------------------------------

TEST(climate_fan_rock_to_swing_fallbacks) {
  const uint8_t v = swing_bit(SwingKind::Vertical);
  const uint8_t h = swing_bit(SwingKind::Horizontal);
  const uint8_t both = swing_bit(SwingKind::Both);
  SwingKind k = SwingKind::Off;

  CHECK(rock_to_swing(0, 0, k) && k == SwingKind::Off);
  CHECK(rock_to_swing(ROCK_BIT_UP_DOWN, v | h, k) && k == SwingKind::Vertical);
  CHECK(rock_to_swing(ROCK_BIT_LEFT_RIGHT, v | h, k) && k == SwingKind::Horizontal);
  CHECK(rock_to_swing(ROCK_BIT_LEFT_RIGHT | ROCK_BIT_UP_DOWN, v | h | both, k) && k == SwingKind::Both);
  // A climate that only swings both ways takes any rock request.
  CHECK(rock_to_swing(ROCK_BIT_UP_DOWN, both, k) && k == SwingKind::Both);
  CHECK(rock_to_swing(ROCK_BIT_LEFT_RIGHT, both, k) && k == SwingKind::Both);
  // Both requested but only vertical + horizontal separately: not possible.
  CHECK(!rock_to_swing(ROCK_BIT_LEFT_RIGHT | ROCK_BIT_UP_DOWN, v | h, k));
  // Horizontal requested on a vertical-only climate.
  CHECK(!rock_to_swing(ROCK_BIT_LEFT_RIGHT, v, k));

  CHECK_EQ(rock_support_of(both), ROCK_BIT_LEFT_RIGHT | ROCK_BIT_UP_DOWN);
  CHECK_EQ(rock_support_of(v), ROCK_BIT_UP_DOWN);
  CHECK_EQ(rock_support_of(h), ROCK_BIT_LEFT_RIGHT);
  CHECK_EQ(rock_support_of(0), 0);
  CHECK_EQ(swing_to_rock(SwingKind::Both), ROCK_BIT_LEFT_RIGHT | ROCK_BIT_UP_DOWN);
  CHECK_EQ(swing_to_rock(SwingKind::Off), 0);
}

namespace {

struct RockState {
  uint8_t rock{0};
};

// Climate fan rock handling: delegate -> submit_inbound, apply via rock_to_swing,
// reconcile on refusal.
class TestRockBinding : public MatterBinding<RockState, MatterClimateFanInbound> {
 public:
  explicit TestRockBinding(uint8_t swing_support)
      : MatterBinding<RockState, MatterClimateFanInbound>(
            EP, BindingPolicy{BootSync::ESPHOME_WINS, 300, true}),
        swing_support_(swing_support) {}

  SwingKind swing{SwingKind::Off};
  uint8_t cluster_rock{0};
  std::vector<std::string> log;

  void controller_rock(uint8_t rock) {
    host_set_role(HostRole::CHIP);
    cluster_rock = rock;
    if (!MatterBindingBase::is_echo(EP)) {  // OnRockSettingChanged
      MatterClimateFanInbound in;
      in.has_rock = true;
      in.rock = rock;
      submit_inbound(in);
    }
    host_set_role(HostRole::MAIN);
  }

 protected:
  void setup_() override {}
  RockState capture_() override { return RockState{swing_to_rock(swing)}; }
  void write_matter_(const RockState &s) override {
    uint8_t value = s.rock & rock_support_of(swing_support_);
    if (cluster_rock != value) {
      cluster_rock = value;
      // SetRockSetting() calls OnRockSettingChanged(): an echo here.
      if (!MatterBindingBase::is_echo(EP))
        log.push_back("echo-leaked");
    }
  }
  void merge_inbound_(MatterClimateFanInbound &a, const MatterClimateFanInbound &i) override {
    climate_fan_merge_inbound(a, i);
  }
  void apply_inbound_(const MatterClimateFanInbound &in) override {
    if (!in.has_rock)
      return;
    SwingKind kind;
    if (!rock_to_swing(in.rock, swing_support_, kind)) {
      log.push_back("unsupported");
      return;
    }
    swing = kind;
    log.push_back("swing");
    request_push();
  }

  uint8_t swing_support_;
};

}  // namespace

TEST(climate_fan_rock_fallback_and_unsupported_reconcile) {
  Rig r;
  // Both-only climate: a left/right request becomes Both and the cluster follows.
  TestRockBinding both(swing_bit(SwingKind::Both));
  both.initialize();
  r.p.settle();
  both.controller_rock(ROCK_BIT_LEFT_RIGHT);
  r.p.settle();
  CHECK(both.swing == SwingKind::Both);
  CHECK_EQ(both.cluster_rock, ROCK_BIT_LEFT_RIGHT | ROCK_BIT_UP_DOWN);
  for (auto &s : both.log)
    CHECK(s != "echo-leaked");
}

TEST(climate_fan_unsupported_rock_reverts_after_settle) {
  Rig r;
  TestRockBinding vertical_only(swing_bit(SwingKind::Vertical));
  vertical_only.initialize();
  r.p.settle();
  vertical_only.controller_rock(ROCK_BIT_UP_DOWN);
  r.p.settle();
  CHECK(vertical_only.swing == SwingKind::Vertical);

  // Horizontal is not supported (and masked by RockSupport in the real cluster,
  // so the write model keeps the request): the climate stays vertical and the
  // reconcile puts the cluster back.
  vertical_only.controller_rock(ROCK_BIT_LEFT_RIGHT);
  r.p.settle();
  CHECK_EQ(vertical_only.log.back(), std::string("unsupported"));
  CHECK_EQ(vertical_only.cluster_rock, ROCK_BIT_LEFT_RIGHT);
  r.p.advance(300);
  r.p.settle();
  CHECK_EQ(vertical_only.cluster_rock, ROCK_BIT_UP_DOWN);
  CHECK(vertical_only.swing == SwingKind::Vertical);
}

TEST(climate_fan_inbound_merge_keeps_mode_and_rock) {
  MatterClimateFanInbound acc, a, b, c;
  a.has_mode = true;
  a.mode = 2;
  b.has_rock = true;
  b.rock = 1;
  c.has_mode = true;
  c.mode = 3;
  climate_fan_merge_inbound(acc, a);
  climate_fan_merge_inbound(acc, b);
  climate_fan_merge_inbound(acc, c);
  CHECK(acc.has_mode && acc.has_rock);
  CHECK_EQ(acc.mode, 3);
  CHECK_EQ(acc.rock, 1);
}
