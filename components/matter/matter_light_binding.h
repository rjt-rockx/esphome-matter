#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "esphome/core/optional.h"
#include "matter_binding.h"
#include "matter_conversions.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstdint>

// Light state machine on the binding framework. Free of ESPHome entity and CHIP
// cluster headers so it can be host-tested (tests/cpp/test_light_binding.cpp);
// matter_lights.cpp supplies the entity hooks, the command decoding and the
// CHIP cluster calls.
namespace esphome::matter {

struct MatterColorTemperatureRange {
  uint16_t min_mireds;
  uint16_t max_mireds;
};

struct MatterLightCapabilities {
  bool has_level{false};
  // ColorControl attribute groups that exist on the endpoint.
  bool has_xy{false};
  bool has_hs{false};
  bool has_enhanced_hue{false};
  bool has_color{false}; // has_xy || has_hs
  bool has_enhanced_color_mode{false};
  optional<MatterColorTemperatureRange> color_temperature_range;
};

// The colour attributes that describe the active Matter ColorMode.
struct MatterLightColorUpdate {
  enum class Kind : uint8_t {
    XY,          // first = CurrentX, second = CurrentY
    HS,          // first = CurrentHue, second = CurrentSaturation
    ENHANCED_HS, // first = EnhancedCurrentHue, second = CurrentSaturation
    CT,          // first = ColorTemperatureMireds
  };
  Kind kind{Kind::XY};
  uint16_t first{0};
  uint16_t second{0};
};

// Matter changes that arrived since the last time they were applied to the
// ESPHome light. Merged so that OnOff, level and colour become one light call.
struct MatterLightPendingUpdate {
  optional<bool> on;
  optional<uint8_t> level;
  optional<MatterLightColorUpdate> color;
  // The values are authoritative (end of a commanded transition, restore after
  // a Stop command): apply them instantly, also when they look unchanged
  // because the light is still fading towards a different target.
  bool settle{false};

  bool empty() const {
    return !this->on.has_value() && !this->level.has_value() &&
           !this->color.has_value();
  }
};

// A Matter command that moves the light to a target, decoded on the CHIP task
// before CHIP executes it. Applied as one ESPHome light call with the real
// transition length instead of following CHIP's intermediate steps.
struct MatterLightCommand {
  optional<bool> on;
  optional<uint8_t> level;
  // Apply `level` only when this command turns a light that is off on (On
  // command with OnLevel).
  bool level_if_turning_on{false};
  optional<MatterLightColorUpdate> color;
  uint32_t transition_ms{0};
  // How long CHIP itself takes to reach the target. That can be longer than
  // transition_ms (On and Off use OnTransitionTime and OffTransitionTime for
  // the light, CHIP only knows OnOffTransitionTime), and CHIP's steps are
  // ignored until it is done.
  uint32_t chip_transition_ms{0};
  // ColorLoopSet starts or stops the color loop effect of the light.
  enum class ColorLoop : uint8_t { NONE, START, STOP };
  ColorLoop color_loop{ColorLoop::NONE};
  // Attribute groups whose CHIP-driven intermediate writes are ignored while
  // the transition runs.
  bool light_group{false}; // OnOff and CurrentLevel
  bool color_group{false}; // ColorControl attributes
  // Stop commands: CHIP stops where it is, so the light does as well.
  bool stop_light{false};
  bool stop_color{false};
};

// What the light is showing. The capability bits of the ESPHome color mode only
// say what the light is able to do: modes such as RGB_COLD_WARM_WHITE contain
// both RGB and white capabilities, so the mapping decides from the colour
// brightness which of them is in use.
enum class LightActiveColor : uint8_t { NONE, COLOR, CT };

// remote_values of the ESPHome light, as the binding reads them (main loop).
struct LightEntityView {
  bool on{false};
  float brightness{0.0f}; // 0.0 - 1.0
  LightActiveColor active{LightActiveColor::NONE};
  float red{0.0f};
  float green{0.0f};
  float blue{0.0f};
  float color_temperature{0.0f}; // mireds
};

// One light call, as decided by the binding (main loop).
struct LightRequest {
  optional<bool> state;
  optional<float> brightness;
  enum class Color : uint8_t { NONE, CT, RGB };
  Color color{Color::NONE};
  float color_temperature{0.0f}; // Color::CT
  float red{0.0f};               // Color::RGB
  float green{0.0f};
  float blue{0.0f};
  uint32_t transition_ms{0};
};

// ---------------------------------------------------------------------------
// Outbound state (ESPHome -> Matter)
// ---------------------------------------------------------------------------

// The colour that was last reported. `epoch` changes whenever it has to be
// written again, so a coalesced push never loses a colour report: the CHIP task
// writes the payload when the epoch differs from the one it wrote last.
struct LightColorReport {
  uint32_t epoch{0};
  bool is_ct{false};
  uint16_t x{0};
  uint16_t y{0};
  uint8_t hue{0};
  uint8_t saturation{0};
  uint16_t enhanced_hue{0};
  uint16_t color_temperature{0};
};

struct LightOutbound {
  bool valid{false};
  bool on{false};
  // False while a Matter command owns OnOff and CurrentLevel (its transition
  // runs on CHIP): the light group is left alone.
  bool write_light{true};
  bool has_level{false};
  uint8_t level{1};
  // False while a Matter command owns the colour attributes.
  bool write_color{true};
  LightColorReport color;
};

// ---------------------------------------------------------------------------
// Inbound (Matter -> ESPHome): attribute changes and decoded commands, in the
// order in which they arrived. Neighbouring attribute updates merge.
// ---------------------------------------------------------------------------
struct LightInboundItem {
  enum class Type : uint8_t { UPDATE, COMMAND };
  Type type{Type::UPDATE};
  MatterLightPendingUpdate update;
  MatterLightCommand command;
};

struct LightInbound {
  static constexpr size_t MAX_ITEMS = 8;
  std::array<LightInboundItem, MAX_ITEMS> items{};
  uint8_t count{0};
  // Items were dropped (too many within one main loop iteration). Whatever they
  // did (a stop, the end of a color loop) is recovered by a settle on the state
  // CHIP ended up with.
  bool overflowed{false};
};

namespace light_ids {
constexpr uint32_t ON_OFF_CLUSTER = 0x0006;
constexpr uint32_t ON_OFF = 0x0000;
constexpr uint32_t LEVEL_CLUSTER = 0x0008;
constexpr uint32_t CURRENT_LEVEL = 0x0000;
constexpr uint32_t LEVEL_REMAINING_TIME = 0x0001;
constexpr uint32_t MIN_LEVEL = 0x0002;
constexpr uint32_t MAX_LEVEL = 0x0003;
constexpr uint32_t COLOR_CLUSTER = 0x0300;
constexpr uint32_t CURRENT_HUE = 0x0000;
constexpr uint32_t CURRENT_SATURATION = 0x0001;
constexpr uint32_t COLOR_REMAINING_TIME = 0x0002;
constexpr uint32_t CURRENT_X = 0x0003;
constexpr uint32_t CURRENT_Y = 0x0004;
constexpr uint32_t COLOR_TEMPERATURE_MIREDS = 0x0007;
constexpr uint32_t COLOR_MODE = 0x0008;
constexpr uint32_t ENHANCED_CURRENT_HUE = 0x4000;
constexpr uint32_t ENHANCED_COLOR_MODE = 0x4001;
} // namespace light_ids

// ESPHome light <-> OnOff, LevelControl and ColorControl clusters.
//
// Matter -> ESPHome: OnOff and CurrentLevel are watched, the colour attributes
// trigger one coalesced read of the ones that belong to the active ColorMode
// (CurrentX/Y and hue/saturation are written one by one), and the commands that
// carry a transition time are decoded before CHIP executes them
// (submit_command()). Everything ends in submit_inbound(); the main loop merges
// OnOff, level and colour into one light call. CHIP walks to the target of a
// command in small steps and writes every one of them: while the transition of
// a command runs those steps are ignored (the "windows"), and when it ends the
// light is settled on what CHIP ended up with.
//
// ESPHome -> Matter: request_push() on every remote_values change that did not
// come from Matter. The whole state is reported; a burst (a transition that
// publishes again and again) costs one or two CHIP work items. The changes of
// the last applied Matter command are recognised as echoes
// (on_entity_published_()) and not pushed back, so CHIP's own transition is not
// disturbed.
//
// No reconcile after an apply: Matter already holds what was applied. A push
// that was skipped because a controller write was waiting is reconciled by the
// framework.
class MatterLightBinding : public MatterBinding<LightOutbound, LightInbound> {
public:
  // Fallback for changes that don't come from a command with a transition time
  // (Move, Step and ColorLoop run at a rate, and CHIP writes every step to the
  // attribute store). Steps that follow each other within this window are
  // treated as one running transition and are applied with a transition as long
  // as the time since the previous step, so the ESPHome light fades between
  // the steps.
  static constexpr uint32_t STREAM_WINDOW_MS = 300;
  // Shorter intervals are not worth a transition. They are applied instantly.
  static constexpr uint32_t MIN_TRANSITION_MS = 20;
  // Tolerance when comparing normalized RGB values.
  static constexpr float RGB_EPSILON = 1.0f / 256.0f;
  // After a command with a transition, CHIP's own writes for the same
  // attributes are ignored for the transition time plus this margin (CHIP's
  // last step lands a little after the nominal end). Then the light is
  // synchronized with the final attribute values, which corrects clamping and
  // other differences.
  static constexpr uint32_t COMMAND_MARGIN_MS = 500;
  // How often the window end checks whether CHIP is done with its transition
  // (RemainingTime is 0), at most this many times.
  static constexpr uint32_t SETTLE_RETRY_MS = 250;
  static constexpr uint32_t SETTLE_MAX_RETRIES = 2400;
  // How often is checked that the color loop effect is still the active effect.
  static constexpr uint32_t COLOR_LOOP_WATCH_MS = 250;
  // Highest valid CurrentLevel, CurrentHue and CurrentSaturation values.
  static constexpr uint8_t MATTER_MAX_LEVEL = 254;

