// Host tests for the light binding (components/matter/matter_light_binding.h): OnOff, level and colour
// between an ESPHome light and the OnOff / LevelControl / ColorControl clusters. The light entity and the
// CHIP cluster calls are faked; the FakePlatform drives the CHIP queue, the main loop and the clock.
#include "fake_platform.h"
#include "matter_light_binding.h"
#include "test_framework.h"

#include <cmath>
#include <string>
#include <vector>

using namespace esphome::matter;
using tp::FakePlatform;
using tp::HostRole;
using tp::host_set_role;

namespace {

constexpr uint16_t EP = 3;
using namespace esphome::matter::light_ids;

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

template<typename F> void as_chip(F &&f) {
  host_set_role(HostRole::CHIP);
  f();
  host_set_role(HostRole::MAIN);
}

esp_matter_attr_val_t u8_val(uint8_t v) { return esp_matter_uint8(v); }
esp_matter_attr_val_t u16_val(uint16_t v) { return esp_matter_uint16(v); }
esp_matter_attr_val_t level_val(uint8_t v) { return esp_matter_nullable_uint8(nullable<uint8_t>(v)); }

class TestLight : public MatterLightBinding {
 public:
  explicit TestLight(uint16_t ep) : MatterLightBinding(ep) {
    caps.has_level = true;
    caps.has_xy = true;
    caps.has_hs = true;
    caps.has_color = true;
    caps.color_temperature_range = MatterColorTemperatureRange{153, 500};
  }

  // ---- the fake entity
  MatterLightCapabilities caps;
  LightEntityView view;
  LightEntityView applied;
  std::vector<LightRequest> requests;
  // The light publishes inside perform() (synchronously), like a light without a delayed publish.
  bool publish_in_perform{true};
  bool effect_running{false};
  bool effect_starts{true};
  int effect_starts_count{0};
  int effect_stops_count{0};
  // CHIP calls, in order with the attribute writes (index into FakePlatform::attribute_writes).
  struct ChipCall {
    std::string what;
    size_t writes_before;
    uint8_t level;
  };
  std::vector<ChipCall> chip_calls;
  FakePlatform *platform{nullptr};

  // ESPHome side: the light changed.
  void entity_changed() { on_entity_published_(); }
  void set_rgb(float r, float g, float b, float brightness = 1.0f) {
    view.on = true;
    view.brightness = brightness;
    view.active = LightActiveColor::COLOR;
    view.red = r;
    view.green = g;
    view.blue = b;
  }
  void set_ct(float mireds, float brightness = 1.0f) {
    view.on = true;
    view.brightness = brightness;
    view.active = LightActiveColor::CT;
    view.color_temperature = mireds;
  }
  void submit_cmd(const MatterLightCommand &c) {
    as_chip([&] { submit_command(c); });
  }

  uint32_t light_retries() const { return retries_[G_LIGHT]; }
  void clear_loop_requested() { color_loop_requested_ = false; }
  void force_push() { request_push(); }
  bool window_light() const { return light_window_active(); }
  bool window_color() const { return color_window_active(); }

 protected:
  LightEntityView entity_view_() override { return view; }
  void perform_(const LightRequest &r) override {
    requests.push_back(r);
    if (r.state.has_value())
      view.on = *r.state;
    if (r.brightness.has_value())
      view.brightness = *r.brightness;
    if (r.color == LightRequest::Color::CT) {
      view.active = LightActiveColor::CT;
      view.color_temperature = r.color_temperature;
    } else if (r.color == LightRequest::Color::RGB) {
      view.active = LightActiveColor::COLOR;
      view.red = r.red;
      view.green = r.green;
      view.blue = r.blue;
    }
    if (publish_in_perform)
      on_entity_published_();
  }
  void start_color_loop_effect_() override {
    effect_starts_count++;
    effect_running = effect_starts;
  }
  void stop_color_loop_effect_hook_() override {
    effect_stops_count++;
    effect_running = false;
  }
  bool color_loop_effect_running_() override { return effect_running; }
  void remember_applied_entity_() override { applied = view; }
  bool remote_matches_applied_() override {
    return applied.on == view.on && applied.brightness == view.brightness && applied.active == view.active &&
           applied.red == view.red && applied.green == view.green && applied.blue == view.blue &&
           applied.color_temperature == view.color_temperature;
  }
  bool probe_capabilities_() override {
    capabilities_ = caps;
    return true;
  }
  void listen_entity_() override {}
  void register_commands_() override {}
  void chip_stop_light_transition_(uint8_t level) override {
    chip_calls.push_back({"stop_light", platform ? platform->attribute_writes.size() : 0, level});
  }
  void chip_stop_color_transitions_() override {
    chip_calls.push_back({"stop_color", platform ? platform->attribute_writes.size() : 0, 0});
  }
  void deactivate_chip_color_loop_() override {
    chip_calls.push_back({"deactivate_loop", platform ? platform->attribute_writes.size() : 0, 0});
  }
};

// Persisted Matter state of a light: on, level 127, xy mode.
void seed_store(FakePlatform &p, bool on = true, uint8_t level = 127) {
  p.store[{EP, ON_OFF_CLUSTER, ON_OFF}] = esp_matter_bool(on);
  p.store[{EP, LEVEL_CLUSTER, CURRENT_LEVEL}] = level_val(level);
  p.store[{EP, LEVEL_CLUSTER, MIN_LEVEL}] = u8_val(1);
  p.store[{EP, LEVEL_CLUSTER, MAX_LEVEL}] = u8_val(254);
  p.store[{EP, COLOR_CLUSTER, COLOR_MODE}] = esp_matter_enum8(1);
  p.store[{EP, COLOR_CLUSTER, CURRENT_X}] = u16_val(32768);
  p.store[{EP, COLOR_CLUSTER, CURRENT_Y}] = u16_val(21845);
  p.store[{EP, COLOR_CLUSTER, CURRENT_HUE}] = u8_val(0);
  p.store[{EP, COLOR_CLUSTER, CURRENT_SATURATION}] = u8_val(254);
}

struct Started {
  Rig r;
  TestLight l{EP};
  explicit Started(bool seed = true) {
    if (seed)
      seed_store(r.p);
    l.platform = &r.p;
    l.initialize();
    r.p.settle();
  }
};

// A controller (or CHIP's own transition) writes an attribute: store + the watch callbacks.
void matter_writes(FakePlatform &p, uint32_t cluster, uint32_t attribute, esp_matter_attr_val_t v) {
  as_chip([&] {
    p.store[{EP, cluster, attribute}] = v;
    p.fire_attribute(EP, cluster, attribute, v);
  });
}

size_t writes_to(FakePlatform &p, uint32_t cluster, uint32_t attribute) {
  size_t n = 0;
  for (auto &w : p.attribute_writes)
    if (w.cl == cluster && w.at == attribute)
      n++;
  return n;
}

esp_matter_attr_val_t last_write(FakePlatform &p, uint32_t cluster, uint32_t attribute) {
  for (size_t i = p.attribute_writes.size(); i-- > 0;)
    if (p.attribute_writes[i].cl == cluster && p.attribute_writes[i].at == attribute)
      return p.attribute_writes[i].value;
  return esp_matter_attr_val_t();
}

MatterLightCommand level_command(uint8_t level, uint32_t transition_ms) {
  MatterLightCommand c;
  c.level = level;
  c.transition_ms = transition_ms;
  c.light_group = true;
  return c;
}

}  // namespace

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

