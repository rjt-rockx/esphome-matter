#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_LIGHT)

#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "matter_component.h"
#include "matter_conversions.h"
#include "matter_lights.h"

#include <algorithm>
#include <app-common/zap-generated/cluster-objects.h>
#include <app/ConcreteCommandPath.h>
#include <app/data-model/Decode.h>
#include <cinttypes>
#include <cmath>
#include <esp_matter_cluster.h>
#include <esp_matter_command.h>
#include <lib/core/TLVReader.h>
#include <mutex>
#include <platform/CHIPDeviceLayer.h>
#include <vector>

static const char *const TAG = "matter";

namespace esphome::matter {

namespace {

// Fallback for changes that don't come from a command with a transition time
// (Move, Step and ColorLoop run at a rate, and CHIP writes every step to the
// attribute store). Steps that follow each other within this window are
// treated as one running transition and are applied with a transition as long
// as the time since the previous step, so the ESPHome light fades between
// the steps.
constexpr uint32_t STREAM_WINDOW_MS = 300;
// Shorter intervals are not worth a transition. They are applied instantly.
constexpr uint32_t MIN_TRANSITION_MS = 20;
// Tolerance when comparing normalized RGB values.
constexpr float RGB_EPSILON = 1.0f / 256.0f;

// After a command with a transition, CHIP's own writes for the same attributes
// are ignored for the transition time plus this margin (CHIP's last step lands
// a little after the nominal end). Then the light is synchronized with the
// final attribute values, which corrects clamping and other differences.
constexpr uint32_t COMMAND_MARGIN_MS = 500;
// Transition times in commands are in 1/10 s.
constexpr uint32_t DECISECOND_MS = 100;
// Highest valid CurrentLevel, CurrentHue and CurrentSaturation values.
constexpr uint8_t MATTER_MAX_LEVEL = 254;

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

// True while `now` is before `until` (also across a wrap of millis()).
bool is_before(uint32_t now, uint32_t until) {
  return static_cast<int32_t>(until - now) > 0;
}

// Attribute reads for the CHIP task. Return false when the attribute doesn't
// exist or is null.
bool read_attribute(uint16_t endpoint_id, uint32_t cluster_id,
                    uint32_t attribute_id, esp_matter_attr_val_t &value) {
  return esp_matter::attribute::get_val(endpoint_id, cluster_id, attribute_id,
                                        &value) == ESP_OK &&
         !value.is_null();
}
bool read_bool_attribute(uint16_t endpoint_id, uint32_t cluster_id,
                         uint32_t attribute_id, bool &out) {
  esp_matter_attr_val_t value;
  if (!read_attribute(endpoint_id, cluster_id, attribute_id, value))
    return false;
  out = value.val.b;
  return true;
}
bool read_u8_attribute(uint16_t endpoint_id, uint32_t cluster_id,
                       uint32_t attribute_id, uint8_t &out) {
  esp_matter_attr_val_t value;
  if (!read_attribute(endpoint_id, cluster_id, attribute_id, value))
    return false;
  out = value.val.u8;
  return true;
}
bool read_u16_attribute(uint16_t endpoint_id, uint32_t cluster_id,
                        uint32_t attribute_id, uint16_t &out) {
  esp_matter_attr_val_t value;
  if (!read_attribute(endpoint_id, cluster_id, attribute_id, value))
    return false;
  out = value.val.u16;
  return true;
}

// Converts the 1/10 s of a command to ms.
uint32_t command_transition_ms(uint16_t transition_ds) {
  return static_cast<uint32_t>(transition_ds) * DECISECOND_MS;
}

// esp-matter user callbacks are plain function pointers without a context, so
// the light that owns the endpoint is looked up here. Registered once during
// setup, read on the CHIP task.
std::mutex &light_mappings_mutex() {
  static std::mutex mutex;
  return mutex;
}
std::vector<MatterLightMapping *> &light_mappings() {
  static std::vector<MatterLightMapping *> mappings;
  return mappings;
}

// esp_matter::command::callback_t. Runs on the CHIP task before CHIP executes
// the command. Always returns ESP_OK so that CHIP still handles the command.
esp_err_t light_command_callback(const chip::app::ConcreteCommandPath &path,
                                 chip::TLV::TLVReader &reader, void *) {
  MatterLightMapping *mapping = nullptr;
  {
    std::lock_guard<std::mutex> lock(light_mappings_mutex());
    for (auto *candidate : light_mappings()) {
      if (candidate->endpoint_id() == path.mEndpointId) {
        mapping = candidate;
        break;
      }
    }
  }
  if (mapping != nullptr)
    mapping->handle_command(path.mClusterId, path.mCommandId, reader);
  return ESP_OK;
}

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
                                     uint16_t endpoint_id,
                                     const char *color_loop_effect) {
  this->mappings_.push_back(
      new MatterLightMapping(light, endpoint_id, color_loop_effect));
}

MatterLightMapping::MatterLightMapping(light::LightState *light,
                                       uint16_t endpoint_id,
                                       const char *color_loop_effect)
    : MatterEndpointMappingBase(endpoint_id), light_(light),
      color_loop_effect_(color_loop_effect) {}

// Mirrors esp_matter::endpoint::extended_color_light::add() without adding the
// ColorTemperature feature unconditionally.
esp_err_t add_extended_color_light(
    esp_matter::endpoint_t *endpoint,
    esp_matter::endpoint::extended_color_light::config_t *config,
    light::LightState *light) {
  using namespace esp_matter;
  namespace el = esp_matter::endpoint::extended_color_light;
  esp_err_t err = endpoint::add_device_type(endpoint, el::get_device_type_id(),
                                            el::get_device_type_version());
  if (err != ESP_OK)
    return err;

  cluster_t *identify_cluster = cluster::identify::create(
      endpoint, &(config->identify), CLUSTER_FLAG_SERVER);
  cluster::identify::command::create_trigger_effect(identify_cluster);
  cluster::groups::create(endpoint, &(config->groups), CLUSTER_FLAG_SERVER);
  cluster_t *on_off_cluster = cluster::on_off::create(
      endpoint, &(config->on_off), CLUSTER_FLAG_SERVER);
  cluster::on_off::feature::lighting::add(on_off_cluster,
                                          &(config->on_off_lighting));
  cluster::on_off::command::create_on(on_off_cluster);
  cluster::on_off::command::create_toggle(on_off_cluster);
  cluster_t *level_control_cluster = cluster::level_control::create(
      endpoint, &(config->level_control), CLUSTER_FLAG_SERVER);
  cluster::level_control::feature::on_off::add(level_control_cluster);
  cluster::level_control::feature::lighting::add(
      level_control_cluster, &(config->level_control_lighting));
  cluster_t *color_control_cluster = cluster::color_control::create(
      endpoint, &(config->color_control), CLUSTER_FLAG_SERVER);
  if (get_color_temperature_range(light->get_traits()).has_value()) {
    cluster::color_control::feature::color_temperature::add(
        color_control_cluster, &(config->color_control_color_temperature));
  } else {
    ESP_LOGI(TAG,
             "Light '%s' has no color temperature range, the endpoint doesn't "
             "advertise the ColorTemperature feature",
             light->get_name().c_str());
  }
  cluster::color_control::feature::xy::add(color_control_cluster,
                                           &(config->color_control_xy));
  cluster::color_control::attribute::create_remaining_time(
      color_control_cluster, config->color_control_remaining_time);
  cluster::color_control::command::create_stop_move_step(color_control_cluster);
  cluster_t *scenes_management_cluster = cluster::scenes_management::create(
      endpoint, &(config->scenes_management), CLUSTER_FLAG_SERVER);
  cluster::scenes_management::command::create_copy_scene(
      scenes_management_cluster);
  cluster::scenes_management::command::create_copy_scene_response(
      scenes_management_cluster);
  return ESP_OK;
}

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
          // CurrentLevel is nullable.
          if (value.is_null())
            return;
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
  this->schedule_full_read_(false, true, true);