  explicit MatterLightBinding(uint16_t endpoint_id)
      // The initial state comes from Matter (own boot read, applied as one
      // light call) and the reconcile only follows skipped pushes.
      : MatterBinding<LightOutbound, LightInbound>(
            endpoint_id, BindingPolicy{BootSync::NONE, 0, false}),
        ctl_slot_(&MatterLightBinding::ctl_trampoline_,
                  reinterpret_cast<intptr_t>(this)) {}

  // CHIP task, before CHIP executes the command.
  void submit_command(const MatterLightCommand &command) {
    LightInbound in;
    in.items[0].type = LightInboundItem::Type::COMMAND;
    in.items[0].command = command;
    in.count = 1;
    this->submit_inbound(in);
  }

  const MatterLightCapabilities &capabilities() const {
    return this->capabilities_;
  }

protected:
  // ---- Entity side (main loop)
  virtual LightEntityView entity_view_() = 0;
  // Performs the light call.
  virtual void perform_(const LightRequest &request) = 0;
  // Start / stop the ESPHome effect that stands for the color loop.
  virtual void start_color_loop_effect_() = 0;
  virtual void stop_color_loop_effect_hook_() = 0;
  virtual bool color_loop_effect_running_() = 0;
  // remote_values right after the last change from Matter.
  virtual void remember_applied_entity_() = 0;
  virtual bool remote_matches_applied_() = 0;

  // ---- Endpoint side
  // Main loop, from setup_(): fill capabilities_ from the endpoint, register
  // the entity listener and the command callbacks.
  // Returns false when there is no entity: the binding stays inert.
  virtual bool probe_capabilities_() = 0;
  virtual void listen_entity_() = 0;
  virtual void register_commands_() = 0;
  // CHIP: CHIP's own transitions are stopped where they are.
  virtual void chip_stop_light_transition_(uint8_t level) = 0;
  virtual void chip_stop_color_transitions_() = 0;
  // CHIP: deactivates CHIP's own color loop (ColorLoopActive back to 0).
  virtual void deactivate_chip_color_loop_() = 0;

  MatterLightCapabilities capabilities_;
  // A ColorLoopSet that activates the loop was accepted and not ended since.
  // CHIP's loop runs (ColorLoopActive) whether or not the effect started.
  // Written on the CHIP task when the command is decoded.
  std::atomic<bool> color_loop_requested_{false};

  // ----------------------------------------------------------------------
  // ESPHome -> Matter
  // ----------------------------------------------------------------------
public:
  // Main loop: remote_values of the light changed.
  void on_entity_published_() {
    if (this->synchronizing_from_matter_)
      return;
    // ESPHome publishes the values of a transition again while it runs (when
    // configured to do so) and when a call finishes. These are the values that
    // came from Matter, which Matter already has.
    if (this->applied_values_valid_) {
      if (this->remote_matches_applied_())
        return;
      this->applied_values_valid_ = false;
    }
    // A change from the ESPHome side wins over the windows of Matter commands.
    // Only then is the end of a color loop checked: that opens a window of its
    // own, which must stay open to ignore the hue that CHIP restores.
    this->end_windows_for_local_change_();
    if (this->color_loop_active_)
      this->check_color_loop_(true);
    this->request_push();
  }

