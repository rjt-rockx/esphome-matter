#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_LIGHT)

#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "matter_component.h"
#include "matter_conversions.h"
#include "matter_lights.h"

#include <algorithm>
#include <app-common/zap-generated/cluster-objects.h>
#include <app/clusters/color-control-server/color-control-server.h>
#include <app/clusters/level-control/level-control.h>
#include <app/ConcreteCommandPath.h>
#include <app/data-model/Decode.h>
#include <cinttypes>
#include <cmath>
#include <cstring>
#include <strings.h>
#include <esp_matter_cluster.h>
#include <esp_matter_command.h>
#include <lib/core/TLVReader.h>
#include <mutex>
#include <platform/CHIPDeviceLayer.h>
#include <vector>

static const char *const TAG = "matter";

namespace esphome::matter {

namespace {

// Transition times in commands are in 1/10 s.
constexpr uint32_t DECISECOND_MS = 100;

// The ids of matter_light_binding.h are the ones of the generated clusters.
static_assert(light_ids::ON_OFF_CLUSTER == chip::app::Clusters::OnOff::Id);
static_assert(light_ids::ON_OFF ==
              chip::app::Clusters::OnOff::Attributes::OnOff::Id);
static_assert(light_ids::LEVEL_CLUSTER == chip::app::Clusters::LevelControl::Id);
static_assert(light_ids::CURRENT_LEVEL ==
              chip::app::Clusters::LevelControl::Attributes::CurrentLevel::Id);
static_assert(light_ids::LEVEL_REMAINING_TIME ==
              chip::app::Clusters::LevelControl::Attributes::RemainingTime::Id);
static_assert(light_ids::MIN_LEVEL ==
              chip::app::Clusters::LevelControl::Attributes::MinLevel::Id);
static_assert(light_ids::MAX_LEVEL ==
              chip::app::Clusters::LevelControl::Attributes::MaxLevel::Id);
static_assert(light_ids::COLOR_CLUSTER == chip::app::Clusters::ColorControl::Id);
static_assert(light_ids::CURRENT_HUE ==
              chip::app::Clusters::ColorControl::Attributes::CurrentHue::Id);
static_assert(
    light_ids::CURRENT_SATURATION ==
    chip::app::Clusters::ColorControl::Attributes::CurrentSaturation::Id);
static_assert(light_ids::COLOR_REMAINING_TIME ==
              chip::app::Clusters::ColorControl::Attributes::RemainingTime::Id);
static_assert(light_ids::CURRENT_X ==
              chip::app::Clusters::ColorControl::Attributes::CurrentX::Id);
static_assert(light_ids::CURRENT_Y ==
              chip::app::Clusters::ColorControl::Attributes::CurrentY::Id);
static_assert(
    light_ids::COLOR_TEMPERATURE_MIREDS ==
    chip::app::Clusters::ColorControl::Attributes::ColorTemperatureMireds::Id);
static_assert(light_ids::COLOR_MODE ==
              chip::app::Clusters::ColorControl::Attributes::ColorMode::Id);
static_assert(
    light_ids::ENHANCED_CURRENT_HUE ==
    chip::app::Clusters::ColorControl::Attributes::EnhancedCurrentHue::Id);
static_assert(
    light_ids::ENHANCED_COLOR_MODE ==
    chip::app::Clusters::ColorControl::Attributes::EnhancedColorMode::Id);

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
LightActiveColor get_active_color(const light::LightColorValues &values) {
  auto mode = values.get_color_mode();
  bool rgb = static_cast<bool>(mode & light::ColorCapability::RGB);
  bool ct = static_cast<bool>(mode & light::ColorCapability::COLOR_TEMPERATURE) ||
            static_cast<bool>(mode & light::ColorCapability::COLD_WARM_WHITE);
  if (rgb && ct)
    return values.get_color_brightness() > 0.0f ? LightActiveColor::COLOR
                                                : LightActiveColor::CT;
  if (rgb)
    return LightActiveColor::COLOR;
  if (ct)
    return LightActiveColor::CT;
  return LightActiveColor::NONE;
}

// Attribute reads for the CHIP task. Return false when the attribute doesn't
// exist or is null.
bool read_attribute(uint16_t endpoint_id, uint32_t cluster_id,
                    uint32_t attribute_id, esp_matter_attr_val_t &value) {
  // get_val logs "Attribute cannot be NULL" for an optional attribute the
  // endpoint doesn't have: check quietly first.
  if (esp_matter::attribute::get(endpoint_id, cluster_id, attribute_id) ==
      nullptr)
    return false;
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
    : MatterLightBinding(endpoint_id), light_(light),
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

// Main loop, from setup_(): what the endpoint has.
bool MatterLightMapping::probe_capabilities_() {
  if (this->light_ == nullptr)
    return false;

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
  return true;
}

void MatterLightMapping::listen_entity_() {
  this->light_->add_remote_values_listener(this);
}

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
    hook(LevelControl::Id, LevelControl::Commands::Move::Id);
    hook(LevelControl::Id, LevelControl::Commands::Step::Id);
    hook(LevelControl::Id, LevelControl::Commands::MoveWithOnOff::Id);
    hook(LevelControl::Id, LevelControl::Commands::StepWithOnOff::Id);
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
    // The rate based commands are not followed with a transition (CHIP
    // writes every step), but they end a color loop.
    hook(ColorControl::Id, ColorControl::Commands::MoveColor::Id);
    hook(ColorControl::Id, ColorControl::Commands::StepColor::Id);
    hook(ColorControl::Id, ColorControl::Commands::MoveHue::Id);
    hook(ColorControl::Id, ColorControl::Commands::StepHue::Id);
    hook(ColorControl::Id, ColorControl::Commands::MoveSaturation::Id);
    hook(ColorControl::Id, ColorControl::Commands::StepSaturation::Id);
    hook(ColorControl::Id, ColorControl::Commands::EnhancedMoveHue::Id);
    hook(ColorControl::Id, ColorControl::Commands::EnhancedStepHue::Id);
    hook(ColorControl::Id, ColorControl::Commands::MoveColorTemperature::Id);
    hook(ColorControl::Id, ColorControl::Commands::StepColorTemperature::Id);
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

  // Move and Step run at a rate: CHIP writes every step, the light follows.
  if (command_id == LevelControl::Commands::Move::Id ||
      command_id == LevelControl::Commands::MoveWithOnOff::Id ||
      command_id == LevelControl::Commands::Step::Id ||
      command_id == LevelControl::Commands::StepWithOnOff::Id) {
    command.rate_light = true;
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
  // On while on and Off while off change nothing (no window to open).
  if (command_id != OnOff::Commands::Toggle::Id && on == target)
    return false;
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

  // Move and Step run at a rate: CHIP writes every step, the light follows.
  if (command_id == ColorControl::Commands::MoveHue::Id ||
      command_id == ColorControl::Commands::StepHue::Id ||
      command_id == ColorControl::Commands::MoveSaturation::Id ||
      command_id == ColorControl::Commands::StepSaturation::Id ||
      command_id == ColorControl::Commands::MoveColor::Id ||
      command_id == ColorControl::Commands::StepColor::Id ||
      command_id == ColorControl::Commands::EnhancedMoveHue::Id ||
      command_id == ColorControl::Commands::EnhancedStepHue::Id ||
      command_id == ColorControl::Commands::MoveColorTemperature::Id ||
      command_id == ColorControl::Commands::StepColorTemperature::Id) {
    command.rate_color = true;
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
    bool activate =
        data.action != ColorControl::ColorLoopActionEnum::kDeactivate;
    // Tracked here, on the CHIP task, in the order of the commands.
    this->color_loop_requested_ = activate;
    command.color_loop = activate ? MatterLightCommand::ColorLoop::START
                                  : MatterLightCommand::ColorLoop::STOP;
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
  return true;
}


// Runs on the CHIP task, before CHIP executes the command.
void MatterLightMapping::handle_command(uint32_t cluster_id,
                                        uint32_t command_id,
                                        chip::TLV::TLVReader &reader) {
  using namespace chip::app::Clusters;
  MatterLightCommand command;
  bool handled = false;
  // Every color command except ColorLoopSet ends a color loop. CHIP's own loop
  // is deactivated first (it doesn't execute colour commands while it runs).
  bool loop_stop = false;
  if (cluster_id == ColorControl::Id &&
      command_id != ColorControl::Commands::ColorLoopSet::Id &&
      this->color_loop_requested_.exchange(false)) {
    this->deactivate_chip_color_loop_();
    loop_stop = true;
  }
  if (cluster_id == OnOff::Id)
    handled = this->decode_on_off_command_(command_id, command);
  else if (cluster_id == LevelControl::Id)
    handled = this->decode_level_command_(command_id, reader, command);
  else if (cluster_id == ColorControl::Id)
    handled = this->decode_color_command_(command_id, reader, command);
  if (!handled && !loop_stop)
    return;
  if (!handled)
    command = MatterLightCommand{};
  if (loop_stop)
    command.color_loop = MatterLightCommand::ColorLoop::STOP;

  // The main loop applies the command. For the commands that stop or end
  // something, it also reads the state CHIP ends up with (after CHIP executed
  // the command) and settles the light on it.
  this->submit_command(command);
}

// ---- Entity hooks (main loop) -----------------------------------------------

LightEntityView MatterLightMapping::entity_view_() {
  const auto &values = this->light_->remote_values;
  LightEntityView view;
  view.on = values.is_on();
  view.brightness = values.get_brightness();
  view.active = get_active_color(values);
  view.red = values.get_red();
  view.green = values.get_green();
  view.blue = values.get_blue();
  view.color_temperature = values.get_color_temperature();
  return view;
}

void MatterLightMapping::perform_(const LightRequest &request) {
  auto call = this->light_->make_call();
  if (request.state.has_value())
    call.set_state(*request.state);
  if (request.brightness.has_value())
    call.set_brightness(*request.brightness);
  if (request.color == LightRequest::Color::CT) {
    call.set_color_temperature(request.color_temperature);
    // Lights that can show RGB and white at the same time (e.g. RGBWW without
    // color_interlock) are put in white mode by dropping the RGB part.
    call.set_color_brightness_if_supported(0.0f);
  } else if (request.color == LightRequest::Color::RGB) {
    call.set_rgb(request.red, request.green, request.blue);
    // The counterpart of the color temperature case: don't leave the white
    // channels of an RGBWW light on, which would wash out the color.
    call.set_white_if_supported(0.0f);
    call.set_cold_white_if_supported(0.0f);
    call.set_warm_white_if_supported(0.0f);
  }
  // Lights without brightness control (on/off) can't transition at all.
  call.set_transition_length_if_supported(request.transition_ms);
  call.perform();
}

void MatterLightMapping::remember_applied_entity_() {
  this->applied_values_ = this->light_->remote_values;
}

bool MatterLightMapping::remote_matches_applied_() {
  return this->light_->remote_values == this->applied_values_;
}

// ---- Color loop -------------------------------------------------------------

bool MatterLightMapping::color_loop_effect_running_() {
  if (this->color_loop_effect_ == nullptr)
    return false;
  auto name = this->light_->get_effect_name();
  size_t length = strlen(this->color_loop_effect_);
  return name.size() == length && length != 0 &&
         strncasecmp(name.c_str(), this->color_loop_effect_, length) == 0;
}

void MatterLightMapping::start_color_loop_effect_() {
  auto call = this->light_->make_call();
  call.set_effect(std::string(this->color_loop_effect_));
  call.perform();
}

void MatterLightMapping::stop_color_loop_effect_hook_() {
  auto call = this->light_->make_call();
  call.set_effect(std::string("None"));
  call.perform();
}

// ---- CHIP hooks -----------------------------------------------------------------

void MatterLightMapping::chip_stop_light_transition_(uint8_t level) {
  using namespace chip::app::Clusters;
  // MoveToLevel without a transition replaces the running one.
  LevelControl::Commands::MoveToLevel::DecodableType data;
  data.level = level;
  data.transitionTime.SetNonNull(0);
  data.optionsMask.Set(LevelControl::OptionsBitmap::kExecuteIfOff);
  data.optionsOverride.Set(LevelControl::OptionsBitmap::kExecuteIfOff);
  LevelControlServer::MoveToLevel(this->endpoint_id(), data);
}

void MatterLightMapping::chip_stop_color_transitions_() {
  using namespace chip::app::Clusters;
  ColorControlServer::Instance().stopAllColorTransitions(this->endpoint_id());
}

void MatterLightMapping::deactivate_chip_color_loop_() {
  using namespace chip::app::Clusters;
  uint8_t active = 0;
  if (!read_u8_attribute(this->endpoint_id(), ColorControl::Id,
                         ColorControl::Attributes::ColorLoopActive::Id,
                         active) ||
      active == 0)
    return;
  ColorControl::Commands::ColorLoopSet::DecodableType data;
  data.updateFlags.Set(ColorControl::UpdateFlagsBitmap::kUpdateAction);
  data.action = ColorControl::ColorLoopActionEnum::kDeactivate;
  data.optionsMask.Set(ColorControl::OptionsBitmap::kExecuteIfOff);
  data.optionsOverride.Set(ColorControl::OptionsBitmap::kExecuteIfOff);
  ColorControlServer::Instance().colorLoopCommand(this->endpoint_id(), data);
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_LIGHT