TEST(light_boot_applies_the_stored_state_as_one_call) {
  Started s;
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
  const LightRequest &req = s.l.requests[0];
  CHECK(req.state.has_value() && *req.state);
  CHECK(req.brightness.has_value());
  CHECK_NEAR(*req.brightness, 127 / 254.0, 0.001);
  CHECK(req.color == LightRequest::Color::RGB);
  // Matter wins at boot: nothing is written back, not even by the publish of the call.
  CHECK(s.r.p.attribute_writes.empty());
  CHECK_EQ(s.r.p.legacy_guard_depth, 0);
}

TEST(light_boot_without_color_control_reads_only_on_off_and_level) {
  Rig r;
  TestLight l(EP);
  l.caps = MatterLightCapabilities{};
  l.caps.has_level = true;
  seed_store(r.p, true, 40);
  l.platform = &r.p;
  l.initialize();
  r.p.settle();
  CHECK_EQ(l.requests.size(), (size_t) 1);
  CHECK(l.requests[0].state.has_value() && *l.requests[0].state);
  CHECK_NEAR(*l.requests[0].brightness, 40 / 254.0, 0.001);
  CHECK(l.requests[0].color == LightRequest::Color::NONE);
}

// ---------------------------------------------------------------------------
// Matter -> ESPHome
// ---------------------------------------------------------------------------

TEST(light_on_off_and_level_of_one_burst_are_one_call) {
  Started s;
  s.l.requests.clear();
  s.l.view.on = false;
  s.l.view.brightness = 0.1f;
  s.l.applied = s.l.view;

  matter_writes(s.r.p, ON_OFF_CLUSTER, ON_OFF, esp_matter_bool(true));
  matter_writes(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL, level_val(200));
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
  CHECK(s.l.requests[0].state.has_value() && *s.l.requests[0].state);
  CHECK_NEAR(*s.l.requests[0].brightness, 200 / 254.0, 0.001);
  CHECK_EQ(s.l.requests[0].transition_ms, (uint32_t) 0);
}

TEST(light_controller_write_is_not_pushed_back) {
  Started s;
  s.l.requests.clear();
  s.l.view.on = false;
  matter_writes(s.r.p, ON_OFF_CLUSTER, ON_OFF, esp_matter_bool(true));
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
  // The light publishes again later (a transition, or a delayed publish): same values, no push.
  s.l.entity_changed();
  s.r.p.settle();
  CHECK(s.r.p.attribute_writes.empty());
  CHECK(!s.l.push_pending());

  // A real change from the ESPHome side is pushed.
  s.l.view.brightness = 0.3f;
  s.l.entity_changed();
  s.r.p.settle();
  CHECK(writes_to(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL) == 1);
}

TEST(light_null_current_level_is_ignored) {
  Started s;
  s.l.requests.clear();
  matter_writes(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL,
                esp_matter_nullable_uint8(nullable<uint8_t>()));
  s.r.p.settle();
  CHECK(s.l.requests.empty());
}

TEST(light_color_attributes_are_read_together) {
  Started s;
  s.l.requests.clear();
  // CurrentX and CurrentY change one after the other within one CHIP work item: one read, both new.
  as_chip([&] {
    s.r.p.store[{EP, COLOR_CLUSTER, CURRENT_X}] = u16_val(10000);
    s.r.p.fire_attribute(EP, COLOR_CLUSTER, CURRENT_X, u16_val(10000));
    s.r.p.store[{EP, COLOR_CLUSTER, CURRENT_Y}] = u16_val(40000);
    s.r.p.fire_attribute(EP, COLOR_CLUSTER, CURRENT_Y, u16_val(40000));
  });
  CHECK_EQ(s.r.p.chip_queue.size(), (size_t) 1);
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
  CHECK(s.l.requests[0].color == LightRequest::Color::RGB);
  float r, g, b;
  CHECK(esphome::matter::conversion::from_matter::color(10000, 40000, r, g, b));
  CHECK_NEAR(s.l.requests[0].red, r, 0.001);
  CHECK_NEAR(s.l.requests[0].green, g, 0.001);
}

TEST(light_color_mode_switch_to_color_temperature) {
  Started s;
  s.l.requests.clear();
  s.r.p.store[{EP, COLOR_CLUSTER, COLOR_TEMPERATURE_MIREDS}] = u16_val(300);
  matter_writes(s.r.p, COLOR_CLUSTER, COLOR_MODE, esp_matter_enum8(2));
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
  CHECK(s.l.requests[0].color == LightRequest::Color::CT);
  CHECK_NEAR(s.l.requests[0].color_temperature, 300, 0.01);

  // Back to HS.
  s.r.p.store[{EP, COLOR_CLUSTER, CURRENT_HUE}] = u8_val(100);
  s.r.p.store[{EP, COLOR_CLUSTER, CURRENT_SATURATION}] = u8_val(200);
  matter_writes(s.r.p, COLOR_CLUSTER, COLOR_MODE, esp_matter_enum8(0));
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), (size_t) 2);
  CHECK(s.l.requests[1].color == LightRequest::Color::RGB);
}

TEST(light_rate_based_steps_follow_each_other_with_a_transition) {
  Started s;
  s.l.requests.clear();
  s.l.applied = s.l.view;
  matter_writes(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL, level_val(100));
  s.r.p.settle();
  s.r.p.advance(100);
  matter_writes(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL, level_val(120));
  s.r.p.settle();
  s.r.p.advance(2000);
  matter_writes(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL, level_val(140));
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), (size_t) 3);
  CHECK_EQ(s.l.requests[0].transition_ms, (uint32_t) 0);
  CHECK_EQ(s.l.requests[1].transition_ms, (uint32_t) 100);
  CHECK_EQ(s.l.requests[2].transition_ms, (uint32_t) 0);
}

// ---------------------------------------------------------------------------
// ESPHome -> Matter
// ---------------------------------------------------------------------------

TEST(light_local_change_reports_state_inside_the_scope) {
  Started s;
  s.l.set_rgb(1.0f, 0.0f, 0.0f, 0.5f);
  s.l.entity_changed();
  s.r.p.settle();
  CHECK_EQ(writes_to(s.r.p, ON_OFF_CLUSTER, ON_OFF), (size_t) 1);
  CHECK(last_write(s.r.p, ON_OFF_CLUSTER, ON_OFF).val.b);
  CHECK_EQ(writes_to(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL), (size_t) 1);
  CHECK_EQ(last_write(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL).val.u8, (uint8_t) 127);
  // xy and hs are both reported, the mode last (xy is preferred).
  CHECK_EQ(writes_to(s.r.p, COLOR_CLUSTER, CURRENT_X), (size_t) 1);
  CHECK_EQ(writes_to(s.r.p, COLOR_CLUSTER, CURRENT_HUE), (size_t) 1);
  CHECK_EQ(writes_to(s.r.p, COLOR_CLUSTER, COLOR_MODE), (size_t) 1);
  CHECK_EQ(last_write(s.r.p, COLOR_CLUSTER, COLOR_MODE).val.u8, (uint8_t) 1);
  CHECK(s.r.p.attribute_writes.back().at == COLOR_MODE);
  for (auto &w : s.r.p.attribute_writes) {
    CHECK(w.echo);
    CHECK(w.report);
  }
  CHECK_EQ(s.r.p.legacy_guard_depth, 0);
}