  // Main loop, for tests and diagnostics.
  bool light_window_active() const { return this->light_window_active_; }
  bool color_window_active() const { return this->color_window_active_; }
  bool color_loop_active() const { return this->color_loop_active_; }

protected:
  LightOutbound capture_() final {
    LightEntityView v = this->entity_view_();
    uint32_t now = binding_platform()->now_ms();
    LightOutbound s;
    s.valid = true;
    s.on = v.on;
    s.has_level = this->capabilities_.has_level;
    s.level = conversion::to_matter::brightness(v.brightness);
    // The transition of a Matter command is CHIP's business until it ends.
    s.write_light = !this->light_group_suppressed_(now);
    // The colour steps of a Matter command are CHIP's business until its window
    // ends. The window after a color loop is not: the light's own colour is
    // reported at once, CHIP's restore of the hue is already ordered before it.
    bool chip_owns_color =
        this->color_window_open_(now) &&
        this->color_window_generation_ != this->loop_end_generation_;
    s.write_color = !chip_owns_color;
    if (!chip_owns_color)
      this->update_color_report_(v);
    s.color = this->color_report_;
    return s;
  }

  void write_matter_(const LightOutbound &s) final {
    if (!s.valid)
      return;
    if (s.write_light) {
      esp_matter_attr_val_t on_val = esp_matter_bool(s.on);
      this->report_(light_ids::ON_OFF_CLUSTER, light_ids::ON_OFF, on_val);
      if (s.has_level) {
        // CurrentLevel stays within MinLevel and MaxLevel.
        uint8_t min_level = 0;
        uint8_t max_level = MATTER_MAX_LEVEL;
        this->read_u8_(light_ids::LEVEL_CLUSTER, light_ids::MIN_LEVEL,
                       min_level);
        this->read_u8_(light_ids::LEVEL_CLUSTER, light_ids::MAX_LEVEL,
                       max_level);
        uint8_t level = std::min(max_level, std::max(min_level, s.level));
        esp_matter_attr_val_t level_val =
            esp_matter_nullable_uint8(nullable<uint8_t>(level));
        this->report_(light_ids::LEVEL_CLUSTER, light_ids::CURRENT_LEVEL,
                      level_val);
      }
    }
    if (s.color.epoch == this->chip_color_epoch_)
      return;
    // A colour skipped while a Matter colour command owns the colour is
    // superseded by that command, not pending: never write it later.
    this->chip_color_epoch_ = s.color.epoch;
    if (s.write_color)
      this->write_color_(s.color);
  }

  // ----------------------------------------------------------------------
  // Matter -> ESPHome
  // ----------------------------------------------------------------------
  void setup_() final {
    if (!this->probe_capabilities_())
      return;
    const bool has_ct = this->capabilities_.color_temperature_range.has_value();

    this->watch_attribute(light_ids::ON_OFF_CLUSTER, light_ids::ON_OFF);
    if (this->capabilities_.has_level)
      this->watch_attribute(light_ids::LEVEL_CLUSTER, light_ids::CURRENT_LEVEL);
    if (this->capabilities_.has_color || has_ct) {
      // Which of the colour attributes is relevant depends on the ColorMode, so
      // every one of them triggers a (coalesced) read of the active ones.
      this->watch_attribute(light_ids::COLOR_CLUSTER, light_ids::COLOR_MODE);
      if (this->capabilities_.has_enhanced_color_mode)
        this->watch_attribute(light_ids::COLOR_CLUSTER,
                              light_ids::ENHANCED_COLOR_MODE);
      if (this->capabilities_.has_xy) {
        this->watch_attribute(light_ids::COLOR_CLUSTER, light_ids::CURRENT_X);
        this->watch_attribute(light_ids::COLOR_CLUSTER, light_ids::CURRENT_Y);
      }
      if (this->capabilities_.has_hs) {
        this->watch_attribute(light_ids::COLOR_CLUSTER, light_ids::CURRENT_HUE);
        this->watch_attribute(light_ids::COLOR_CLUSTER,
                              light_ids::CURRENT_SATURATION);
      }
      if (this->capabilities_.has_enhanced_hue)
        this->watch_attribute(light_ids::COLOR_CLUSTER,
                              light_ids::ENHANCED_CURRENT_HUE);
      if (has_ct)
        this->watch_attribute(light_ids::COLOR_CLUSTER,
                              light_ids::COLOR_TEMPERATURE_MIREDS);
    }

    // Light calls on_entity_published_() on updates.
    this->listen_entity_();

    // Restore the ESPHome light from the persisted Matter state. The values are
    // read in one go and applied as a single light call. That way the level and
    // colour are set before the light is turned on, so it doesn't flash at the
    // brightness and colour it happened to have before.
    this->request_state_read_(false, true, true);

    this->register_commands_();
  }

  // CHIP: a watched attribute changed (not by ESPHome).
  void on_attribute_(uint32_t cluster, uint32_t attribute,
                     const esp_matter_attr_val_t &v) override {
    if (cluster == light_ids::ON_OFF_CLUSTER &&
        attribute == light_ids::ON_OFF) {
      MatterLightPendingUpdate update;
      update.on = v.val.b;
      this->submit_update_(update);
    } else if (cluster == light_ids::LEVEL_CLUSTER &&
               attribute == light_ids::CURRENT_LEVEL) {
      // CurrentLevel is nullable.
      if (v.is_null())
        return;
      MatterLightPendingUpdate update;
      update.level = v.val.u8;
      this->submit_update_(update);
    } else if (cluster == light_ids::COLOR_CLUSTER) {
      this->schedule_color_read_();
    }
  }

  void merge_inbound_(LightInbound &acc, const LightInbound &in) final {
    for (uint8_t i = 0; i < in.count; i++) {
      const LightInboundItem &item = in.items[i];
      if (item.type == LightInboundItem::Type::UPDATE && acc.count > 0 &&
          acc.items[acc.count - 1].type == LightInboundItem::Type::UPDATE) {
        merge_update_(acc.items[acc.count - 1].update, item.update);
        continue;
      }
      if (acc.count == LightInbound::MAX_ITEMS) {
        // Many commands within one main loop iteration: drop the oldest. The
        // apply recovers what it did by a settle (see LightInbound).
        ESP_LOGW("matter.light", "Light inbound queue full, dropped oldest");
        acc.overflowed = true;
        for (size_t k = 1; k < LightInbound::MAX_ITEMS; k++)
          acc.items[k - 1] = acc.items[k];
        acc.count--;
      }
      acc.items[acc.count++] = item;
    }
  }

