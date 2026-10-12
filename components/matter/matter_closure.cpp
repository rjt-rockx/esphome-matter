#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_COVER) && defined(USE_MATTER_CLOSURE)

#include "esphome/core/log.h"
#include "matter_closure.h"
#include "matter_component.h"

#include <app/clusters/closure-control-server/ClosureControlCluster.h>
#include <cmath>
#include <data_model_provider/esp_matter_data_model_provider.h>
#include <platform/CHIPDeviceLayer.h>
#include <vector>

static const char *const TAG = "matter.closure";

namespace esphome::matter {

namespace {

namespace cc = chip::app::Clusters::ClosureControl;
using Status = chip::Protocols::InteractionModel::Status;

// Positions at or beyond these are reported as fully open / closed.
constexpr float kEndTolerance = 0.01f;

std::vector<MatterClosureMapping *> &closure_mappings() {
  static std::vector<MatterClosureMapping *> mappings;
  return mappings;
}

} // namespace

void MatterComponent::register_closure(cover::Cover *cover, uint16_t endpoint_id) {
  auto *mapping = new MatterClosureMapping(cover, endpoint_id);
  closure_mappings().push_back(mapping);
  this->mappings_.push_back(mapping);
}

void *get_closure_delegate(uint16_t endpoint_id) {
  for (auto *mapping : closure_mappings()) {
    if (mapping->endpoint_id() == endpoint_id)
      return static_cast<cc::ClosureControlClusterDelegate *>(mapping);
  }
  ESP_LOGE(TAG, "No cover registered for closure endpoint %u", endpoint_id);
  return nullptr;
}

MatterClosureMapping::MatterClosureMapping(cover::Cover *cover, uint16_t endpoint_id)
    : MatterEndpointMappingBase(endpoint_id), cover_(cover) {}

void MatterClosureMapping::initialize() {
  if (this->cover_ == nullptr)
    return;
  this->cover_->add_on_state_callback([this]() { this->push_state_to_matter_(); });
  global_matter_component->defer_to_main_loop([this]() { this->push_state_to_matter_(); });
}

// ---------------------------------------------------------------------------
// Matter -> ESPHome (CHIP task)
// ---------------------------------------------------------------------------

Status MatterClosureMapping::HandleMoveToCommand(const chip::Optional<cc::TargetPositionEnum> &position,
                                                 const chip::Optional<bool> &latch,
                                                 const chip::Optional<chip::app::Clusters::Globals::ThreeLevelAutoEnum> &speed) {
  // Latching and Speed are not advertised, so only the position is of interest.
  if (!position.HasValue())
    return Status::InvalidCommand;
  switch (position.Value()) {
    case cc::TargetPositionEnum::kMoveToFullyOpen:
      global_matter_component->defer_to_main_loop([this]() { this->move_(true); });
      return Status::Success;
    case cc::TargetPositionEnum::kMoveToFullyClosed:
      global_matter_component->defer_to_main_loop([this]() { this->move_(false); });
      return Status::Success;
    default:
      // Pedestrian, ventilation and signature positions need features that are not enabled.
      return Status::ConstraintError;
  }
}

Status MatterClosureMapping::HandleStopCommand() {
  if (!this->cover_->get_traits().get_supports_stop()) {
    ESP_LOGW(TAG, "Stop requested on endpoint %u but the cover has no stop action", this->endpoint_id());
    return Status::Failure;
  }
  global_matter_component->defer_to_main_loop([this]() { this->stop_(); });
  return Status::Success;
}

Status MatterClosureMapping::HandleCalibrateCommand() { return Status::UnsupportedCommand; }

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------

void MatterClosureMapping::move_(bool open) {
  ESP_LOGD(TAG, "Matter MoveTo %s on endpoint %u", open ? "open" : "close", this->endpoint_id());
  auto call = this->cover_->make_call();
  if (open)
    call.set_command_open();
  else
    call.set_command_close();
  call.perform();
  // The cluster sets MainState to Moving after the delegate returned. When the
  // cover is already at the requested end it publishes nothing, so settle the
  // state explicitly.
  this->push_state_to_matter_();
}

void MatterClosureMapping::stop_() {
  this->cover_->make_call().set_command_stop().perform();
  this->push_state_to_matter_();
}

// ---------------------------------------------------------------------------
// ESPHome -> Matter
// ---------------------------------------------------------------------------

void MatterClosureMapping::push_state_to_matter_() {
  float position = this->cover_->position;
  bool known = std::isfinite(position);
  auto operation = this->cover_->current_operation;
  bool moving = operation != cover::COVER_OPERATION_IDLE;
  bool opening = operation == cover::COVER_OPERATION_OPENING;

  cc::CurrentPositionEnum current = cc::CurrentPositionEnum::kPartiallyOpened;
  if (known && position >= 1.0f - kEndTolerance)
    current = cc::CurrentPositionEnum::kFullyOpened;
  else if (known && position <= kEndTolerance)
    current = cc::CurrentPositionEnum::kFullyClosed;

  uint16_t eid = this->endpoint_id();
  chip::DeviceLayer::SystemLayer().ScheduleLambda([eid, known, current, moving, opening]() {
    auto *server = esp_matter::data_model::provider::get_instance().registry().Get({eid, chip::app::Clusters::ClosureControl::Id});
    if (server == nullptr)
      return;
    auto *cluster = static_cast<cc::ClosureControlCluster *>(server);

    // OverallCurrentState must be non-null for the cluster to accept MoveTo; an
    // unknown position is a null Position inside the struct.
    chip::app::DataModel::Nullable<cc::CurrentPositionEnum> position_value = chip::app::DataModel::NullNullable;
    if (known)
      position_value = chip::app::DataModel::MakeNullable(current);
    cc::GenericOverallCurrentState current_state;
    current_state.position = chip::MakeOptional(position_value);
    (void) cluster->SetOverallCurrentState(chip::app::DataModel::MakeNullable(current_state));

    chip::app::DataModel::Nullable<cc::GenericOverallTargetState> target_state = chip::app::DataModel::NullNullable;
    if (moving) {
      cc::GenericOverallTargetState target;
      target.position = chip::MakeOptional(chip::app::DataModel::MakeNullable(
          opening ? cc::TargetPositionEnum::kMoveToFullyOpen : cc::TargetPositionEnum::kMoveToFullyClosed));
      target_state = chip::app::DataModel::MakeNullable(target);
    }
    (void) cluster->SetOverallTargetState(target_state);

    bool was_moving = cluster->GetMainState() == cc::MainStateEnum::kMoving;
    (void) cluster->SetMainState(moving ? cc::MainStateEnum::kMoving : cc::MainStateEnum::kStopped);
    if (was_moving && !moving)
      (void) cluster->GenerateMovementCompletedEvent();
  });
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_COVER && USE_MATTER_CLOSURE