TEST(light_unchanged_colour_does_not_rewrite_the_colour_mode) {
  Started s;
  s.l.set_rgb(0.0f, 1.0f, 0.0f);
  s.l.entity_changed();
  s.r.p.settle();
  CHECK_EQ(writes_to(s.r.p, COLOR_CLUSTER, COLOR_MODE), (size_t) 1);
  // Only the brightness changes: ColorMode (which a controller may have switched to HS) is left alone.
  s.l.view.brightness = 0.4f;
  s.l.entity_changed();
  s.r.p.settle();
  CHECK_EQ(writes_to(s.r.p, COLOR_CLUSTER, COLOR_MODE), (size_t) 1);
  CHECK_EQ(writes_to(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL), (size_t) 2);
  // Another colour is reported again, a colour temperature switches the mode.
  s.l.set_ct(350.0f);
  s.l.entity_changed();
  s.r.p.settle();
  CHECK_EQ(writes_to(s.r.p, COLOR_CLUSTER, COLOR_TEMPERATURE_MIREDS), (size_t) 1);
  CHECK_EQ(last_write(s.r.p, COLOR_CLUSTER, COLOR_MODE).val.u8, (uint8_t) 2);
}

TEST(light_burst_of_changes_costs_one_push_and_keeps_the_colour) {
  Started s;
  s.l.set_rgb(1.0f, 0.0f, 0.0f);
  s.l.entity_changed();
  // The colour changes, then only the brightness, before CHIP ran: the colour report must not be lost.
  s.l.view.brightness = 0.6f;
  s.l.entity_changed();
  s.l.view.brightness = 0.8f;
  s.l.entity_changed();
  CHECK_EQ(s.r.p.chip_queue.size(), (size_t) 1);
  s.r.p.settle();
  CHECK_EQ(writes_to(s.r.p, ON_OFF_CLUSTER, ON_OFF), (size_t) 1);
  CHECK_EQ(last_write(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL).val.u8, (uint8_t) 203);
  CHECK_EQ(writes_to(s.r.p, COLOR_CLUSTER, CURRENT_X), (size_t) 1);
  CHECK_EQ(writes_to(s.r.p, COLOR_CLUSTER, COLOR_MODE), (size_t) 1);
}

TEST(light_level_is_clamped_to_min_and_max_level) {
  Started s;
  s.r.p.store[{EP, LEVEL_CLUSTER, MIN_LEVEL}] = u8_val(10);
  s.r.p.store[{EP, LEVEL_CLUSTER, MAX_LEVEL}] = u8_val(100);
  s.l.view.on = true;
  s.l.view.brightness = 1.0f;
  s.l.entity_changed();
  s.r.p.settle();
  CHECK_EQ(last_write(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL).val.u8, (uint8_t) 100);
  s.l.view.brightness = 0.0f;
  s.l.entity_changed();
  s.r.p.settle();
  CHECK_EQ(last_write(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL).val.u8, (uint8_t) 10);
}

TEST(light_push_captured_before_a_controller_write_is_not_applied_over_it) {
  Started s;
  s.l.requests.clear();
  s.r.p.attribute_writes.clear();
  // ESPHome changes the brightness, and at the same time a controller turns the light off.
  s.l.view.brightness = 0.3f;
  s.l.entity_changed();
  matter_writes(s.r.p, ON_OFF_CLUSTER, ON_OFF, esp_matter_bool(false));
  // CHIP runs the push first: it is older than the write and is skipped.
  s.r.p.run_chip();
  CHECK(s.r.p.attribute_writes.empty());
  s.r.p.settle();
  // The write was applied, and the reconcile reports the light's state.
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
  CHECK(!s.l.view.on);
  CHECK_EQ(writes_to(s.r.p, ON_OFF_CLUSTER, ON_OFF), (size_t) 1);
  CHECK(!last_write(s.r.p, ON_OFF_CLUSTER, ON_OFF).val.b);
}

// ---------------------------------------------------------------------------
// Commands with a transition
// ---------------------------------------------------------------------------

TEST(light_command_transition_ignores_chips_steps_until_the_window_ends) {
  Started s;
  s.l.requests.clear();
  s.l.view.on = true;
  s.l.view.brightness = 0.1f;
  s.l.applied = s.l.view;

  s.l.submit_cmd(level_command(200, 2000));
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
  CHECK_NEAR(*s.l.requests[0].brightness, 200 / 254.0, 0.001);
  CHECK_EQ(s.l.requests[0].transition_ms, (uint32_t) 2000);
  CHECK(s.l.window_light());
  // The call's own publishes are echoes.
  CHECK(s.r.p.attribute_writes.empty());

  // CHIP walks to the target in steps and writes every one: ignored.
  matter_writes(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL, level_val(40));
  matter_writes(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL, level_val(80));
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), (size_t) 1);

  // At the end of the window the light is settled on what CHIP ended up with.
  s.r.p.store[{EP, LEVEL_CLUSTER, CURRENT_LEVEL}] = level_val(200);
  s.r.p.advance(2400);
  s.r.p.settle();
  CHECK(s.l.window_light());
  s.r.p.advance(200);
  s.r.p.settle();
  CHECK(!s.l.window_light());
  CHECK_EQ(s.l.requests.size(), (size_t) 2);
  CHECK(s.l.requests[1].state.has_value());  // settling is forced
  CHECK_EQ(s.l.requests[1].transition_ms, (uint32_t) 0);
  CHECK_NEAR(*s.l.requests[1].brightness, 200 / 254.0, 0.001);
}

TEST(light_window_waits_while_chip_is_still_in_transition) {
  Started s;
  s.l.requests.clear();
  s.l.submit_cmd(level_command(200, 1000));
  s.r.p.settle();
  s.r.p.store[{EP, LEVEL_CLUSTER, LEVEL_REMAINING_TIME}] = u16_val(5);
  s.r.p.advance(1500);
  s.r.p.settle();
  CHECK(s.l.window_light());
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
  s.r.p.advance(MatterLightBinding::SETTLE_RETRY_MS);
  s.r.p.settle();
  CHECK(s.l.window_light());
  s.r.p.store[{EP, LEVEL_CLUSTER, LEVEL_REMAINING_TIME}] = u16_val(0);
  s.r.p.store[{EP, LEVEL_CLUSTER, CURRENT_LEVEL}] = level_val(190);
  s.r.p.advance(MatterLightBinding::SETTLE_RETRY_MS);
  s.r.p.settle();
  CHECK(!s.l.window_light());
  CHECK_EQ(s.l.requests.size(), (size_t) 2);
  CHECK_NEAR(*s.l.requests[1].brightness, 190 / 254.0, 0.001);
}