  // Main loop. Processes attribute updates and commands in the order in which
  // they arrived, then applies what is left as one light call.
  void apply_inbound_(const LightInbound &in) final {
    if (in.overflowed) {
      // A dropped command may have been a stop or the end of a color loop. The
      // loop effect ends unless a retained ColorLoopSet starts it again (the
      // items below run after this), and the light settles on CHIP's state.
      if (!this->color_loop_requested_.load())
        this->stop_color_loop_effect_();
      this->light_window_active_ = false;
      this->color_window_active_ = false;
      this->request_state_read_(true, true, true);
    }
    for (uint8_t i = 0; i < in.count; i++) {
      const LightInboundItem &item = in.items[i];
      if (item.type == LightInboundItem::Type::UPDATE)
        this->queue_update_(item.update);
      else
        this->apply_command_(item.command);
    }
    this->apply_pending_();
  }

  // CHIP
  void submit_update_(const MatterLightPendingUpdate &update) {
    if (update.empty())
      return;
    LightInbound in;
    in.items[0].type = LightInboundItem::Type::UPDATE;
    in.items[0].update = update;
    in.count = 1;
    this->submit_inbound(in);
  }

  static void merge_update_(MatterLightPendingUpdate &acc,
                            const MatterLightPendingUpdate &in) {
    if (in.settle)
      acc.settle = true;
    if (in.on.has_value())
      acc.on = in.on;
    if (in.level.has_value())
      acc.level = in.level;
    if (in.color.has_value())
      acc.color = in.color;
  }

  // ---- Reads on the CHIP task ------------------------------------------
  bool read_value_(uint32_t cluster, uint32_t attribute,
                   esp_matter_attr_val_t &value) {
    // Absent attributes and null values read as "no value".
    return binding_platform()->read_attribute(this->endpoint_id_, cluster,
                                              attribute, value) &&
           !value.is_null();
  }
  bool read_bool_(uint32_t cluster, uint32_t attribute, bool &out) {
    esp_matter_attr_val_t value;
    if (!this->read_value_(cluster, attribute, value))
      return false;
    out = value.val.b;
    return true;
  }
  bool read_u8_(uint32_t cluster, uint32_t attribute, uint8_t &out) {
    esp_matter_attr_val_t value;
    if (!this->read_value_(cluster, attribute, value))
      return false;
    out = value.val.u8;
    return true;
  }
  bool read_u16_(uint32_t cluster, uint32_t attribute, uint16_t &out) {
    esp_matter_attr_val_t value;
    if (!this->read_value_(cluster, attribute, value))
      return false;
    out = value.val.u16;
    return true;
  }

  // Reads OnOff, the level and the active colour from the attribute store.
  MatterLightPendingUpdate read_state_() {
    MatterLightPendingUpdate update;
    uint8_t level;
    if (this->capabilities_.has_level &&
        this->read_u8_(light_ids::LEVEL_CLUSTER, light_ids::CURRENT_LEVEL,
                       level))
      update.level = level;
    update.color = this->read_color_update_();
    bool on;
    if (this->read_bool_(light_ids::ON_OFF_CLUSTER, light_ids::ON_OFF, on))
      update.on = on;
    return update;
  }

  // Reads the colour attributes that belong to the active (Enhanced)ColorMode.
  optional<MatterLightColorUpdate> read_color_update_() {
    using Kind = MatterLightColorUpdate::Kind;
    if (!this->capabilities_.has_color &&
        !this->capabilities_.color_temperature_range.has_value())
      return nullopt;

    auto read_u8 = [this](uint32_t attribute, uint8_t &out) {
      return this->read_u8_(light_ids::COLOR_CLUSTER, attribute, out);
    };
    auto read_u16 = [this](uint32_t attribute, uint16_t &out) {
      return this->read_u16_(light_ids::COLOR_CLUSTER, attribute, out);
    };

    // EnhancedColorMode: 0 = HS, 1 = XY, 2 = CT, 3 = enhanced HS. ColorMode only
    // knows the first three.
    uint8_t mode = 0;
    if (!read_u8(this->capabilities_.has_enhanced_color_mode
                     ? light_ids::ENHANCED_COLOR_MODE
                     : light_ids::COLOR_MODE,
                 mode))
      return nullopt;

    MatterLightColorUpdate update;
    switch (mode) {
    case 0: { // CurrentHueAndCurrentSaturation
      uint8_t hue;
      uint8_t saturation;
      if (!this->capabilities_.has_hs ||
          !read_u8(light_ids::CURRENT_HUE, hue) ||
          !read_u8(light_ids::CURRENT_SATURATION, saturation))
        return nullopt;
      update.kind = Kind::HS;
      update.first = hue;
      update.second = saturation;
      return update;
    }
    case 1: { // CurrentXAndCurrentY
      uint16_t x;
      uint16_t y;
      if (!this->capabilities_.has_xy || !read_u16(light_ids::CURRENT_X, x) ||
          !read_u16(light_ids::CURRENT_Y, y))
        return nullopt;
      update.kind = Kind::XY;
      update.first = x;
      update.second = y;
      return update;
    }
    case 2: { // ColorTemperatureMireds
      uint16_t mireds;
      if (!this->capabilities_.color_temperature_range.has_value() ||
          !read_u16(light_ids::COLOR_TEMPERATURE_MIREDS, mireds))
        return nullopt;
      update.kind = Kind::CT;
      update.first = mireds;
      return update;
    }
    case 3: { // EnhancedCurrentHueAndCurrentSaturation
      uint16_t enhanced_hue;
      uint8_t saturation;
      if (!this->capabilities_.has_enhanced_hue ||
          !read_u16(light_ids::ENHANCED_CURRENT_HUE, enhanced_hue) ||
          !read_u8(light_ids::CURRENT_SATURATION, saturation))
        return nullopt;
      update.kind = Kind::ENHANCED_HS;
      update.first = enhanced_hue;
      update.second = saturation;
      return update;
    }
    default:
      return nullopt;
    }
  }