  this->register_commands_();
}

// Reads OnOff, the level and the active colour from the attribute store.
// Runs on the CHIP task.
MatterLightPendingUpdate MatterLightMapping::read_state_() {
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
  return update;
}

// Reads the state on the CHIP task (after the work item that is running now)
// and applies it on the main loop. Settling applies the values instantly, for
// the groups that are asked for.
void MatterLightMapping::schedule_full_read_(bool settle, bool light,
                                             bool color) {
  chip::DeviceLayer::SystemLayer().ScheduleLambda([this, settle, light,
                                                   color]() {
    MatterLightPendingUpdate update = this->read_state_();
    if (!light) {
      update.on.reset();
      update.level.reset();
    }
    if (!color)
      update.color.reset();
    update.settle = settle;
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

  // Report the colour that the light is showing right now. While a color loop
  // effect runs the color is the effect's business, CHIP's loop attributes
  // are what controllers see.
  ActiveColor active = this->color_loop_active_ ? ActiveColor::NONE
                                                : get_active_color(values);
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
  // CHIP walks to the target of a command in small steps and writes every one
  // of them. The light was already sent to the target of that command with
  // the real transition time, so the steps are ignored while it runs.
  uint32_t now = millis();
  if (this->light_group_suppressed_(now)) {
    update.on.reset();
    update.level.reset();
  }
  if (this->color_group_suppressed_(now))
    update.color.reset();
  if (!update.on.has_value() && !update.level.has_value() &&
      !update.color.has_value())
    return;
  if (update.settle)
    this->pending_.settle = true;
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
    light::LightCall &call, const MatterLightColorUpdate &color, bool force) {
  using Kind = MatterLightColorUpdate::Kind;
  const auto &values = this->light_->remote_values;
  bool is_active_color = get_active_color(values) == ActiveColor::COLOR;

  if (color.kind == Kind::CT) {
    if (!this->capabilities_.color_temperature_range.has_value())
      return false;
    float mireds = static_cast<float>(color.first);
    if (!force && get_active_color(values) == ActiveColor::CT &&
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
  if (!force && is_active_color && nearly_equal(values.get_red(), red) &&
      nearly_equal(values.get_green(), green) &&
      nearly_equal(values.get_blue(), blue))
    return false;
  call.set_rgb(red, green, blue);
  // The counterpart of the color temperature case: don't leave the white
  // channels of an RGBWW light on, which would wash out the color.
  call.set_white_if_supported(0.0f);
  call.set_cold_white_if_supported(0.0f);
  call.set_warm_white_if_supported(0.0f);
  return true;
}

void MatterLightMapping::apply_pending_() {
  this->apply_scheduled_ = false;
  MatterLightPendingUpdate update = this->pending_;
  this->pending_ = MatterLightPendingUpdate{};
  // Settling applies the values even when they look unchanged: the light may
  // still be fading to a target that Matter didn't reach (Stop command).
  bool force = update.settle;

  const auto &values = this->light_->remote_values;
  auto call = this->light_->make_call();
  bool changed = false;

  if (update.on.has_value() && (force || values.is_on() != *update.on)) {
    call.set_state(*update.on);
    changed = true;
  }
  if (update.level.has_value()) {
    float brightness = conversion::from_matter::brightness(*update.level);
    if (force ||
        std::fabs(values.get_brightness() - brightness) >= (0.5f / 254.0f)) {
      call.set_brightness(brightness);
      changed = true;
    }
  }
  if (update.color.has_value() &&
      this->add_color_to_call_(call, *update.color, force))
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
    uint32_t now = millis();
    if (this->last_apply_valid_) {
      uint32_t elapsed = now - this->last_apply_ms_;
      if (elapsed >= MIN_TRANSITION_MS && elapsed <= STREAM_WINDOW_MS)
        transition_ms = elapsed;
    }
    this->last_apply_ms_ = now;
    this->last_apply_valid_ = true;
  }
  // Lights without brightness control (on/off) can't transition at all.
  call.set_transition_length_if_supported(transition_ms);

  this->synchronizing_from_matter_ = true;
  call.perform();
  this->synchronizing_from_matter_ = false;
  this->remember_applied_values_();
}

// Matter already has the values that were just applied. Remember them so
// their echo is ignored, also when it is published later on and the flag
// above is no longer set.
void MatterLightMapping::remember_applied_values_() {
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

bool MatterLightMapping::light_group_suppressed_(uint32_t now) const {
  return this->light_window_active_ &&
         is_before(now, this->light_window_until_ms_);
}

bool MatterLightMapping::color_group_suppressed_(uint32_t now) const {
  // The steps of a color loop belong to the effect of the light.
  if (this->color_loop_active_)
    return true;
  return this->color_window_active_ &&
         is_before(now, this->color_window_until_ms_);
}

// ---- Commands with a transition time --------------------------------------

// Registers the user callback of the commands that move a light to a target.
// Rate based commands (Move, Step, ColorLoopSet) are left to the heuristic
// above.
void MatterLightMapping::register_commands_() {
  using namespace chip::app::Clusters;
  {
    std::lock_guard<std::mutex> lock(light_mappings_mutex());
    light_mappings().push_back(this);
  }
  uint16_t eid = this->endpoint_id();
  auto hook = [eid](uint32_t cluster_id, uint32_t command_id) {
    auto *command = esp_matter::command::get(eid, cluster_id, command_id);
    if (command == nullptr)
      return; // The endpoint doesn't have this command.
    auto existing = esp_matter::command::get_user_callback(command);
    if (existing != nullptr && existing != light_command_callback) {
      ESP_LOGW(TAG,
               "Endpoint %u already has a handler for command 0x%" PRIX32
               " of cluster 0x%" PRIX32 ", its transition time is not used",
               eid, command_id, cluster_id);
      return;
    }
    esp_matter::command::set_user_callback(command, light_command_callback);
  };
  if (this->capabilities_.has_level) {
    hook(OnOff::Id, OnOff::Commands::On::Id);
    hook(OnOff::Id, OnOff::Commands::Off::Id);
    hook(OnOff::Id, OnOff::Commands::Toggle::Id);
    hook(LevelControl::Id, LevelControl::Commands::MoveToLevel::Id);
    hook(LevelControl::Id, LevelControl::Commands::MoveToLevelWithOnOff::Id);
    hook(LevelControl::Id, LevelControl::Commands::Stop::Id);
    hook(LevelControl::Id, LevelControl::Commands::StopWithOnOff::Id);
  }
  if (this->capabilities_.has_color ||
      this->capabilities_.color_temperature_range.has_value()) {
    hook(ColorControl::Id, ColorControl::Commands::MoveToColor::Id);
    hook(ColorControl::Id, ColorControl::Commands::MoveToColorTemperature::Id);
    hook(ColorControl::Id, ColorControl::Commands::MoveToHue::Id);
    hook(ColorControl::Id, ColorControl::Commands::MoveToSaturation::Id);
    hook(ColorControl::Id, ColorControl::Commands::MoveToHueAndSaturation::Id);
    hook(ColorControl::Id, ColorControl::Commands::EnhancedMoveToHue::Id);
    hook(ColorControl::Id,
         ColorControl::Commands::EnhancedMoveToHueAndSaturation::Id);
    hook(ColorControl::Id, ColorControl::Commands::StopMoveStep::Id);
    if (this->color_loop_effect_ != nullptr)
      hook(ColorControl::Id, ColorControl::Commands::ColorLoopSet::Id);
  }
}

// Whether CHIP executes a command of the cluster while the light is off. This
// follows CHIP: the ExecuteIfOff bit of the Options attribute, overridden by
// the command's OptionsMask and OptionsOverride.
bool MatterLightMapping::execute_if_off_(uint32_t cluster_id,
                                         uint32_t options_attribute_id,
                                         uint8_t options_mask,
                                         uint8_t options_override) {
  using namespace chip::app::Clusters;
  constexpr uint8_t EXECUTE_IF_OFF = 0x1;
  bool on = true;
  if (!read_bool_attribute(this->endpoint_id(), OnOff::Id,
                           OnOff::Attributes::OnOff::Id, on) ||
      on)
    return true;
  if (options_mask & EXECUTE_IF_OFF)
    return (options_override & EXECUTE_IF_OFF) != 0;
  uint8_t options = 0;
  read_u8_attribute(this->endpoint_id(), cluster_id, options_attribute_id,
                    options);
  return (options & EXECUTE_IF_OFF) != 0;
}

bool MatterLightMapping::decode_level_command_(uint32_t command_id,
                                               chip::TLV::TLVReader &reader,
                                               MatterLightCommand &command) {
  using namespace chip::app::Clusters;
  using chip::app::DataModel::Decode;
  uint16_t eid = this->endpoint_id();

  if (command_id == LevelControl::Commands::Stop::Id ||
      command_id == LevelControl::Commands::StopWithOnOff::Id) {
    command.stop_light = true;
    return true;
  }

  uint8_t level;
  chip::app::DataModel::Nullable<uint16_t> transition_ds;
  uint8_t options_mask;
  uint8_t options_override;
  bool with_on_off;
  if (command_id == LevelControl::Commands::MoveToLevel::Id) {
    LevelControl::Commands::MoveToLevel::DecodableType data;
    if (Decode(reader, data) != CHIP_NO_ERROR)
      return false;
    level = data.level;
    transition_ds = data.transitionTime;
    options_mask = data.optionsMask.Raw();
    options_override = data.optionsOverride.Raw();
    with_on_off = false;
  } else if (command_id == LevelControl::Commands::MoveToLevelWithOnOff::Id) {
    LevelControl::Commands::MoveToLevelWithOnOff::DecodableType data;
    if (Decode(reader, data) != CHIP_NO_ERROR)
      return false;
    level = data.level;
    transition_ds = data.transitionTime;
    options_mask = data.optionsMask.Raw();
    options_override = data.optionsOverride.Raw();
    with_on_off = true;
  } else {
    return false;
  }

  // CHIP rejects an out of range level and ignores the command when the light
  // is off, unless it is told to execute anyway.
  if (level > MATTER_MAX_LEVEL)
    return false;
  if (!with_on_off &&
      !this->execute_if_off_(LevelControl::Id,
                             LevelControl::Attributes::Options::Id,
                             options_mask, options_override))
    return false;

  // CHIP moves to the level, limited to MinLevel and MaxLevel.
  uint8_t min_level = 0;
  uint8_t max_level = MATTER_MAX_LEVEL;
  read_u8_attribute(eid, LevelControl::Id,
                    LevelControl::Attributes::MinLevel::Id, min_level);
  read_u8_attribute(eid, LevelControl::Id,
                    LevelControl::Attributes::MaxLevel::Id, max_level);
  level = std::min(max_level, std::max(min_level, level));
  command.level = level;
  // MoveToLevelWithOnOff turns the light off at the lowest level, and on at
  // any other level.
  if (with_on_off)
    command.on = level != min_level;

  uint16_t transition = 0;
  if (!transition_ds.IsNull()) {
    transition = transition_ds.Value();
  } else {
    // null: the time is taken from OnOffTransitionTime, or as fast as possible.
    read_u16_attribute(eid, LevelControl::Id,
                       LevelControl::Attributes::OnOffTransitionTime::Id,
                       transition);
  }
  command.transition_ms = command_transition_ms(transition);
  command.light_group = true;
  return true;
}

bool MatterLightMapping::decode_on_off_command_(uint32_t command_id,
                                                MatterLightCommand &command) {
  using namespace chip::app::Clusters;
  uint16_t eid = this->endpoint_id();
  // OnOff only fades a light with LevelControl. CHIP uses OnOffTransitionTime
  // for both turning on and off.
  if (!this->capabilities_.has_level)
    return false;

  bool on = false;
  read_bool_attribute(eid, OnOff::Id, OnOff::Attributes::OnOff::Id, on);
  bool target;
  if (command_id == OnOff::Commands::On::Id) {
    target = true;
  } else if (command_id == OnOff::Commands::Off::Id) {
    target = false;
  } else if (command_id == OnOff::Commands::Toggle::Id) {
    target = !on;
  } else {
    return false;
  }
  command.on = target;
  if (target) {
    // Turning on goes to OnLevel when it is set, else to the level that the
    // light had.
    uint8_t on_level;
    if (read_u8_attribute(eid, LevelControl::Id,
                          LevelControl::Attributes::OnLevel::Id, on_level)) {
      command.level = std::min<uint8_t>(on_level, MATTER_MAX_LEVEL);
      command.level_if_turning_on = true;
    }
  }
  // CHIP uses OnOffTransitionTime for both directions. The spec lets
  // OnTransitionTime and OffTransitionTime override it for the light (a null
  // value falls back to OnOffTransitionTime).
  uint16_t on_off_time = 0;
  read_u16_attribute(eid, LevelControl::Id,
                     LevelControl::Attributes::OnOffTransitionTime::Id,
                     on_off_time);
  uint16_t transition = on_off_time;
  read_u16_attribute(eid, LevelControl::Id,
                     target ? LevelControl::Attributes::OnTransitionTime::Id
                            : LevelControl::Attributes::OffTransitionTime::Id,
                     transition);
  command.transition_ms = command_transition_ms(transition);
  command.chip_transition_ms = command_transition_ms(on_off_time);
  command.light_group = true;
  return true;
}

bool MatterLightMapping::decode_color_command_(uint32_t command_id,
                                               chip::TLV::TLVReader &reader,
                                               MatterLightCommand &command) {
  using namespace chip::app::Clusters;
  using chip::app::DataModel::Decode;
  using Kind = MatterLightColorUpdate::Kind;
  uint16_t eid = this->endpoint_id();

  if (command_id == ColorControl::Commands::StopMoveStep::Id) {
    command.stop_color = true;
    return true;
  }

  if (command_id == ColorControl::Commands::ColorLoopSet::Id) {
    // The loop is the ESPHome effect of the light. CHIP still runs its own
    // loop on the attributes; those steps are ignored while the effect runs.
    ColorControl::Commands::ColorLoopSet::DecodableType data;
    if (this->color_loop_effect_ == nullptr ||
        Decode(reader, data) != CHIP_NO_ERROR ||
        !data.updateFlags.Has(ColorControl::UpdateFlagsBitmap::kUpdateAction) ||
        data.action == ColorControl::ColorLoopActionEnum::kUnknownEnumValue ||
        data.direction == ColorControl::ColorLoopDirectionEnum::kUnknownEnumValue ||
        !this->execute_if_off_(ColorControl::Id,
                               ColorControl::Attributes::Options::Id,
                               data.optionsMask.Raw(),
                               data.optionsOverride.Raw()))
      return false;
    command.color_loop =
        data.action == ColorControl::ColorLoopActionEnum::kDeactivate
            ? MatterLightCommand::ColorLoop::STOP
            : MatterLightCommand::ColorLoop::START;
    return true;
  }

  MatterLightColorUpdate color;
  uint16_t transition_ds = 0;
  uint8_t options_mask = 0;
  uint8_t options_override = 0;
  if (command_id == ColorControl::Commands::MoveToColor::Id) {
    ColorControl::Commands::MoveToColor::DecodableType data;
    if (Decode(reader, data) != CHIP_NO_ERROR)
      return false;
    color.kind = Kind::XY;
    color.first = data.colorX;
    color.second = data.colorY;
    transition_ds = data.transitionTime;
    options_mask = data.optionsMask.Raw();
    options_override = data.optionsOverride.Raw();
  } else if (command_id == ColorControl::Commands::MoveToColorTemperature::Id) {
    ColorControl::Commands::MoveToColorTemperature::DecodableType data;
    if (Decode(reader, data) != CHIP_NO_ERROR)
      return false;
    if (!this->capabilities_.color_temperature_range.has_value() ||
        data.colorTemperatureMireds == 0)
      return false;
    // CHIP limits the value to the range of the light.
    const auto &range = *this->capabilities_.color_temperature_range;
    color.kind = Kind::CT;
    color.first = std::min(
        std::max(data.colorTemperatureMireds, range.min_mireds),
        range.max_mireds);
    transition_ds = data.transitionTime;
    options_mask = data.optionsMask.Raw();
    options_override = data.optionsOverride.Raw();
  } else if (command_id == ColorControl::Commands::MoveToHue::Id) {
    ColorControl::Commands::MoveToHue::DecodableType data;
    if (Decode(reader, data) != CHIP_NO_ERROR)
      return false;
    // MoveToHue keeps the saturation. The direction can't be followed:
    // ESPHome fades in RGB space.
    uint8_t saturation;
    if (data.hue > MATTER_MAX_LEVEL ||
        !read_u8_attribute(eid, ColorControl::Id,
                           ColorControl::Attributes::CurrentSaturation::Id,
                           saturation))
      return false;
    color.kind = Kind::HS;
    color.first = data.hue;
    color.second = saturation;
    transition_ds = data.transitionTime;
    options_mask = data.optionsMask.Raw();
    options_override = data.optionsOverride.Raw();
  } else if (command_id == ColorControl::Commands::MoveToSaturation::Id) {
    ColorControl::Commands::MoveToSaturation::DecodableType data;
    if (Decode(reader, data) != CHIP_NO_ERROR)
      return false;
    if (data.saturation > MATTER_MAX_LEVEL)
      return false;
    // MoveToSaturation keeps the hue.
    uint16_t enhanced_hue;
    uint8_t hue;
    if (this->capabilities_.has_enhanced_hue &&
        read_u16_attribute(eid, ColorControl::Id,
                           ColorControl::Attributes::EnhancedCurrentHue::Id,
                           enhanced_hue)) {
      color.kind = Kind::ENHANCED_HS;
      color.first = enhanced_hue;
    } else if (read_u8_attribute(eid, ColorControl::Id,
                                 ColorControl::Attributes::CurrentHue::Id,
                                 hue)) {
      color.kind = Kind::HS;
      color.first = hue;
    } else {
      return false;
    }
    color.second = data.saturation;
    transition_ds = data.transitionTime;
    options_mask = data.optionsMask.Raw();
    options_override = data.optionsOverride.Raw();
  } else if (command_id == ColorControl::Commands::MoveToHueAndSaturation::Id) {
    ColorControl::Commands::MoveToHueAndSaturation::DecodableType data;
    if (Decode(reader, data) != CHIP_NO_ERROR)
      return false;
    if (data.hue > MATTER_MAX_LEVEL || data.saturation > MATTER_MAX_LEVEL)
      return false;
    color.kind = Kind::HS;
    color.first = data.hue;
    color.second = data.saturation;
    transition_ds = data.transitionTime;
    options_mask = data.optionsMask.Raw();
    options_override = data.optionsOverride.Raw();
  } else if (command_id == ColorControl::Commands::EnhancedMoveToHue::Id) {
    ColorControl::Commands::EnhancedMoveToHue::DecodableType data;
    if (Decode(reader, data) != CHIP_NO_ERROR)
      return false;
    uint8_t saturation;
    if (!read_u8_attribute(eid, ColorControl::Id,
                           ColorControl::Attributes::CurrentSaturation::Id,
                           saturation))
      return false;
    color.kind = Kind::ENHANCED_HS;
    color.first = data.enhancedHue;
    color.second = saturation;
    transition_ds = data.transitionTime;
    options_mask = data.optionsMask.Raw();
    options_override = data.optionsOverride.Raw();
  } else if (command_id ==
             ColorControl::Commands::EnhancedMoveToHueAndSaturation::Id) {
    ColorControl::Commands::EnhancedMoveToHueAndSaturation::DecodableType data;
    if (Decode(reader, data) != CHIP_NO_ERROR)
      return false;
    if (data.saturation > MATTER_MAX_LEVEL)
      return false;
    color.kind = Kind::ENHANCED_HS;
    color.first = data.enhancedHue;
    color.second = data.saturation;
    transition_ds = data.transitionTime;
    options_mask = data.optionsMask.Raw();
    options_override = data.optionsOverride.Raw();
  } else {
    return false;
  }

  // CHIP ignores colour commands while the light is off, unless told not to.
  if (!this->execute_if_off_(ColorControl::Id,
                             ColorControl::Attributes::Options::Id,
                             options_mask, options_override))
    return false;
  command.color = color;
  command.transition_ms = command_transition_ms(transition_ds);
  command.color_group = true;
  // Any other color command ends a running color loop.
  if (this->color_loop_active_)
    command.color_loop = MatterLightCommand::ColorLoop::STOP;
  return true;
}

// Runs on the CHIP task, before CHIP executes the command.
void MatterLightMapping::handle_command(uint32_t cluster_id,
                                        uint32_t command_id,
                                        chip::TLV::TLVReader &reader) {
  using namespace chip::app::Clusters;
  MatterLightCommand command;
  bool handled = false;
  if (cluster_id == OnOff::Id)
    handled = this->decode_on_off_command_(command_id, command);
  else if (cluster_id == LevelControl::Id)
    handled = this->decode_level_command_(command_id, reader, command);
  else if (cluster_id == ColorControl::Id)
    handled = this->decode_color_command_(command_id, reader, command);
  if (!handled)
    return;

  // CHIP stops where it is. Synchronize with that once it has executed the
  // command (the read runs after the current work item on the CHIP task).
  if (command.stop_light)
    this->schedule_full_read_(true, true, false);
  else if (command.stop_color)
    this->schedule_full_read_(true, false, true);
  else if (command.color_loop == MatterLightCommand::ColorLoop::STOP &&
           !command.color.has_value())
    // CHIP restores the hue from before the loop; follow that.
    this->schedule_full_read_(true, false, true);
  global_matter_component->defer_to_main_loop(
      [this, command]() { this->apply_command_(command); });
}

// Moves the light to the target of a command with the transition time of the
// command, as one light call.
void MatterLightMapping::apply_command_(const MatterLightCommand &command) {
  if (command.stop_light || command.stop_color) {
    // The values to settle on follow from the CHIP task.
    if (command.stop_light)
      this->light_window_active_ = false;
    if (command.stop_color)
      this->color_window_active_ = false;
    return;
  }
  if (command.color_loop == MatterLightCommand::ColorLoop::START) {
    // ColorLoopSet: run the effect. CHIP's own steps are ignored until a
    // later command ends the loop.
    this->pending_.color.reset();
    this->color_loop_active_ = true;
    this->last_apply_valid_ = false;
    auto call = this->light_->make_call();
    call.set_effect(std::string(this->color_loop_effect_));
    this->synchronizing_from_matter_ = true;
    call.perform();
    this->synchronizing_from_matter_ = false;
    return;
  }

  const auto &values = this->light_->remote_values;
  auto call = this->light_->make_call();
  bool changed = false;
  bool turns_on = command.on.has_value() && *command.on && !values.is_on();

  if (command.on.has_value() && values.is_on() != *command.on) {
    call.set_state(*command.on);
    changed = true;
  }
  // A light that is turned off keeps its brightness, which fades out.
  bool brightness_applies =
      command.level.has_value() && (!command.on.has_value() || *command.on) &&
      (!command.level_if_turning_on || turns_on);
  if (brightness_applies) {
    float brightness = conversion::from_matter::brightness(*command.level);
    if (std::fabs(values.get_brightness() - brightness) >= (0.5f / 254.0f)) {
      call.set_brightness(brightness);
      changed = true;
    }
  }
  if (command.color.has_value() &&
      this->add_color_to_call_(call, *command.color, false))
    changed = true;

  // A color command ends the color loop: the effect stops and the light goes to
  // the color of the command (or, for ColorLoopSet deactivate, the color that
  // CHIP restores, which follows from the read scheduled for this command).
  if (command.color_loop == MatterLightCommand::ColorLoop::STOP) {
    if (this->color_loop_active_) {
      call.set_effect(std::string("None"));
      changed = true;
    }
    this->color_loop_active_ = false;
  }

  // Ignore CHIP's steps towards the target of this command, but not for the
  // other attribute group which isn't part of it. A change that was queued
  // before the command is out of date.
  uint32_t now = millis();
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
    this->color_window_until_ms_ = now + window_ms;
    this->pending_.color.reset();
  }
  // Later steps of a rate based command are not related to this one.
  this->last_apply_valid_ = false;

  if (changed) {
    // Lights without brightness control (on/off) can't transition at all.
    call.set_transition_length_if_supported(command.transition_ms);
    this->synchronizing_from_matter_ = true;
    call.perform();
    this->synchronizing_from_matter_ = false;
    this->remember_applied_values_();
  }

  // When the window ends, synchronize with what CHIP ended up with.
  global_matter_component->run_on_main_loop_after(
      window_ms, [this]() { this->finish_command_windows_(); });
}

void MatterLightMapping::finish_command_windows_() {
  uint32_t now = millis();
  // A newer command extended a window; its own timer finishes it.
  if (this->light_group_suppressed_(now) || this->color_group_suppressed_(now))
    return;
  bool light = this->light_window_active_;
  bool color = this->color_window_active_;
  if (!light && !color)
    return;
  this->light_window_active_ = false;
  this->color_window_active_ = false;
  this->schedule_full_read_(true, light, color);
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_LIGHT
