#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_VALVE) && defined(USE_MATTER_VALVE)

#include "esphome/core/log.h"
#include "matter_component.h"
#include "matter_valve.h"

#include <app/clusters/valve-configuration-and-control-server/ValveConfigurationAndControlCluster.h>
#include <clusters/ValveConfigurationAndControl/Attributes.h>
#include <clusters/ValveConfigurationAndControl/ClusterId.h>
#include <data_model_provider/esp_matter_data_model_provider.h>
#include <esp_matter_cluster.h>

#include <algorithm>
#include <cmath>
#include <vector>

static const char *const TAG = "matter.valve";

namespace esphome::matter {

namespace {

// Tolerance when comparing the position of the valve to a target position.
constexpr float POSITION_TOLERANCE = 0.005f;

// Mappings are registered during setup() and never removed, so the CHIP task
// can safely read this afterwards.
std::vector<MatterValveMapping *> &valve_mappings() {
  static std::vector<MatterValveMapping *> mappings;
  return mappings;
}

} // namespace

void MatterComponent::register_valve(valve::Valve *valve,
                                     uint16_t endpoint_id) {
  auto *mapping = new MatterValveMapping(valve, endpoint_id);
  valve_mappings().push_back(mapping);
  this->mappings_.push_back(mapping);
}

void *get_valve_delegate(uint16_t endpoint_id) {
  for (auto *mapping : valve_mappings()) {
    if (mapping->endpoint_id() == endpoint_id)
      return static_cast<chip::app::Clusters::ValveConfigurationAndControl::
                             Delegate *>(mapping->delegate());
  }
  return nullptr;
}

chip::app::DataModel::Nullable<chip::Percent>
MatterValveDelegate::HandleOpenValve(
    chip::app::DataModel::Nullable<chip::Percent> level) {
  return this->mapping_->handle_open(level);
}

CHIP_ERROR MatterValveDelegate::HandleCloseValve() {
  this->mapping_->handle_close();
  return CHIP_NO_ERROR;
}

MatterValveMapping::MatterValveMapping(valve::Valve *valve,
                                       uint16_t endpoint_id)
    : MatterBinding<MatterValveState, MatterValveCommand>(
          endpoint_id,
          BindingPolicy{BootSync::ESPHOME_WINS, SETTLE_MS, true}),
      valve_(valve), delegate_(this) {}

void MatterValveMapping::setup_() {
  if (this->valve_ == nullptr)
    return;

  using namespace chip::app::Clusters;
  this->has_level_ =
      esp_matter::attribute::get(
          this->endpoint_id(), ValveConfigurationAndControl::Id,
          ValveConfigurationAndControl::Attributes::CurrentLevel::Id) != nullptr;

  this->valve_->add_on_state_callback([this]() { this->request_push(); });
}

// Called on the CHIP task.
chip::app::DataModel::Nullable<chip::Percent>
MatterValveMapping::handle_open(
    chip::app::DataModel::Nullable<chip::Percent> level) {
  MatterValveCommand cmd;
  cmd.open = true;
  cmd.has_level = !level.IsNull();
  cmd.level = cmd.has_level ? level.Value() : 100;
  this->submit_inbound(cmd);

  // Must return the current level when the Level feature is supported.
  if (!this->has_level_)
    return chip::app::DataModel::NullNullable;
  return chip::app::DataModel::MakeNullable(
      static_cast<chip::Percent>(this->current_level_.load()));
}

// Called on the CHIP task.
void MatterValveMapping::handle_close() {
  MatterValveCommand cmd;
  cmd.open = false;
  this->submit_inbound(cmd);
}

void MatterValveMapping::apply_inbound_(const MatterValveCommand &cmd) {
  if (this->valve_ == nullptr)
    return;
  if (!cmd.open) {
    ESP_LOGD(TAG, "Matter close command on endpoint %u", this->endpoint_id());
    // An idle valve at the target publishes nothing: skip the call, the
    // framework's settle window then pushes the current state.
    if (this->valve_->current_operation == valve::VALVE_OPERATION_IDLE &&
        std::fabs(this->valve_->position - valve::VALVE_CLOSED) <
            POSITION_TOLERANCE)
      return;
    auto call = this->valve_->make_call();
    call.set_command_close();
    call.perform();
    return;
  }

  auto traits = this->valve_->get_traits();
  bool use_position =
      cmd.has_level && traits.get_supports_position() && cmd.level < 100;
  float target = use_position ? static_cast<float>(cmd.level) / 100.0f
                              : valve::VALVE_OPEN;
  ESP_LOGD(TAG, "Matter open command on endpoint %u: target=%.2f",
           this->endpoint_id(), target);
  if (this->valve_->current_operation == valve::VALVE_OPERATION_IDLE &&
      std::fabs(this->valve_->position - target) < POSITION_TOLERANCE)
    return;
  auto call = this->valve_->make_call();
  if (use_position)
    call.set_position(target);
  else
    call.set_command_open();
  call.perform();
}

// Captures the state of the valve.
//
// ESPHome operation OPENING/CLOSING    -> CurrentState Transitioning
// ESPHome idle, position 0.0           -> CurrentState Closed, CurrentLevel 0
// ESPHome idle, position > 0.0         -> CurrentState Open, CurrentLevel
//                                         round(position * 100)
// CurrentLevel is only updated when the Level feature is enabled.
MatterValveState MatterValveMapping::capture_() {
  MatterValveState s;
  if (this->valve_ == nullptr)
    return s;
  float position = this->valve_->position;
  if (!std::isfinite(position))
    return s;
  position = std::clamp(position, 0.0f, 1.0f);

  using chip::app::Clusters::ValveConfigurationAndControl::ValveStateEnum;
  s.valid = true;
  s.level = static_cast<uint8_t>(std::lround(position * 100.0f));
  s.state = ValveStateEnum::kTransitioning;
  if (this->valve_->current_operation == valve::VALVE_OPERATION_IDLE)
    s.state = position > 0.0f ? ValveStateEnum::kOpen : ValveStateEnum::kClosed;
  this->current_level_.store(s.level);
  return s;
}

void MatterValveMapping::write_matter_(const MatterValveState &s) {
  if (!s.valid)
    return;
  using namespace chip::app::Clusters;
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {this->endpoint_id(), ValveConfigurationAndControl::Id});
  if (server == nullptr)
    return;
  auto *cluster = static_cast<ValveConfigurationAndControlCluster *>(server);
  // The current level is updated first because updating it to the target
  // level also settles CurrentState. The state is then set explicitly.
  if (this->has_level_)
    cluster->UpdateCurrentLevel(s.level);
  cluster->UpdateCurrentState(s.state);
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_VALVE && USE_MATTER_VALVE