  // CHIP. CurrentX and CurrentY (and hue and saturation) are updated
  // separately. Read them after the current Matter work item completes so a new
  // coordinate is not combined with the previous value of the other one.
  void schedule_color_read_() {
    if (this->color_read_pending_.exchange(true))
      return;
    if (!binding_platform()->schedule_chip(
            &MatterLightBinding::color_read_trampoline_,
            reinterpret_cast<intptr_t>(this))) {
      // The CHIP queue is full: the next colour attribute change tries again.
      this->color_read_pending_.store(false);
      ESP_LOGW("matter.light", "Could not schedule the colour read");
    }
  }
  static void color_read_trampoline_(intptr_t ctx) {
    auto *self = reinterpret_cast<MatterLightBinding *>(ctx);
    MATTER_ASSERT_CHIP_THREAD();
    self->color_read_pending_.store(false);
    auto color = self->read_color_update_();
    if (!color.has_value())
      return;
    MatterLightPendingUpdate update;
    update.color = color;
    self->submit_update_(update);
  }

  // ---- Work for the CHIP task, requested by the main loop ---------------
  enum : uint8_t {
    CTL_READ_LIGHT = 1u << 0,
    CTL_READ_COLOR = 1u << 1,
    CTL_READ_SETTLE = 1u << 2,
    CTL_IDLE_LIGHT = 1u << 3,
    CTL_IDLE_COLOR = 1u << 4,
    CTL_DEACTIVATE_LOOP = 1u << 5,
    CTL_STOP_LIGHT = 1u << 6,
    CTL_STOP_COLOR = 1u << 7,
  };

  void request_ctl_(uint8_t bits) {
    MATTER_ASSERT_MAIN_LOOP();
    this->ctl_bits_.fetch_or(bits);
    this->ctl_slot_.request();
  }

  // Main loop. Reads the state on the CHIP task (after the work item that is
  // running now) and applies it. Settling applies the values instantly, for the
  // groups that are asked for.
  void request_state_read_(bool settle, bool light, bool color) {
    uint8_t bits = 0;
    if (settle)
      bits |= CTL_READ_SETTLE;
    if (light)
      bits |= CTL_READ_LIGHT;
    if (color)
      bits |= CTL_READ_COLOR;
    this->request_ctl_(bits);
  }

  static void ctl_trampoline_(intptr_t ctx) {
    auto *self = reinterpret_cast<MatterLightBinding *>(ctx);
    self->ctl_slot_.begin_run();
    self->run_ctl_on_chip_();
  }

  void run_ctl_on_chip_() {
    MATTER_ASSERT_CHIP_THREAD();
    uint8_t bits = this->ctl_bits_.exchange(0);
    // A change from the ESPHome side ended the windows: CHIP stops its
    // transitions where they are. Ahead of everything else, and not in the push:
    // a push can be skipped and then outlive a newer command's transition.
    if (bits & CTL_STOP_COLOR)
      this->chip_stop_color_transitions_();
    if (bits & CTL_STOP_LIGHT)
      this->chip_stop_light_transition_(this->stop_level_.load());
    if (bits & CTL_DEACTIVATE_LOOP) {
      this->deactivate_chip_color_loop_();
      // CHIP restores the hue from before the loop. The report of the light's
      // own colour has to come after that, whatever the order in which the work
      // items ran: report again.
      if (this->rereport_after_deactivate_.exchange(false)) {
        binding_platform()->defer_main([this]() {
          this->reported_color_valid_ = false;
          this->request_push();
        });
      }
    }
    if (bits & (CTL_IDLE_LIGHT | CTL_IDLE_COLOR))
      this->check_chip_idle_(bits & CTL_IDLE_LIGHT, bits & CTL_IDLE_COLOR);
    if (bits & (CTL_READ_LIGHT | CTL_READ_COLOR)) {
      MatterLightPendingUpdate update = this->read_state_();
      if (!(bits & CTL_READ_LIGHT)) {
        update.on.reset();
        update.level.reset();
      }
      if (!(bits & CTL_READ_COLOR))
        update.color.reset();
      update.settle = (bits & CTL_READ_SETTLE) != 0;
      this->submit_update_(update);
    }
  }

  // CHIP's transition timer can run behind the nominal time. Settling on its
  // attributes while RemainingTime is not 0 would jump to a value in the middle
  // of the transition, so the windows stay open until it is done.
  void check_chip_idle_(bool light, bool color) {
    uint16_t remaining = 0;
    bool busy = false;
    if (light &&
        this->read_u16_(light_ids::LEVEL_CLUSTER,
                        light_ids::LEVEL_REMAINING_TIME, remaining) &&
        remaining != 0)
      busy = true;
    if (color &&
        this->read_u16_(light_ids::COLOR_CLUSTER,
                        light_ids::COLOR_REMAINING_TIME, remaining) &&
        remaining != 0)
      busy = true;
    binding_platform()->defer_main([this, light, color, busy]() {
      this->window_end_checked_(light, color, busy);
    });
  }

  // ----------------------------------------------------------------------
  // Main loop: applying Matter changes
  // ----------------------------------------------------------------------
  static bool is_before_(uint32_t now, uint32_t until) {
    return static_cast<int32_t>(until - now) > 0;
  }
  static bool nearly_equal_(float a, float b) {
    return std::fabs(a - b) < RGB_EPSILON;
  }

  bool light_group_suppressed_(uint32_t now) const {
    return this->light_window_active_ &&
           is_before_(now, this->light_window_until_ms_);
  }
  // A window of a command (or the end of a color loop) is open.
  bool color_window_open_(uint32_t now) const {
    return this->color_window_active_ &&
           is_before_(now, this->color_window_until_ms_);
  }
  // CHIP's colour attribute writes are ignored: a window is open or the effect
  // of a color loop runs (the steps of the loop belong to the effect).
  bool color_group_suppressed_(uint32_t now) const {
    return this->color_loop_active_ || this->color_window_open_(now);
  }

  // Merges the update into the pending one. CHIP walks to the target of a
  // command in small steps and writes every one of them. The light was already
  // sent to the target of that command with the real transition time, so the
  // steps are ignored while it runs.
  void queue_update_(MatterLightPendingUpdate update) {
    uint32_t now = binding_platform()->now_ms();
    if (this->light_group_suppressed_(now)) {
      update.on.reset();
      update.level.reset();
    }
    if (this->color_group_suppressed_(now))
      update.color.reset();
    if (update.empty())
      return;
    merge_update_(this->pending_, update);
  }