TEST(light_attribute_updates_before_a_command_are_discarded_by_it) {
  Started s;
  s.l.requests.clear();
  s.l.view.on = true;
  s.l.view.brightness = 0.5f;
  s.l.applied = s.l.view;
  as_chip([&] {
    s.r.p.store[{EP, ON_OFF_CLUSTER, ON_OFF}] = esp_matter_bool(false);
    s.r.p.fire_attribute(EP, ON_OFF_CLUSTER, ON_OFF, esp_matter_bool(false));
    s.l.submit_command(level_command(200, 500));
  });
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
  CHECK(!s.l.requests[0].state.has_value());
  CHECK(s.l.view.on);
}

TEST(light_change_from_esphome_ends_the_window_and_stops_chip) {
  Started s;
  s.l.requests.clear();
  s.l.submit_cmd(level_command(200, 5000));
  s.r.p.settle();
  CHECK(s.l.window_light());
  s.r.p.attribute_writes.clear();

  // The user changes the light during the transition: it wins, CHIP stops where it is.
  s.l.view.brightness = 0.2f;
  s.l.entity_changed();
  CHECK(!s.l.window_light());
  s.r.p.settle();
  CHECK_EQ(s.l.chip_calls.size(), (size_t) 1);
  CHECK(s.l.chip_calls[0].what == "stop_light");
  CHECK_EQ(s.l.chip_calls[0].level, (uint8_t) 51);
  CHECK_EQ(s.l.chip_calls[0].writes_before, (size_t) 0);  // before the reports
  CHECK_EQ(last_write(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL).val.u8, (uint8_t) 51);
  // The old timer does nothing.
  s.r.p.advance(6000);
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
  CHECK_EQ(s.l.chip_calls.size(), (size_t) 1);
}

TEST(light_stop_command_settles_on_chips_state) {
  Started s;
  s.l.requests.clear();
  s.l.submit_cmd(level_command(250, 5000));
  s.r.p.settle();
  CHECK(s.l.window_light());
  // CHIP stopped at 80.
  s.r.p.store[{EP, LEVEL_CLUSTER, CURRENT_LEVEL}] = level_val(80);
  MatterLightCommand stop;
  stop.stop_light = true;
  s.l.submit_cmd(stop);
  s.r.p.settle();
  CHECK(!s.l.window_light());
  CHECK_EQ(s.l.requests.size(), (size_t) 2);
  CHECK_NEAR(*s.l.requests[1].brightness, 80 / 254.0, 0.001);
  CHECK_EQ(s.l.requests[1].transition_ms, (uint32_t) 0);
}

TEST(light_on_command_uses_on_level_only_when_turning_on) {
  Started s;
  s.l.requests.clear();
  s.l.view.on = false;
  s.l.view.brightness = 0.9f;
  s.l.applied = s.l.view;
  MatterLightCommand on;
  on.on = true;
  on.level = 100;
  on.level_if_turning_on = true;
  on.transition_ms = 300;
  on.light_group = true;
  s.l.submit_cmd(on);
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
  CHECK(*s.l.requests[0].state);
  CHECK_NEAR(*s.l.requests[0].brightness, 100 / 254.0, 0.001);
  CHECK_EQ(s.l.requests[0].transition_ms, (uint32_t) 300);

  // On while already on: OnLevel is not applied.
  s.l.view.brightness = 0.9f;
  s.l.applied = s.l.view;
  s.r.p.advance(1000);
  s.r.p.settle();  // the window ends: the light is settled on the stored state
  size_t before = s.l.requests.size();
  s.l.view.brightness = 0.9f;
  s.l.applied = s.l.view;
  s.l.submit_cmd(on);
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), before);
}

TEST(light_color_command_opens_only_the_colour_window) {
  Started s;
  s.l.requests.clear();
  MatterLightCommand c;
  c.color = MatterLightColorUpdate{MatterLightColorUpdate::Kind::XY, 10000, 40000};
  c.transition_ms = 1000;
  c.color_group = true;
  s.l.submit_cmd(c);
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
  CHECK(s.l.requests[0].color == LightRequest::Color::RGB);
  CHECK_EQ(s.l.requests[0].transition_ms, (uint32_t) 1000);
  CHECK(s.l.window_color());
  CHECK(!s.l.window_light());
  // Level steps of someone else are not ignored, colour steps are.
  s.l.view.brightness = 0.1f;
  s.l.applied = s.l.view;
  matter_writes(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL, level_val(200));
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), (size_t) 2);
  matter_writes(s.r.p, COLOR_CLUSTER, CURRENT_X, u16_val(30000));
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), (size_t) 2);
}

// ---------------------------------------------------------------------------
// Color loop
// ---------------------------------------------------------------------------

TEST(light_color_loop_effect_ignores_chips_hue_and_reports_after_it_ends) {
  Started s;
  s.l.requests.clear();
  MatterLightCommand start;
  start.color_loop = MatterLightCommand::ColorLoop::START;
  s.l.submit_cmd(start);
  s.l.effect_running = false;
  s.r.p.settle();
  CHECK_EQ(s.l.effect_starts_count, 1);
  CHECK(s.l.color_loop_active());

  // CHIP's loop writes the hue all the time: ignored while the effect runs.
  s.r.p.store[{EP, COLOR_CLUSTER, CURRENT_HUE}] = u8_val(50);
  matter_writes(s.r.p, COLOR_CLUSTER, COLOR_MODE, esp_matter_enum8(0));
  s.r.p.settle();
  CHECK(s.l.requests.empty());

  // While it runs the light's colour is not reported.
  s.l.view.brightness = 0.3f;
  s.l.entity_changed();
  s.r.p.settle();
  CHECK_EQ(writes_to(s.r.p, COLOR_CLUSTER, COLOR_MODE), (size_t) 0);
  CHECK_EQ(writes_to(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL), (size_t) 1);

  // The effect ends (another effect, or the light was turned off).
  s.l.effect_running = false;
  s.l.set_rgb(0.0f, 0.0f, 1.0f, 0.3f);
  s.r.p.advance(250);
  s.r.p.settle();
  CHECK(!s.l.color_loop_active());
  CHECK_EQ(s.l.chip_calls.size(), (size_t) 1);
  CHECK(s.l.chip_calls[0].what == "deactivate_loop");
  CHECK(s.l.window_color());
  // CHIP restores the hue: ignored in the window.
  matter_writes(s.r.p, COLOR_CLUSTER, CURRENT_HUE, u8_val(10));
  s.r.p.settle();
  CHECK(s.l.requests.empty());
  CHECK_EQ(writes_to(s.r.p, COLOR_CLUSTER, COLOR_MODE), (size_t) 0);
  // After the window the light's own colour is reported.
  s.r.p.advance(600);
  s.r.p.settle();
  CHECK(!s.l.window_color());
  CHECK_EQ(writes_to(s.r.p, COLOR_CLUSTER, COLOR_MODE), (size_t) 1);
}

TEST(light_color_loop_that_does_not_start_follows_chip) {
  Started s;
  s.l.requests.clear();
  s.l.effect_starts = false;
  MatterLightCommand start;
  start.color_loop = MatterLightCommand::ColorLoop::START;
  s.l.submit_cmd(start);
  s.r.p.settle();
  CHECK_EQ(s.l.effect_starts_count, 1);
  CHECK(!s.l.color_loop_active());
  // CHIP's steps are applied as usual.
  s.r.p.store[{EP, COLOR_CLUSTER, CURRENT_HUE}] = u8_val(60);
  s.r.p.store[{EP, COLOR_CLUSTER, CURRENT_SATURATION}] = u8_val(254);
  matter_writes(s.r.p, COLOR_CLUSTER, COLOR_MODE, esp_matter_enum8(0));
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
}

