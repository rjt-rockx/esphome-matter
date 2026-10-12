#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_LIGHT)

#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "matter_component.h"
#include "matter_conversions.h"
#include "matter_lights.h"

#include <algorithm>
#include <cmath>
#include <esp_matter_cluster.h>
#include <platform/CHIPDeviceLayer.h>

static const char *const TAG = "matter";

namespace esphome::matter {

namespace {

// CHIP steps level and colour transitions itself and writes every step to the
// attribute store, but the transition time of the command is not visible to
// us. Steps that follow each other within this window are treated as one
// running transition and are applied with a transition as long as the time
// since the previous step, so the ESPHome light fades between the steps.
constexpr uint32_t STREAM_WINDOW_MS = 300;
// Shorter intervals are not worth a transition. They are applied instantly.
constexpr uint32_t MIN_TRANSITION_MS = 20;
// Tolerance when comparing normalized RGB values.
constexpr float RGB_EPSILON = 1.0f / 256.0f;

enum class ActiveColor : uint8_t { NONE, COLOR, CT };

optional<MatterColorTemperatureRange>
get_color_temperature_range(const light::LightTraits &traits) {
  float min_mireds = traits.get_min_mireds();
  float max_mireds = traits.get_max_mireds();
  if (min_mireds <= 0.0f || max_mireds <= 0.0f)
    return nullopt;
  return MatterColorTemperatureRange{
      conversion::to_matter::color_temperature(min_mireds),
      conversion::to_matter::color_temperature(max_mireds),
  };
}

// Determines what the light is currently showing. The capability bits of the
// ESPHome color mode only say what the light is able to do: modes such as
// RGB_COLD_WARM_WHITE contain both RGB and white capabilities. In those modes
// the colour brightness tells if the RGB channels are in use or not.
ActiveColor get_active_color(const light::LightColorValues &values) {
  auto mode = values.get_color_mode();
  bool rgb = static_cast<bool>(mode & light::ColorCapability::RGB);
  bool ct = static_cast<bool>(mode & light::ColorCapability::COLOR_TEMPERATURE) ||
            static_cast<bool>(mode & light::ColorCapability::COLD_WARM_WHITE);
  if (rgb && ct)
    return values.get_color_brightness() > 0.0f ? ActiveColor::COLOR
                                                : ActiveColor::CT;
  if (rgb)
    return ActiveColor::COLOR;
  if (ct)
    return ActiveColor::CT;
  return ActiveColor::NONE;
}

bool nearly_equal(float a, float b) { return std::fabs(a - b) < RGB_EPSILON; }

// Everything push_state_to_matter() reports. It is captured by value in the
// lambda that runs on the CHIP task.
struct LightReport {
  bool on{false};
  bool has_level{false};
  uint8_t level{1};
  bool report_color{false};
  bool report_ct{false};
  bool has_xy{false};
  bool has_hs{false};
  bool has_enhanced_hue{false};
  bool has_enhanced_color_mode{false};
  uint16_t x{0};
  uint16_t y{0};
  uint8_t hue{0};
  uint8_t saturation{0};
  uint16_t enhanced_hue{0};
  uint16_t color_temperature{0};
};

} // namespace

void MatterComponent::register_light(light::LightState *light,
                                     uint16_t endpoint_id) {
  this->mappings_.push_back(new MatterLightMapping(light, endpoint_id));
}

MatterLightMapping::MatterLightMapping(light::LightState *light,
                                       uint16_t endpoint_id)
    : MatterEndpointMappingBase(endpoint_id), light_(light) {}

bool MatterLightMapping::has_attribute_(uint32_t cluster_id,
                                        uint32_t attribute_id) const {
  return esp_matter::attribute::get(this->endpoint_id(), cluster_id,
                                    attribute_id) != nullptr;
}

void MatterLightMapping::initialize() {
  if (this->light_ == nullptr)
    return;

  using namespace chip::app::Clusters;

  // Initialize capabilities
  this->capabilities_.has_level =
      this->has_server_cluster(LevelControl::Id);
  bool has_color_control = this->has_server_cluster(ColorControl::Id);
  if (has_color_control) {
    this->capabilities_.has_xy = this->has_attribute_(
                                     ColorControl::Id,
                                     ColorControl::Attributes::CurrentX::Id) &&
                                 this->has_attribute_(
                                     ColorControl::Id,
                                     ColorControl::Attributes::CurrentY::Id);
    this->capabilities_.has_hs =
        this->has_attribute_(ColorControl::Id,
                             ColorControl::Attributes::CurrentHue::Id) &&
        this->has_attribute_(ColorControl::Id,
                             ColorControl::Attributes::CurrentSaturation::Id);
    this->capabilities_.has_enhanced_hue =
        this->capabilities_.has_hs &&
        this->has_attribute_(
            ColorControl::Id,
            ColorControl::Attributes::EnhancedCurrentHue::Id);
    this->capabilities_.has_enhanced_color_mode = this->has_attribute_(
        ColorControl::Id, ColorControl::Attributes::EnhancedColorMode::Id);
    this->capabilities_.color_temperature_range =
        get_color_temperature_range(this->light_->get_traits());
    if (!this->has_attribute_(
            ColorControl::Id,
            ColorControl::Attributes::ColorTemperatureMireds::Id))
      this->capabilities_.color_temperature_range = nullopt;
  }
  this->capabilities_.has_color =
      this->capabilities_.has_xy || this->capabilities_.has_hs;
  bool has_ct = this->capabilities_.color_temperature_range.has_value();

  // Register attribute callbacks. They run on the CHIP task so the work is
  // deferred to the main loop, where it is merged into a single light call.
  global_matter_component->register_attribute_callback(
      this->endpoint_id(), OnOff::Id, OnOff::Attributes::OnOff::Id,
      [this](const esp_matter_attr_val_t &value) {
        MatterLightPendingUpdate update;
        update.on = value.val.b;
        global_matter_component->defer_to_main_loop(
            [this, update]() { this->queue_update_(update); });
      });
  if (this->capabilities_.has_level) {
    global_matter_component->register_attribute_callback(
        this->endpoint_id(), LevelControl::Id,
        LevelControl::Attributes::CurrentLevel::Id,
        [this](const esp_matter_attr_val_t &value) {
          MatterLightPendingUpdate update;
          update.level = value.val.u8;
          global_matter_component->defer_to_main_loop(
              [this, update]() { this->queue_update_(update); });
        });
  }
  if (this->capabilities_.has_color || has_ct) {
    // Which of the colour attributes is relevant depends on the ColorMode, so
    // every one of them triggers a (coalesced) read of the active ones.
    MatterAttributeCallback color_callback =
        [this](const esp_matter_attr_val_t &) { this->schedule_color_read_(); };
    auto register_color_attribute = [&](uint32_t attribute_id) {
      global_matter_component->register_attribute_callback(
          this->endpoint_id(), ColorControl::Id, attribute_id, color_callback);
    };
    register_color_attribute(ColorControl::Attributes::ColorMode::Id);
    if (this->capabilities_.has_enhanced_color_mode)
      register_color_attribute(ColorControl::Attributes::EnhancedColorMode::Id);
    if (this->capabilities_.has_xy) {
      register_color_attribute(ColorControl::Attributes::CurrentX::Id);
      register_color_attribute(ColorControl::Attributes::CurrentY::Id);
    }
    if (this->capabilities_.has_hs) {
      register_color_attribute(ColorControl::Attributes::CurrentHue::Id);
      register_color_attribute(ColorControl::Attributes::CurrentSaturation::Id);
    }
    if (this->capabilities_.has_enhanced_hue)
      register_color_attribute(
          ColorControl::Attributes::EnhancedCurrentHue::Id);
    if (has_ct)
      register_color_attribute(
          ColorControl::Attributes::ColorTemperatureMireds::Id);
  }

  // Light calls on_light_remote_values_update() on updates.
  this->light_->add_remote_values_listener(this);

  // Restore the ESPHome light from the persisted Matter state. The values are
  // read in one go and applied as a single light call. That way the level and
  // colour are set before the light is turned on, so it doesn't flash at the
  // brightness and colour it happened to have before.
  chip::DeviceLayer::SystemLayer().ScheduleLambda([this]() {
    using namespace chip::app::Clusters;
    MatterLightPendingUpdate update;
    esp_matter_attr_val_t value;
    if (this->capabilities_.has_level &&
        esp_matter::attribute::get_val(
            this->endpoint_id(), LevelControl::Id,
            LevelControl::Attributes::CurrentLevel::Id, &value) == ESP_OK &&
        !value.is_null())
      update.level = value.val.u8;
    update.color = this->read_color_update_();
    if (esp_matter::attribute::get_val(this->endpoint_id(), OnOff::Id,
                                       OnOff::Attributes::OnOff::Id,
                                       &value) == ESP_OK &&
        !value.is_null())
      update.on = value.val.b;
    global_matter_component->defer_to_main_loop(
        [this, update]() { this->queue_update_(update); });
  });
}

// Runs on the CHIP task.
void MatterLightMapping::schedule_color_read_() {
  if (this->color_update_pending_)
    return;
  this->color_update_pending_ = true;
  // CurrentX and CurrentY (and hue and saturation) are updated separately.
  // Read them after the current Matter work item completes so a new
  // coordinate is not combined with the previous value of the other one.
  chip::DeviceLayer::SystemLayer().ScheduleLambda([this]() {
    this->color_update_pending_ = false;
    auto color = this->read_color_update_();
    if (!color.has_value())
      return;
    MatterLightPendingUpdate update;
    update.color = color;
    global_matter_component->defer_to_main_loop(
        [this, update]() { this->queue_update_(update); });
  });
}

// Reads the colour attributes that belong to the active (Enhanced)ColorMode.
// Runs on the CHIP task.
optional<MatterLightColorUpdate> MatterLightMapping::read_color_update_() {
  using namespace chip::app::Clusters;
  using Kind = MatterLightColorUpdate::Kind;
  uint16_t eid = this->endpoint_id();
  if (!this->capabilities_.has_color &&
      !this->capabilities_.color_temperature_range.has_value())
    return nullopt;

  auto read_u8 = [eid](uint32_t attribute_id, uint8_t &out) {
    esp_matter_attr_val_t value;
    if (esp_matter::attribute::get_val(eid, ColorControl::Id, attribute_id,
                                       &value) != ESP_OK ||
        value.is_null())
      return false;
    out = value.val.u8;
    return true;
  };
  auto read_u16 = [eid](uint32_t attribute_id, uint16_t &out) {
    esp_matter_attr_val_t value;
    if (esp_matter::attribute::get_val(eid, ColorControl::Id, attribute_id,
                                       &value) != ESP_OK ||
        value.is_null())
      return false;
    out = value.val.u16;
    return true;
  };

  // EnhancedColorMode: 0 = HS, 1 = XY, 2 = CT, 3 = enhanced HS. ColorMode only
  // knows the first three.
  uint8_t mode = 0;
  if (!read_u8(this->capabilities_.has_enhanced_color_mode
                   ? ColorControl::Attributes::EnhancedColorMode::Id
                   : ColorControl::Attributes::ColorMode::Id,
               mode))
    return nullopt;

  MatterLightColorUpdate update;
  switch (mode) {
  case 0: { // CurrentHueAndCurrentSaturation
    uint8_t hue;
    uint8_t saturation;
    if (!this->capabilities_.has_hs ||
        !read_u8(ColorControl::Attributes::CurrentHue::Id, hue) ||
        !read_u8(ColorControl::Attributes::CurrentSaturation::Id, saturation))
      return nullopt;
    update.kind = Kind::HS;
    update.first = hue;
    update.second = saturation;
    return update;
  }
  case 1: { // CurrentXAndCurrentY
    uint16_t x;
    uint16_t y;
    if (!this->capabilities_.has_xy ||
        !read_u16(ColorControl::Attributes::CurrentX::Id, x) ||
        !read_u16(ColorControl::Attributes::CurrentY::Id, y))
      return nullopt;
    update.kind = Kind::XY;
    update.first = x;
    update.second = y;
    return update;
  }
  case 2: { // ColorTemperatureMireds
    uint16_t mireds;
    if (!this->capabilities_.color_temperature_range.has_value() ||
        !read_u16(ColorControl::Attributes::ColorTemperatureMireds::Id,
                  mireds))
      return nullopt;
    update.kind = Kind::CT;
    update.first = mireds;
    return update;
  }
  case 3: { // EnhancedCurrentHueAndCurrentSaturation
    uint16_t enhanced_hue;
    uint8_t saturation;
    if (!this->capabilities_.has_enhanced_hue ||
        !read_u16(ColorControl::Attributes::EnhancedCurrentHue::Id,
                  enhanced_hue) ||
        !read_u8(ColorControl::Attributes::CurrentSaturation::Id, saturation))
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

void MatterLightMapping::on_light_remote_values_update() {
  if (this->synchronizing_from_matter_)
    return;
  // ESPHome publishes the values of a transition again while it runs (when
  // configured to do so) and when a call finishes. These are the values that
  // came from Matter, which Matter already has.
  if (this->applied_values_valid_) {
    if (this->light_->remote_values == this->applied_values_)
      return;
    this->applied_values_valid_ = false;
  }
  this->push_state_to_matter();
}

// Pushes the whole state of the light to Matter. esp-matter "deduplicates"
// attribute updates so it's okay to not check if the values are changed.
void MatterLightMapping::push_state_to_matter() {
  uint16_t eid = this->endpoint_id();
  const auto &values = this->light_->remote_values;

  LightReport report;
  report.on = values.is_on();
  report.has_level = this->capabilities_.has_level;
  report.level = conversion::to_matter::brightness(values.get_brightness());
  report.has_xy = this->capabilities_.has_xy;
  report.has_hs = this->capabilities_.has_hs;
  report.has_enhanced_hue = this->capabilities_.has_enhanced_hue;
  report.has_enhanced_color_mode = this->capabilities_.has_enhanced_color_mode;

  // Report the colour that the light is showing right now.
  ActiveColor active = get_active_color(values);
  if (active == ActiveColor::COLOR && this->capabilities_.has_color) {
    float r = values.get_red();
    float g = values.get_green();
    float b = values.get_blue();
    bool changed = !this->reported_color_valid_ ||
                   this->reported_color_is_ct_ ||
                   !nearly_equal(r, this->reported_red_) ||
                   !nearly_equal(g, this->reported_green_) ||
                   !nearly_equal(b, this->reported_blue_);
    // Black has no xy or hue. Leave the Matter colour as it is.
    if (changed &&
        conversion::to_matter::color(r, g, b, report.x, report.y) &&
        conversion::to_matter::hue_saturation(r, g, b, report.hue,
                                              report.saturation,
                                              report.enhanced_hue)) {
      report.report_color = true;
      this->reported_color_valid_ = true;
      this->reported_color_is_ct_ = false;
      this->reported_red_ = r;
      this->reported_green_ = g;
      this->reported_blue_ = b;
    }
  } else if (active == ActiveColor::CT &&
             this->capabilities_.color_temperature_range.has_value()) {
    float mireds = values.get_color_temperature();
    bool changed = !this->reported_color_valid_ ||
                   !this->reported_color_is_ct_ ||
                   std::fabs(mireds - this->reported_color_temperature_) >= 0.5f;
    if (changed) {
      report.report_ct = true;
      report.color_temperature = conversion::to_matter::color_temperature(mireds);
      this->reported_color_valid_ = true;
      this->reported_color_is_ct_ = true;
      this->reported_color_temperature_ = mireds;
    }
  }

  chip::DeviceLayer::SystemLayer().ScheduleLambda([eid, report]() {
    using namespace chip::app::Clusters;
    esp_matter_attr_val_t on_val = esp_matter_bool(report.on);
    esp_matter::attribute::report(eid, OnOff::Id, OnOff::Attributes::OnOff::Id,
                                  &on_val);
    if (report.has_level) {
      esp_matter_attr_val_t level_val =
          esp_matter_nullable_uint8(nullable<uint8_t>(report.level));
      esp_matter::attribute::report(
          eid, LevelControl::Id, LevelControl::Attributes::CurrentLevel::Id,
          &level_val);
    }
    if (report.report_color) {
      // Report the colour in every representation the endpoint has, so that
      // controllers using either one see the same colour. The mode is
      // reported last. XY is the preferred mode.
      uint8_t mode = static_cast<uint8_t>(
          report.has_xy ? ColorControl::ColorModeEnum::kCurrentXAndCurrentY
                        : ColorControl::ColorModeEnum::
                              kCurrentHueAndCurrentSaturation);
      if (report.has_xy) {
        esp_matter_attr_val_t x_val = esp_matter_uint16(report.x);
        esp_matter::attribute::report(eid, ColorControl::Id,
                                      ColorControl::Attributes::CurrentX::Id,
                                      &x_val);
        esp_matter_attr_val_t y_val = esp_matter_uint16(report.y);
        esp_matter::attribute::report(eid, ColorControl::Id,
                                      ColorControl::Attributes::CurrentY::Id,
                                      &y_val);
      }
      if (report.has_hs) {
        esp_matter_attr_val_t hue_val = esp_matter_uint8(report.hue);
        esp_matter::attribute::report(
            eid, ColorControl::Id, ColorControl::Attributes::CurrentHue::Id,
            &hue_val);
        esp_matter_attr_val_t saturation_val =
            esp_matter_uint8(report.saturation);
        esp_matter::attribute::report(
            eid, ColorControl::Id,
            ColorControl::Attributes::CurrentSaturation::Id, &saturation_val);
      }
      if (report.has_enhanced_hue) {
        esp_matter_attr_val_t enhanced_hue_val =
            esp_matter_uint16(report.enhanced_hue);
        esp_matter::attribute::report(
            eid, ColorControl::Id,
            ColorControl::Attributes::EnhancedCurrentHue::Id,
            &enhanced_hue_val);
      }
      esp_matter_attr_val_t color_mode_val = esp_matter_enum8(mode);
      esp_matter::attribute::report(eid, ColorControl::Id,
                                    ColorControl::Attributes::ColorMode::Id,
                                    &color_mode_val);
      if (report.has_enhanced_color_mode)
        esp_matter::attribute::report(
            eid, ColorControl::Id,
            ColorControl::Attributes::EnhancedColorMode::Id, &color_mode_val);
    }
    if (report.report_ct) {
      esp_matter_attr_val_t color_temperature_val =
          esp_matter_uint16(report.color_temperature);
      esp_matter::attribute::report(
          eid, ColorControl::Id,
          ColorControl::Attributes::ColorTemperatureMireds::Id,
          &color_temperature_val);
      esp_matter_attr_val_t color_mode_val =
          esp_matter_enum8(static_cast<uint8_t>(
              ColorControl::ColorModeEnum::kColorTemperatureMireds));
      esp_matter::attribute::report(eid, ColorControl::Id,
                                    ColorControl::Attributes::ColorMode::Id,
                                    &color_mode_val);
      if (report.has_enhanced_color_mode)
        esp_matter::attribute::report(
            eid, ColorControl::Id,
            ColorControl::Attributes::EnhancedColorMode::Id, &color_mode_val);
    }
  });
}

// Merges the update into the pending one and applies it on the next iteration
// of the main loop, after all updates that were already queued.
void MatterLightMapping::queue_update_(MatterLightPendingUpdate update) {
  if (update.on.has_value())
    this->pending_.on = update.on;
  if (update.level.has_value())
    this->pending_.level = update.level;
  if (update.color.has_value())
    this->pending_.color = update.color;
  if (this->apply_scheduled_)
    return;
  this->apply_scheduled_ = true;
  global_matter_component->defer_to_main_loop(
      [this]() { this->apply_pending_(); });
}

// Adds the Matter colour to the light call. Returns false when the call
// doesn't need to change (or the colour can't be applied).
bool MatterLightMapping::add_color_to_call_(
    light::LightCall &call, const MatterLightColorUpdate &color) {
  using Kind = MatterLightColorUpdate::Kind;
  const auto &values = this->light_->remote_values;
  bool is_active_color = get_active_color(values) == ActiveColor::COLOR;

  if (color.kind == Kind::CT) {
    if (!this->capabilities_.color_temperature_range.has_value())
      return false;
    float mireds = static_cast<float>(color.first);
    if (get_active_color(values) == ActiveColor::CT &&
        std::fabs(values.get_color_temperature() - mireds) < 0.5f)
      return false;
    call.set_color_temperature(mireds);
    // Lights that can show RGB and white at the same time (e.g. RGBWW without
    // color_interlock) are put in white mode by dropping the RGB part.
    call.set_color_brightness_if_supported(0.0f);
    return true;
  }

  float red = 0.0f;
  float green = 0.0f;
  float blue = 0.0f;
  bool converted = false;
  switch (color.kind) {
  case Kind::XY:
    converted = conversion::from_matter::color(color.first, color.second, red,
                                               green, blue);
    break;
  case Kind::HS:
    converted = conversion::from_matter::hue_saturation(
        static_cast<uint8_t>(color.first), static_cast<uint8_t>(color.second),
        red, green, blue);
    break;
  case Kind::ENHANCED_HS:
    converted = conversion::from_matter::enhanced_hue_saturation(
        color.first, static_cast<uint8_t>(color.second), red, green, blue);
    break;
  default:
    break;
  }
  if (!converted) {
    ESP_LOGW(TAG,
             "Failed to convert Matter color: endpoint=%u, kind=%u, first=%u, "
             "second=%u",
             this->endpoint_id(), static_cast<unsigned>(color.kind),
             color.first, color.second);
    return false;
  }
  if (is_active_color && nearly_equal(values.get_red(), red) &&
      nearly_equal(values.get_green(), green) &&
      nearly_equal(values.get_blue(), blue))
    return false;
  call.set_rgb(red, green, blue);
  return true;
}

void MatterLightMapping::apply_pending_() {
  this->apply_scheduled_ = false;
  MatterLightPendingUpdate update = this->pending_;
  this->pending_ = MatterLightPendingUpdate{};

  const auto &values = this->light_->remote_values;
  auto call = this->light_->make_call();
  bool changed = false;

  if (update.on.has_value() && values.is_on() != *update.on) {
    call.set_state(*update.on);
    changed = true;
  }
  if (update.level.has_value()) {
    float brightness = conversion::from_matter::brightness(*update.level);
    if (std::fabs(values.get_brightness() - brightness) >= (0.5f / 254.0f)) {
      call.set_brightness(brightness);
      changed = true;
    }
  }
  if (update.color.has_value() &&
      this->add_color_to_call_(call, *update.color))
    changed = true;
  if (!changed)
    return;

  // CHIP moves to the target of a transition in small steps and writes every
  // one of them. Don't fight that by jumping to each step instantly: when this
  // update directly follows a previous one, let the light fade for the time
  // that passed between the two. A lone change is applied instantly.
  uint32_t now = millis();
  uint32_t transition_ms = 0;
  if (this->last_apply_valid_) {
    uint32_t elapsed = now - this->last_apply_ms_;
    if (elapsed >= MIN_TRANSITION_MS && elapsed <= STREAM_WINDOW_MS)
      transition_ms = elapsed;
  }
  this->last_apply_ms_ = now;
  this->last_apply_valid_ = true;
  call.set_transition_length(transition_ms);

  this->synchronizing_from_matter_ = true;
  call.perform();
  this->synchronizing_from_matter_ = false;

  // Matter already has these values. Remember them so their echo is ignored,
  // also when it is published later on and the flag above is no longer set.
  this->applied_values_ = this->light_->remote_values;
  this->applied_values_valid_ = true;
  const auto &applied = this->light_->remote_values;
  switch (get_active_color(applied)) {
  case ActiveColor::COLOR:
    this->reported_color_valid_ = true;
    this->reported_color_is_ct_ = false;
    this->reported_red_ = applied.get_red();
    this->reported_green_ = applied.get_green();
    this->reported_blue_ = applied.get_blue();
    break;
  case ActiveColor::CT:
    this->reported_color_valid_ = true;
    this->reported_color_is_ct_ = true;
    this->reported_color_temperature_ = applied.get_color_temperature();
    break;
  default:
    break;
  }
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_LIGHT