  // Adds the Matter colour to the request. Returns false when the call doesn't
  // need to change (or the colour can't be applied).
  bool add_color_to_request_(LightRequest &request,
                             const MatterLightColorUpdate &color, bool force,
                             const LightEntityView &view) {
    using Kind = MatterLightColorUpdate::Kind;
    bool is_active_color = view.active == LightActiveColor::COLOR;

    if (color.kind == Kind::CT) {
      if (!this->capabilities_.color_temperature_range.has_value())
        return false;
      float mireds = static_cast<float>(color.first);
      if (!force && view.active == LightActiveColor::CT &&
          std::fabs(view.color_temperature - mireds) < 0.5f)
        return false;
      request.color = LightRequest::Color::CT;
      request.color_temperature = mireds;
      return true;
    }

    float red = 0.0f;
    float green = 0.0f;
    float blue = 0.0f;
    bool converted = false;
    switch (color.kind) {
    case Kind::XY:
      converted = conversion::from_matter::color(color.first, color.second,
                                                 red, green, blue);
      break;
    case Kind::HS:
      converted = conversion::from_matter::hue_saturation(
          static_cast<uint8_t>(color.first),
          static_cast<uint8_t>(color.second), red, green, blue);
      break;
    case Kind::ENHANCED_HS:
      converted = conversion::from_matter::enhanced_hue_saturation(
          color.first, static_cast<uint8_t>(color.second), red, green, blue);
      break;
    default:
      break;
    }
    if (!converted) {
      ESP_LOGW("matter.light",
               "Failed to convert Matter color: endpoint=%u, kind=%u, "
               "first=%u, second=%u",
               static_cast<unsigned>(this->endpoint_id_),
               static_cast<unsigned>(color.kind),
               static_cast<unsigned>(color.first),
               static_cast<unsigned>(color.second));
      return false;
    }
    if (!force && is_active_color && nearly_equal_(view.red, red) &&
        nearly_equal_(view.green, green) && nearly_equal_(view.blue, blue))
      return false;
    request.color = LightRequest::Color::RGB;
    request.red = red;
    request.green = green;
    request.blue = blue;
    return true;
  }

  void perform_from_matter_(const LightRequest &request) {
    this->synchronizing_from_matter_ = true;
    this->perform_(request);
    this->synchronizing_from_matter_ = false;
    this->remember_applied_values_();
  }

  void apply_pending_() {
    MatterLightPendingUpdate update = this->pending_;
    this->pending_ = MatterLightPendingUpdate{};
    if (update.empty())
      return;
    // Settling applies the values even when they look unchanged: the light may
    // still be fading to a target that Matter didn't reach (Stop command).
    bool force = update.settle;

    LightEntityView view = this->entity_view_();
    LightRequest request;
    bool changed = false;

    if (update.on.has_value() && (force || view.on != *update.on)) {
      request.state = *update.on;
      changed = true;
    }
    if (update.level.has_value()) {
      float brightness = conversion::from_matter::brightness(*update.level);
      if (force ||
          std::fabs(view.brightness - brightness) >= (0.5f / 254.0f)) {
        request.brightness = brightness;
        changed = true;
      }
    }
    if (update.color.has_value() &&
        this->add_color_to_request_(request, *update.color, force, view))
      changed = true;
    if (!changed)
      return;

    // Rate based commands (Move, Step, ColorLoop) have no transition time that
    // we could use: CHIP moves in small steps and writes every one of them.
    // Don't fight that by jumping to each step instantly: when this update
    // directly follows a previous one, let the light fade for the time that
    // passed between the two. A lone change is applied instantly.
    uint32_t transition_ms = 0;
    if (force) {
      this->last_apply_valid_ = false;
    } else {
      uint32_t now = binding_platform()->now_ms();
      if (this->last_apply_valid_) {
        uint32_t elapsed = now - this->last_apply_ms_;
        if (elapsed >= MIN_TRANSITION_MS && elapsed <= STREAM_WINDOW_MS)
          transition_ms = elapsed;
      }
      this->last_apply_ms_ = now;
      this->last_apply_valid_ = true;
    }
    request.transition_ms = transition_ms;
    this->perform_from_matter_(request);
  }

  // Matter already has the values that were just applied. Remember them so
  // their echo is ignored, also when it is published later on and the flag
  // above is no longer set.
  void remember_applied_values_() {
    this->remember_applied_entity_();
    this->applied_values_valid_ = true;
    LightEntityView applied = this->entity_view_();
    switch (applied.active) {
    case LightActiveColor::COLOR:
      this->reported_color_valid_ = true;
      this->reported_color_is_ct_ = false;
      this->reported_red_ = applied.red;
      this->reported_green_ = applied.green;
      this->reported_blue_ = applied.blue;
      break;
    case LightActiveColor::CT:
      this->reported_color_valid_ = true;
      this->reported_color_is_ct_ = true;
      this->reported_color_temperature_ = applied.color_temperature;
      break;
    default:
      break;
    }
  }

  // ---- Commands with a transition time ------------------------------------

  // Moves the light to the target of a command with the transition time of the
  // command, as one light call.
  void apply_command_(const MatterLightCommand &command) {
    // The effect is stopped in a call of its own: it would otherwise take the
    // transition of the call that follows.
    if (command.color_loop == MatterLightCommand::ColorLoop::STOP)
      this->stop_color_loop_effect_();
    if (command.stop_light || command.stop_color) {
      // The values to settle on follow from the CHIP task: CHIP stops where it
      // is, so the light does as well.
      if (command.stop_light)
        this->light_window_active_ = false;
      if (command.stop_color)
        this->color_window_active_ = false;
      this->request_state_read_(true, command.stop_light, command.stop_color);
      return;
    }
    if (command.color_loop == MatterLightCommand::ColorLoop::START) {
      // ColorLoopSet: run the effect. CHIP's own steps are ignored until the
      // loop ends, but only if the effect really started (it doesn't on a light
      // that is off).
      this->last_apply_valid_ = false;
      this->synchronizing_from_matter_ = true;
      this->start_color_loop_effect_();
      this->synchronizing_from_matter_ = false;
      if (this->color_loop_effect_running_()) {
        this->pending_.color.reset();
        this->color_loop_active_ = true;
        this->watch_color_loop_();
      } else {
        ESP_LOGW("matter.light",
                 "Color loop effect didn't start on endpoint %u (is the light "
                 "off?), the light follows CHIP's loop instead",
                 static_cast<unsigned>(this->endpoint_id_));
      }
      return;
    }
    if (command.color_loop == MatterLightCommand::ColorLoop::STOP &&
        !command.color.has_value() && !command.on.has_value() &&
        !command.level.has_value()) {
      // Nothing else in this command. CHIP restores the hue from before the
      // loop; follow that.
      this->request_state_read_(true, false, true);
      return;
    }

    LightEntityView view = this->entity_view_();
    LightRequest request;
    bool changed = false;
    bool turns_on = command.on.has_value() && *command.on && !view.on;

    if (command.on.has_value() && view.on != *command.on) {
      request.state = *command.on;
      changed = true;
    }
    // A light that is turned off keeps its brightness, which fades out.
    bool brightness_applies =
        command.level.has_value() && (!command.on.has_value() || *command.on) &&
        (!command.level_if_turning_on || turns_on);
    if (brightness_applies) {
      float brightness = conversion::from_matter::brightness(*command.level);
      if (std::fabs(view.brightness - brightness) >= (0.5f / 254.0f)) {
        request.brightness = brightness;
        changed = true;
      }
    }
    if (command.color.has_value() &&
        this->add_color_to_request_(request, *command.color, false, view))
      changed = true;

    // Ignore CHIP's steps towards the target of this command, but not for the
    // other attribute group which isn't part of it. A change that was queued
    // before the command is out of date.
    uint32_t now = binding_platform()->now_ms();
    uint32_t window_ms =
        std::max(command.transition_ms, command.chip_transition_ms) +
        COMMAND_MARGIN_MS;
    if (command.light_group) {
      this->light_window_active_ = true;
      this->light_window_until_ms_ = now + window_ms;
      this->pending_.on.reset();
      this->pending_.level.reset();
    }
    if (command.color_group) {
      this->color_window_active_ = true;
      this->color_window_generation_++;
      this->color_window_until_ms_ = now + window_ms;
      this->pending_.color.reset();
    }
    // Later steps of a rate based command are not related to this one.
    this->last_apply_valid_ = false;

    if (changed) {
      request.transition_ms = command.transition_ms;
      this->perform_from_matter_(request);
    }

    // When the window ends, synchronize with what CHIP ended up with.
    binding_platform()->main_after(window_ms, [this]() {
      this->finish_command_windows_();
    });
  }