// ---------------------------------------------------------------------------
// Queue
// ---------------------------------------------------------------------------

TEST(light_many_commands_in_one_burst_keep_the_newest) {
  Started s;
  s.l.requests.clear();
  as_chip([&] {
    for (int i = 0; i < 12; i++) {
      MatterLightCommand c = level_command(static_cast<uint8_t>(20 + i * 10), 0);
      s.l.submit_command(c);
    }
  });
  s.r.p.settle();
  // The oldest were dropped with a warning; the newest command was applied.
  CHECK(!s.l.requests.empty());
  CHECK_NEAR(s.l.view.brightness, (20 + 11 * 10) / 254.0, 0.001);
  bool warned = false;
  for (auto &line : host_stub::log_lines())
    if (line.level == 'W' && line.text.find("queue full") != std::string::npos)
      warned = true;
  CHECK(warned);
}

TEST(light_change_that_ends_the_color_loop_reports_the_colour_at_once) {
  Started s;
  MatterLightCommand start;
  start.color_loop = MatterLightCommand::ColorLoop::START;
  s.l.submit_cmd(start);
  s.r.p.settle();
  CHECK(s.l.color_loop_active());
  s.r.p.attribute_writes.clear();

  // The user picks another effect: the light publishes, the loop is over.
  s.l.effect_running = false;
  s.l.set_rgb(0.0f, 0.0f, 1.0f, 0.5f);
  s.l.entity_changed();
  s.r.p.settle();
  CHECK(!s.l.color_loop_active());
  CHECK_EQ(s.l.chip_calls.size(), (size_t) 1);
  CHECK(s.l.chip_calls[0].what == "deactivate_loop");
  CHECK_EQ(s.l.chip_calls[0].writes_before, (size_t) 0);  // CHIP's hue is restored before the report
  // Reported at once, and again after CHIP's restore of the hue (the work items may run in any order).
  CHECK_EQ(writes_to(s.r.p, COLOR_CLUSTER, COLOR_MODE), (size_t) 2);
  // CHIP's restore of the hue within the window is ignored.
  s.l.requests.clear();
  matter_writes(s.r.p, COLOR_CLUSTER, CURRENT_HUE, u8_val(10));
  s.r.p.settle();
  CHECK(s.l.requests.empty());
}

// ---------------------------------------------------------------------------
// Review fixes
// ---------------------------------------------------------------------------

TEST(light_overflow_never_loses_the_end_of_a_color_loop) {
  Started s;
  MatterLightCommand start;
  start.color_loop = MatterLightCommand::ColorLoop::START;
  s.l.submit_cmd(start);
  s.r.p.settle();
  CHECK(s.l.color_loop_active());
  s.l.requests.clear();

  // A colour command ends the loop (as handle_command decodes it), followed by many commands and CHIP's
  // attribute writes in the same main loop iteration: the oldest items are dropped.
  s.r.p.store[{EP, COLOR_CLUSTER, CURRENT_X}] = u16_val(12000);
  s.r.p.store[{EP, COLOR_CLUSTER, CURRENT_Y}] = u16_val(30000);
  s.l.clear_loop_requested();
  as_chip([&] {
    MatterLightCommand c;
    c.color = MatterLightColorUpdate{MatterLightColorUpdate::Kind::XY, 12000, 30000};
    c.color_loop = MatterLightCommand::ColorLoop::STOP;
    c.color_group = true;
    c.transition_ms = 0;
    s.l.submit_command(c);
    for (int i = 0; i < 5; i++) {
      s.l.submit_command(level_command(static_cast<uint8_t>(30 + i * 20), 0));
      s.r.p.fire_attribute(EP, LEVEL_CLUSTER, CURRENT_LEVEL, level_val(static_cast<uint8_t>(30 + i * 20)));
    }
  });
  s.r.p.settle();
  CHECK(!s.l.color_loop_active());
  CHECK(s.l.effect_stops_count >= 1);
  CHECK(!s.l.effect_running);
  // The light is settled on the state CHIP ended up with, colour included.
  bool colour_applied = false;
  for (auto &r : s.l.requests)
    if (r.color == LightRequest::Color::RGB)
      colour_applied = true;
  CHECK(colour_applied);
}

TEST(light_dropped_stop_command_still_settles) {
  Started s;
  s.l.submit_cmd(level_command(250, 5000));
  s.r.p.settle();
  CHECK(s.l.window_light());
  s.r.p.store[{EP, LEVEL_CLUSTER, CURRENT_LEVEL}] = level_val(90);
  size_t before = s.l.requests.size();
  // Stop first, then enough colour commands and CHIP writes to push it out of the queue.
  as_chip([&] {
    MatterLightCommand stop;
    stop.stop_light = true;
    s.l.submit_command(stop);
    for (int i = 0; i < 5; i++) {
      MatterLightCommand c;
      c.color = MatterLightColorUpdate{MatterLightColorUpdate::Kind::XY, static_cast<uint16_t>(10000 + i * 1000), 30000};
      c.color_group = true;
      s.l.submit_command(c);
      s.r.p.fire_attribute(EP, ON_OFF_CLUSTER, ON_OFF, esp_matter_bool(true));
    }
  });
  s.r.p.settle();
  CHECK(!s.l.window_light());
  CHECK(s.l.requests.size() > before);
  // Settled on the stored level, not left at the target of the interrupted command.
  CHECK_NEAR(s.l.view.brightness, 90 / 254.0, 0.001);
}

TEST(light_stop_of_a_local_change_does_not_outlive_a_newer_command) {
  Started s;
  s.l.requests.clear();
  s.l.submit_cmd(level_command(200, 5000));
  s.r.p.settle();
  CHECK(s.l.window_light());
  s.r.p.attribute_writes.clear();

  // A local brightness change ends window A; the stop for CHIP is queued at once.
  s.l.view.brightness = 0.2f;
  s.l.entity_changed();
  CHECK(!s.l.window_light());
  // An Off with a transition arrives before CHIP ran any of it (so the push is skipped).
  MatterLightCommand off;
  off.on = false;
  off.transition_ms = 1000;
  off.chip_transition_ms = 1000;
  off.light_group = true;
  s.l.submit_cmd(off);
  s.r.p.settle();

  // The stop ran ahead of the Off (once), the Off was applied and its window is open: nothing replaces it.
  CHECK_EQ(s.l.chip_calls.size(), (size_t) 1);
  CHECK(s.l.chip_calls[0].what == "stop_light");
  CHECK(!s.l.view.on);
  CHECK(s.l.window_light());
  CHECK_EQ(writes_to(s.r.p, ON_OFF_CLUSTER, ON_OFF), (size_t) 0);
  s.r.p.advance(1200);
  s.r.p.settle();
  CHECK_EQ(s.l.chip_calls.size(), (size_t) 1);
}