  void finish_command_windows_() {
    uint32_t now = binding_platform()->now_ms();
    // A newer command extended a window; its own timer finishes it. A running
    // color loop doesn't keep the windows open: it has no window.
    if (this->light_group_suppressed_(now) || this->color_window_open_(now))
      return;
    bool light = this->light_window_active_;
    bool color = this->color_window_active_;
    if (!light && !color)
      return;
    this->settle_when_idle_(light, color);
  }

  void settle_when_idle_(bool light, bool color) {
    uint8_t bits = 0;
    if (light)
      bits |= CTL_IDLE_LIGHT;
    if (color)
      bits |= CTL_IDLE_COLOR;
    this->request_ctl_(bits);
  }

  // Main loop: the CHIP task checked whether its transitions are done.
  void window_end_checked_(bool light, bool color, bool busy) {
    uint32_t now = binding_platform()->now_ms();
    // A newer command has its own timer.
    if (this->light_group_suppressed_(now) || this->color_window_open_(now))
      return;
    if (busy && this->settle_retries_ < SETTLE_MAX_RETRIES) {
      this->settle_retries_++;
      if (light)
        this->light_window_until_ms_ = now + SETTLE_RETRY_MS;
      if (color)
        this->color_window_until_ms_ = now + SETTLE_RETRY_MS;
      binding_platform()->main_after(SETTLE_RETRY_MS, [this]() {
        this->finish_command_windows_();
      });
      return;
    }
    this->settle_retries_ = 0;
    this->light_window_active_ = false;
    this->color_window_active_ = false;
    this->request_state_read_(true, light, color);
  }

  // A change on the ESPHome side during the window of a Matter command wins:
  // the window ends and CHIP stops its transition where it is, so the end of
  // that transition doesn't overwrite the local change.
  void end_windows_for_local_change_() {
    bool light = this->light_window_active_;
    bool color = this->color_window_active_;
    if (!light && !color)
      return;
    this->light_window_active_ = false;
    this->color_window_active_ = false;
    this->pending_ = MatterLightPendingUpdate{};
    // CHIP stops first, then the push that follows reports.
    uint8_t bits = 0;
    if (color)
      bits |= CTL_STOP_COLOR;
    if (light) {
      bits |= CTL_STOP_LIGHT;
      this->stop_level_.store(
          conversion::to_matter::brightness(this->entity_view_().brightness));
    }
    this->request_ctl_(bits);
  }

  // ---- Color loop -----------------------------------------------------------

  // Stops the color loop effect in a call of its own.
  void stop_color_loop_effect_() {
    if (this->color_loop_active_ || this->color_loop_effect_running_()) {
      this->synchronizing_from_matter_ = true;
      this->stop_color_loop_effect_hook_();
      this->synchronizing_from_matter_ = false;
    }
    this->color_loop_active_ = false;
  }

  void watch_color_loop_() {
    binding_platform()->main_after(COLOR_LOOP_WATCH_MS, [this]() {
      if (!this->color_loop_active_)
        return;
      this->check_color_loop_(false);
      if (this->color_loop_active_)
        this->watch_color_loop_();
    });
  }

  // The effect can end without Matter: another effect was picked, the light was
  // turned off (also by a Matter Off command). Then CHIP's loop is deactivated
  // so ColorLoopActive goes back to 0.
  // `push_follows`: the caller pushes the state right away. Then the push may
  // run before CHIP restored the hue, and the colour is reported once more
  // after the restore. (Otherwise the timer below reports it, well after.)
  void check_color_loop_(bool push_follows) {
    if (!this->color_loop_active_ || this->color_loop_effect_running_())
      return;
    this->color_loop_active_ = false;
    this->color_loop_requested_ = false;
    // CHIP restores the hue from before the loop. That is not a color the light
    // is going to show, so ignore it for a moment and report the light's own
    // color afterwards.
    this->color_window_active_ = true;
    this->color_window_until_ms_ =
        binding_platform()->now_ms() + COMMAND_MARGIN_MS;
    uint32_t generation = ++this->color_window_generation_;
    this->loop_end_generation_ = generation;
    this->reported_color_valid_ = false;
    this->rereport_after_deactivate_.store(push_follows);
    this->request_ctl_(CTL_DEACTIVATE_LOOP);
    binding_platform()->main_after(
        COMMAND_MARGIN_MS + 50, [this, generation]() {
          // Only the window opened here; a newer Matter command owns its own.
          if (this->color_window_generation_ == generation)
            this->color_window_active_ = false;
          this->request_push();
        });
  }

  // ---- Reports (CHIP, inside the PushScope) ---------------------------------
  void report_(uint32_t cluster, uint32_t attribute,
               esp_matter_attr_val_t &value) {
    binding_platform()->write_attribute(this->endpoint_id_, cluster, attribute,
                                        value, true);
  }