TEST(light_stop_of_a_local_colour_change_does_not_outlive_a_newer_command) {
  Started s;
  MatterLightCommand c;
  c.color = MatterLightColorUpdate{MatterLightColorUpdate::Kind::XY, 10000, 40000};
  c.transition_ms = 5000;
  c.color_group = true;
  s.l.submit_cmd(c);
  s.r.p.settle();
  CHECK(s.l.window_color());
  s.l.set_rgb(0.0f, 1.0f, 0.0f);
  s.l.entity_changed();
  CHECK(!s.l.window_color());
  MatterLightCommand c2;
  c2.color = MatterLightColorUpdate{MatterLightColorUpdate::Kind::XY, 20000, 20000};
  c2.transition_ms = 1000;
  c2.color_group = true;
  s.l.submit_cmd(c2);
  s.r.p.settle();
  CHECK_EQ(s.l.chip_calls.size(), (size_t) 1);
  CHECK(s.l.chip_calls[0].what == "stop_color");
  CHECK(s.l.window_color());
  // The colour of the light is not reported over the running command.
  CHECK_EQ(writes_to(s.r.p, COLOR_CLUSTER, COLOR_MODE), (size_t) 0);

  // The skipped local colour is superseded by the command, not pending: once the
  // command has settled, a brightness-only change must not write it to Matter.
  s.r.p.store[{EP, COLOR_CLUSTER, CURRENT_X}] = u16_val(20000);
  s.r.p.store[{EP, COLOR_CLUSTER, CURRENT_Y}] = u16_val(20000);
  for (int i = 0; i < 20 && s.l.window_color(); i++) {
    s.r.p.advance(MatterLightBinding::SETTLE_RETRY_MS);
    s.r.p.settle();
  }
  CHECK(!s.l.window_color());
  size_t x_writes = writes_to(s.r.p, COLOR_CLUSTER, CURRENT_X);
  s.l.view.brightness = 0.5f;
  s.l.entity_changed();
  s.r.p.settle();
  CHECK_EQ(writes_to(s.r.p, COLOR_CLUSTER, CURRENT_X), x_writes);
}

TEST(light_stop_move_step_settles_on_chips_colour) {
  Started s;
  s.l.requests.clear();
  MatterLightCommand c;
  c.color = MatterLightColorUpdate{MatterLightColorUpdate::Kind::XY, 10000, 40000};
  c.transition_ms = 5000;
  c.color_group = true;
  s.l.submit_cmd(c);
  s.r.p.settle();
  CHECK(s.l.window_color());
  s.r.p.store[{EP, COLOR_CLUSTER, CURRENT_X}] = u16_val(20000);
  s.r.p.store[{EP, COLOR_CLUSTER, CURRENT_Y}] = u16_val(25000);
  MatterLightCommand stop;
  stop.stop_color = true;
  s.l.submit_cmd(stop);
  s.r.p.settle();
  CHECK(!s.l.window_color());
  CHECK_EQ(s.l.requests.size(), (size_t) 2);
  float r, g, b;
  CHECK(esphome::matter::conversion::from_matter::color(20000, 25000, r, g, b));
  CHECK(s.l.requests[1].color == LightRequest::Color::RGB);
  CHECK_NEAR(s.l.requests[1].red, r, 0.001);
  CHECK_NEAR(s.l.requests[1].green, g, 0.001);
  CHECK_EQ(s.l.requests[1].transition_ms, (uint32_t) 0);
}

TEST(light_color_loop_deactivate_settles_on_chips_hue) {
  Started s;
  MatterLightCommand start;
  start.color_loop = MatterLightCommand::ColorLoop::START;
  s.l.submit_cmd(start);
  s.r.p.settle();
  CHECK(s.l.color_loop_active());
  s.l.requests.clear();
  // ColorLoopSet(deactivate): CHIP restored the hue it had before the loop (HS mode).
  s.r.p.store[{EP, COLOR_CLUSTER, COLOR_MODE}] = esp_matter_enum8(0);
  s.r.p.store[{EP, COLOR_CLUSTER, CURRENT_HUE}] = u8_val(30);
  s.r.p.store[{EP, COLOR_CLUSTER, CURRENT_SATURATION}] = u8_val(254);
  s.l.clear_loop_requested();
  MatterLightCommand stop;
  stop.color_loop = MatterLightCommand::ColorLoop::STOP;
  s.l.submit_cmd(stop);
  s.r.p.settle();
  CHECK(!s.l.color_loop_active());
  CHECK_EQ(s.l.effect_stops_count, 1);
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
  float r, g, b;
  CHECK(esphome::matter::conversion::from_matter::hue_saturation(30, 254, r, g, b));
  CHECK(s.l.requests[0].color == LightRequest::Color::RGB);
  CHECK_NEAR(s.l.requests[0].red, r, 0.001);
  CHECK_NEAR(s.l.requests[0].green, g, 0.001);
}

TEST(light_color_command_ends_the_loop_and_moves_the_colour) {
  Started s;
  MatterLightCommand start;
  start.color_loop = MatterLightCommand::ColorLoop::START;
  s.l.submit_cmd(start);
  s.r.p.settle();
  s.l.requests.clear();
  s.l.clear_loop_requested();
  MatterLightCommand c;
  c.color = MatterLightColorUpdate{MatterLightColorUpdate::Kind::XY, 10000, 40000};
  c.color_loop = MatterLightCommand::ColorLoop::STOP;
  c.transition_ms = 1000;
  c.color_group = true;
  s.l.submit_cmd(c);
  s.r.p.settle();
  CHECK(!s.l.color_loop_active());
  CHECK_EQ(s.l.effect_stops_count, 1);
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
  CHECK(s.l.requests[0].color == LightRequest::Color::RGB);
  CHECK_EQ(s.l.requests[0].transition_ms, (uint32_t) 1000);
  CHECK(s.l.window_color());
}

TEST(light_move_to_level_with_on_off_to_the_minimum_turns_off_without_brightness) {
  Started s;
  s.l.requests.clear();
  s.l.view.on = true;
  s.l.view.brightness = 0.5f;
  s.l.applied = s.l.view;
  MatterLightCommand c;
  c.on = false;
  c.level = 1;  // MinLevel
  c.transition_ms = 500;
  c.light_group = true;
  s.l.submit_cmd(c);
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
  CHECK(s.l.requests[0].state.has_value() && !*s.l.requests[0].state);
  CHECK(!s.l.requests[0].brightness.has_value());  // the light fades out at its brightness
  CHECK_EQ(s.l.requests[0].transition_ms, (uint32_t) 500);
}