  void write_color_(const LightColorReport &c) {
    using namespace light_ids;
    if (c.is_ct) {
      esp_matter_attr_val_t ct_val = esp_matter_uint16(c.color_temperature);
      this->report_(COLOR_CLUSTER, COLOR_TEMPERATURE_MIREDS, ct_val);
      esp_matter_attr_val_t mode_val =
          esp_matter_enum8(COLOR_MODE_COLOR_TEMPERATURE);
      this->report_(COLOR_CLUSTER, COLOR_MODE, mode_val);
      if (this->capabilities_.has_enhanced_color_mode)
        this->report_(COLOR_CLUSTER, ENHANCED_COLOR_MODE, mode_val);
      return;
    }
    // Report the colour in every representation the endpoint has, so that
    // controllers using either one see the same colour. The mode is
    // reported last. XY is the preferred mode.
    uint8_t mode = this->capabilities_.has_xy ? COLOR_MODE_XY : COLOR_MODE_HS;
    if (this->capabilities_.has_xy) {
      esp_matter_attr_val_t x_val = esp_matter_uint16(c.x);
      this->report_(COLOR_CLUSTER, CURRENT_X, x_val);
      esp_matter_attr_val_t y_val = esp_matter_uint16(c.y);
      this->report_(COLOR_CLUSTER, CURRENT_Y, y_val);
    }
    if (this->capabilities_.has_hs) {
      esp_matter_attr_val_t hue_val = esp_matter_uint8(c.hue);
      this->report_(COLOR_CLUSTER, CURRENT_HUE, hue_val);
      esp_matter_attr_val_t saturation_val = esp_matter_uint8(c.saturation);
      this->report_(COLOR_CLUSTER, CURRENT_SATURATION, saturation_val);
    }
    if (this->capabilities_.has_enhanced_hue) {
      esp_matter_attr_val_t enhanced_hue_val = esp_matter_uint16(c.enhanced_hue);
      this->report_(COLOR_CLUSTER, ENHANCED_CURRENT_HUE, enhanced_hue_val);
    }
    esp_matter_attr_val_t mode_val = esp_matter_enum8(mode);
    this->report_(COLOR_CLUSTER, COLOR_MODE, mode_val);
    if (this->capabilities_.has_enhanced_color_mode)
      this->report_(COLOR_CLUSTER, ENHANCED_COLOR_MODE, mode_val);
  }

  // Main loop. Decides whether the colour has to be reported again, the same
  // way as before the migration: only when it differs from the last reported
  // one, to avoid needlessly rewriting ColorMode (which would switch a
  // controller's HS mode back to XY).
  void update_color_report_(const LightEntityView &v) {
    // Report the colour that the light is showing right now. While a color loop
    // effect runs the color is the effect's business, CHIP's loop attributes
    // are what controllers see.
    LightActiveColor active =
        this->color_loop_active_ ? LightActiveColor::NONE : v.active;
    if (active == LightActiveColor::COLOR && this->capabilities_.has_color) {
      float r = v.red;
      float g = v.green;
      float b = v.blue;
      bool changed = !this->reported_color_valid_ ||
                     this->reported_color_is_ct_ ||
                     !nearly_equal_(r, this->reported_red_) ||
                     !nearly_equal_(g, this->reported_green_) ||
                     !nearly_equal_(b, this->reported_blue_);
      LightColorReport report = this->color_report_;
      // Black has no xy or hue. Leave the Matter colour as it is.
      if (changed &&
          conversion::to_matter::color(r, g, b, report.x, report.y) &&
          conversion::to_matter::hue_saturation(
              r, g, b, report.hue, report.saturation, report.enhanced_hue)) {
        report.is_ct = false;
        report.epoch = this->color_report_.epoch + 1;
        this->color_report_ = report;
        this->reported_color_valid_ = true;
        this->reported_color_is_ct_ = false;
        this->reported_red_ = r;
        this->reported_green_ = g;
        this->reported_blue_ = b;
      }
    } else if (active == LightActiveColor::CT &&
               this->capabilities_.color_temperature_range.has_value()) {
      float mireds = v.color_temperature;
      bool changed =
          !this->reported_color_valid_ || !this->reported_color_is_ct_ ||
          std::fabs(mireds - this->reported_color_temperature_) >= 0.5f;
      if (changed) {
        this->color_report_.is_ct = true;
        this->color_report_.color_temperature =
            conversion::to_matter::color_temperature(mireds);
        this->color_report_.epoch++;
        this->reported_color_valid_ = true;
        this->reported_color_is_ct_ = true;
        this->reported_color_temperature_ = mireds;
      }
    }
  }

  // ColorMode values.
  static constexpr uint8_t COLOR_MODE_HS = 0;
  static constexpr uint8_t COLOR_MODE_XY = 1;
  static constexpr uint8_t COLOR_MODE_COLOR_TEMPERATURE = 2;

  // Main loop only.
  MatterLightPendingUpdate pending_;
  bool color_loop_active_{false};
  bool synchronizing_from_matter_{false};
  uint32_t settle_retries_{0};
  // Counts the opened colour windows, so a delayed close only closes its own.
  uint32_t color_window_generation_{0};
  // The generation of the window that was opened by the end of a color loop.
  uint32_t loop_end_generation_{0};
  // Remote values right after the last change from Matter. Used to ignore the
  // echo of that change, also when ESPHome publishes it later (transitions).
  bool applied_values_valid_{false};
  uint32_t last_apply_ms_{0};
  bool last_apply_valid_{false};
  // While a transition that came from a Matter command runs, CHIP's
  // intermediate attribute writes for that group are ignored. Matter still
  // reports them. The end of the window is set by the command's transition
  // time plus a margin, after which the light is synchronized with the final
  // attribute values.
  bool light_window_active_{false};
  uint32_t light_window_until_ms_{0};
  bool color_window_active_{false};
  uint32_t color_window_until_ms_{0};
  // Last colour that was reported to Matter, to avoid needlessly rewriting
  // ColorMode.
  bool reported_color_valid_{false};
  bool reported_color_is_ct_{false};
  float reported_red_{0.0f};
  float reported_green_{0.0f};
  float reported_blue_{0.0f};
  float reported_color_temperature_{0.0f};
  LightColorReport color_report_;

  // CHIP task only.
  uint32_t chip_color_epoch_{0};

private:
  ChipWorkSlot ctl_slot_;
  std::atomic<uint8_t> ctl_bits_{0};
  std::atomic<uint8_t> stop_level_{1};
  std::atomic<bool> rereport_after_deactivate_{false};
  std::atomic<bool> color_read_pending_{false};
};

} // namespace esphome::matter

#endif // USE_MATTER