TEST(light_enhanced_hue_and_saturation_commands) {
  Rig r;
  TestLight l(EP);
  l.caps.has_enhanced_hue = true;
  l.caps.has_enhanced_color_mode = true;
  seed_store(r.p);
  r.p.store[{EP, COLOR_CLUSTER, ENHANCED_COLOR_MODE}] = esp_matter_enum8(3);
  r.p.store[{EP, COLOR_CLUSTER, ENHANCED_CURRENT_HUE}] = u16_val(21845);
  r.p.store[{EP, COLOR_CLUSTER, CURRENT_SATURATION}] = u8_val(200);
  l.platform = &r.p;
  l.initialize();
  r.p.settle();
  // Boot read in enhanced HS mode.
  CHECK_EQ(l.requests.size(), (size_t) 1);
  float er, eg, eb;
  CHECK(esphome::matter::conversion::from_matter::enhanced_hue_saturation(21845, 200, er, eg, eb));
  CHECK(l.requests[0].color == LightRequest::Color::RGB);
  CHECK_NEAR(l.requests[0].green, eg, 0.001);

  // MoveToSaturation keeps the hue: decoded as ENHANCED_HS with the new saturation.
  l.requests.clear();
  MatterLightCommand c;
  c.color = MatterLightColorUpdate{MatterLightColorUpdate::Kind::ENHANCED_HS, 21845, 100};
  c.transition_ms = 200;
  c.color_group = true;
  l.submit_cmd(c);
  r.p.settle();
  CHECK_EQ(l.requests.size(), (size_t) 1);
  CHECK(esphome::matter::conversion::from_matter::enhanced_hue_saturation(21845, 100, er, eg, eb));
  CHECK_NEAR(l.requests[0].green, eg, 0.001);
  CHECK_NEAR(l.requests[0].red, er, 0.001);
  CHECK_EQ(l.requests[0].transition_ms, (uint32_t) 200);

  // An attribute change in that mode is read as enhanced HS too.
  l.requests.clear();
  r.p.store[{EP, COLOR_CLUSTER, CURRENT_SATURATION}] = u8_val(254);
  matter_writes(r.p, COLOR_CLUSTER, ENHANCED_CURRENT_HUE, u16_val(40000));
  r.p.settle();
  // The window of the command is over by now? No: it is still open, so the step is ignored.
  CHECK(l.requests.empty());
  r.p.advance(1000);
  r.p.settle();
  CHECK(!l.requests.empty());
}

TEST(light_window_gives_up_waiting_for_chip_after_the_retry_limit) {
  Started s;
  s.l.requests.clear();
  s.l.submit_cmd(level_command(200, 0));
  s.r.p.settle();
  s.r.p.store[{EP, LEVEL_CLUSTER, LEVEL_REMAINING_TIME}] = u16_val(1);  // never done
  s.r.p.advance(MatterLightBinding::COMMAND_MARGIN_MS);
  s.r.p.settle();
  for (uint32_t i = 0; i < MatterLightBinding::SETTLE_MAX_RETRIES; i++) {
    CHECK(s.l.window_light());
    s.r.p.advance(MatterLightBinding::SETTLE_RETRY_MS);
    s.r.p.settle();
    if (!s.l.window_light())
      break;
  }
  CHECK(!s.l.window_light());
  CHECK_EQ(s.l.requests.size(), (size_t) 2);  // the command, then the forced settle
}

TEST(light_chip_steps_do_not_leak_when_chip_outlasts_the_nominal_window) {
  Started s;
  s.l.requests.clear();
  s.l.submit_cmd(level_command(200, 3000));
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
  CHECK_EQ(s.l.requests[0].transition_ms, (uint32_t) 3000);

  // CHIP is much slower than the 3 s it was given: it is at 21 and still has 30 deciseconds to go.
  s.r.p.store[{EP, LEVEL_CLUSTER, LEVEL_REMAINING_TIME}] = u16_val(30);
  uint8_t level = 21;
  s.r.p.store[{EP, LEVEL_CLUSTER, CURRENT_LEVEL}] = level_val(level);

  // The nominal time and the margin pass; the CHIP task is busy and has not answered the check yet. A step that
  // arrives in between must not move the light.
  s.r.p.advance(3600);
  level += 4;
  matter_writes(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL, level_val(level));
  s.r.p.run_main();
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
  CHECK(s.l.window_light());

  // Then the check runs: CHIP is not done. The windows stay open across any number of rounds, each with a step.
  for (int i = 0; i < 30; i++) {
    s.r.p.settle();
    level += 4;
    matter_writes(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL, level_val(level));
    s.r.p.run_main();
    s.r.p.advance(MatterLightBinding::SETTLE_RETRY_MS + 10);
    CHECK_EQ(s.l.requests.size(), (size_t) 1);
    CHECK(s.l.window_light());
  }
  CHECK_NEAR(s.l.view.brightness, 200 / 254.0, 0.001);

  // CHIP arrives: one settle, on the final value.
  s.r.p.store[{EP, LEVEL_CLUSTER, LEVEL_REMAINING_TIME}] = u16_val(0);
  s.r.p.store[{EP, LEVEL_CLUSTER, CURRENT_LEVEL}] = level_val(200);
  s.r.p.advance(MatterLightBinding::SETTLE_RETRY_MS + 10);
  s.r.p.settle();
  CHECK(!s.l.window_light());
  CHECK_EQ(s.l.requests.size(), (size_t) 2);
  CHECK_NEAR(*s.l.requests[1].brightness, 200 / 254.0, 0.001);
  CHECK_EQ(s.l.requests[1].transition_ms, (uint32_t) 0);
}

TEST(light_new_command_cancels_the_wait_for_the_old_one) {
  Started s;
  s.l.requests.clear();
  s.l.submit_cmd(level_command(200, 1000));
  s.r.p.settle();
  s.r.p.store[{EP, LEVEL_CLUSTER, LEVEL_REMAINING_TIME}] = u16_val(30);
  s.r.p.advance(1600);  // the idle check of the first command is requested, not served yet
  // A second command arrives before the answer: the answer belongs to the old window and is dropped.
  s.l.submit_cmd(level_command(50, 10000));
  s.r.p.settle();
  size_t n = s.l.requests.size();
  s.r.p.store[{EP, LEVEL_CLUSTER, LEVEL_REMAINING_TIME}] = u16_val(0);
  s.r.p.advance(500);
  s.r.p.settle();
  CHECK(s.l.window_light());  // the second command's window (10 s) is still open
  CHECK_EQ(s.l.requests.size(), n);
}

// ---------------------------------------------------------------------------
// Waiting for CHIP's transition: one wait per attribute group
// ---------------------------------------------------------------------------

namespace {

MatterLightCommand color_command(uint16_t x, uint16_t y, uint32_t transition_ms) {
  MatterLightCommand c;
  c.color = MatterLightColorUpdate{MatterLightColorUpdate::Kind::XY, x, y};
  c.transition_ms = transition_ms;
  c.color_group = true;
  return c;
}

// CHIP is in the middle of a long level transition: RemainingTime is not 0.
void chip_is_busy(FakePlatform &p) {
  p.store[{EP, LEVEL_CLUSTER, LEVEL_REMAINING_TIME}] = u16_val(30);
  p.store[{EP, COLOR_CLUSTER, COLOR_REMAINING_TIME}] = u16_val(0);
}

}  // namespace

TEST(light_step_right_after_the_nominal_time_does_not_leak) {
  Started s;
  s.l.requests.clear();
  s.l.submit_cmd(level_command(200, 3000));
  s.r.p.settle();
  chip_is_busy(s.r.p);
  // The step is processed after the nominal time but before the timer that asks CHIP got to run.
  s.r.p.now += 3501;
  matter_writes(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL, level_val(31));
  s.r.p.run_main();
  CHECK_EQ(s.l.requests.size(), (size_t) 1);
  CHECK(s.l.window_light());
}

TEST(light_colour_command_does_not_cancel_the_level_wait) {
  Started s;
  s.l.requests.clear();
  s.l.submit_cmd(level_command(200, 1000));
  s.r.p.settle();
  chip_is_busy(s.r.p);
  s.r.p.advance(1600);
  s.r.p.settle();  // the level check ran: busy, windows stay open
  CHECK(s.l.window_light());
  // A colour command arrives while the level transition is still waited for.
  s.l.submit_cmd(color_command(10000, 40000, 500));
  s.r.p.settle();
  size_t n = s.l.requests.size();
  // CHIP's late level steps are still ignored.
  matter_writes(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL, level_val(60));
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), n);
  CHECK(s.l.window_light());
  s.r.p.advance(MatterLightBinding::SETTLE_RETRY_MS + 10);
  matter_writes(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL, level_val(64));
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), n);
  CHECK(s.l.window_light());
}

TEST(light_level_stop_leaves_the_colour_wait_alone_and_it_still_settles) {
  Started s;
  s.l.requests.clear();
  MatterLightCommand both = level_command(200, 1000);
  both.color = MatterLightColorUpdate{MatterLightColorUpdate::Kind::XY, 10000, 40000};
  both.color_group = true;
  s.l.submit_cmd(both);
  s.r.p.settle();
  chip_is_busy(s.r.p);
  s.r.p.store[{EP, COLOR_CLUSTER, COLOR_REMAINING_TIME}] = u16_val(20);
  s.r.p.advance(1600);
  s.r.p.settle();  // both groups waited for, both busy
  CHECK(s.l.window_light());
  CHECK(s.l.window_color());

  // Stop the level transition only.
  s.r.p.store[{EP, LEVEL_CLUSTER, CURRENT_LEVEL}] = level_val(90);
  s.r.p.store[{EP, LEVEL_CLUSTER, LEVEL_REMAINING_TIME}] = u16_val(0);
  MatterLightCommand stop;
  stop.stop_light = true;
  s.l.submit_cmd(stop);
  s.r.p.settle();
  CHECK(!s.l.window_light());
  CHECK(s.l.window_color());
  CHECK_NEAR(s.l.view.brightness, 90 / 254.0, 0.001);
  // CHIP's late colour steps are still ignored.
  size_t n = s.l.requests.size();
  matter_writes(s.r.p, COLOR_CLUSTER, CURRENT_X, u16_val(12000));
  s.r.p.settle();
  CHECK_EQ(s.l.requests.size(), n);
  // The colour transition ends: the colour settles.
  s.r.p.store[{EP, COLOR_CLUSTER, COLOR_REMAINING_TIME}] = u16_val(0);
  s.r.p.store[{EP, COLOR_CLUSTER, CURRENT_X}] = u16_val(20000);
  s.r.p.advance(MatterLightBinding::SETTLE_RETRY_MS + 10);
  s.r.p.settle();
  CHECK(!s.l.window_color());
  CHECK_EQ(s.l.requests.size(), n + 1);
  float r, g, b;
  CHECK(esphome::matter::conversion::from_matter::color(20000, 21845, r, g, b));
  CHECK_NEAR(s.l.requests.back().red, r, 0.001);
}

TEST(light_idle_answer_of_the_light_does_not_close_a_colour_window_opened_since) {
  Started s;
  s.l.requests.clear();
  s.l.submit_cmd(level_command(200, 1000));
  s.r.p.settle();
  // The nominal end of the level command requests the check; CHIP has not answered when a colour command is
  // applied (same main loop iteration), and the answer says "not busy".
  s.r.p.advance(1600);
  CHECK(!s.r.p.chip_queue.empty());
  s.l.submit_cmd(color_command(10000, 40000, 3000));
  s.r.p.run_main();  // the colour command is applied before CHIP's answer arrives
  CHECK(s.l.window_color());
  s.r.p.store[{EP, LEVEL_CLUSTER, LEVEL_REMAINING_TIME}] = u16_val(0);
  s.r.p.store[{EP, COLOR_CLUSTER, COLOR_REMAINING_TIME}] = u16_val(0);
  s.r.p.settle();
  // The level window is over, the colour window is the new command's and stays.
  CHECK(!s.l.window_light());
  CHECK(s.l.window_color());
  // Its own end settles the colour.
  s.r.p.store[{EP, COLOR_CLUSTER, CURRENT_X}] = u16_val(15000);
  size_t n = s.l.requests.size();
  s.r.p.advance(3600);
  s.r.p.settle();
  CHECK(!s.l.window_color());
  CHECK(s.l.requests.size() > n);
}

TEST(light_local_off_is_not_turned_back_on_by_a_stale_idle_answer) {
  Started s;
  s.l.requests.clear();
  s.l.submit_cmd(level_command(200, 1000));
  s.r.p.settle();
  s.r.p.advance(1600);  // the check is requested, CHIP has not run it
  // The user turns the light off. CHIP still has OnOff = true and level 200 in its store.
  s.l.view.on = false;
  s.l.entity_changed();
  CHECK(!s.l.window_light());
  s.r.p.settle();  // the stop, the (stale) check and the push run
  CHECK(!s.l.view.on);
  size_t on_requests = 0;
  for (auto &r : s.l.requests)
    if (r.state.has_value() && *r.state)
      on_requests++;
  CHECK_EQ(on_requests, (size_t) 0);
  CHECK(!last_write(s.r.p, ON_OFF_CLUSTER, ON_OFF).val.b);
}

TEST(light_retries_start_again_after_a_new_command) {
  Started s;
  s.l.submit_cmd(level_command(200, 500));
  s.r.p.settle();
  chip_is_busy(s.r.p);
  s.r.p.advance(1100);
  for (int i = 0; i < 5; i++) {
    s.r.p.settle();
    s.r.p.advance(MatterLightBinding::SETTLE_RETRY_MS + 10);
  }
  s.r.p.settle();
  CHECK(s.l.light_retries() >= 5);
  // A dimmer slider: the next command starts a new count.
  s.l.submit_cmd(level_command(100, 500));
  s.r.p.settle();
  CHECK_EQ(s.l.light_retries(), (uint32_t) 0);
}

TEST(light_push_leaves_the_colour_alone_while_a_colour_command_runs) {
  Started s;
  MatterLightCommand c;
  c.color = MatterLightColorUpdate{MatterLightColorUpdate::Kind::XY, 10000, 40000};
  c.transition_ms = 5000;
  c.color_group = true;
  s.l.submit_cmd(c);
  s.r.p.settle();
  CHECK(s.l.window_color());
  s.r.p.attribute_writes.clear();

  // A push while the window is open (a reconcile): OnOff and level follow the light, the colour is CHIP's.
  s.l.view.brightness = 0.4f;
  s.l.view.red = 0.0f;
  s.l.view.green = 0.0f;
  s.l.view.blue = 1.0f;
  s.l.force_push();
  s.r.p.settle();
  CHECK_EQ(writes_to(s.r.p, LEVEL_CLUSTER, CURRENT_LEVEL), (size_t) 1);
  CHECK_EQ(writes_to(s.r.p, COLOR_CLUSTER, COLOR_MODE), (size_t) 0);
  CHECK_EQ(writes_to(s.r.p, COLOR_CLUSTER, CURRENT_X), (size_t) 0);

  // At the end of the window the light is settled on CHIP's colour, which Matter already has.
  s.r.p.advance(6000);
  s.r.p.settle();
  CHECK(!s.l.window_color());
  CHECK_EQ(writes_to(s.r.p, COLOR_CLUSTER, COLOR_MODE), (size_t) 0);
}
