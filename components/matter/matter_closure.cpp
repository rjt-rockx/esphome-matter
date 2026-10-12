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
    : MatterClosureBinding(endpoint_id), cover_(cover) {
  this->has_stop_ = cover->get_traits().get_supports_stop();
}

void MatterClosureMapping::setup_() {
  MatterClosureBinding::setup_();
  if (this->cover_ == nullptr)
    return;
  this->has_stop_ = this->cover_->get_traits().get_supports_stop();
  this->cover_->add_on_state_callback([this]() { this->on_entity_state_(); });
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
      this->submit_command(ClosureCommand::OPEN);
      return Status::Success;
    case cc::TargetPositionEnum::kMoveToFullyClosed:
      this->submit_command(ClosureCommand::CLOSE);
      return Status::Success;
    default:
      // Pedestrian, ventilation and signature positions need features that are not enabled.
      return Status::ConstraintError;
  }
}

Status MatterClosureMapping::HandleStopCommand() {
  // The cluster sets MainState to Stopped after Success. Without a stop action
  // the cover keeps moving, so the reconcile after the stop pushes the real state.
  this->submit_command(ClosureCommand::STOP);
  return Status::Success;
}

Status MatterClosureMapping::HandleCalibrateCommand() { return Status::UnsupportedCommand; }

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------

ClosureEntityView MatterClosureMapping::entity_() {
  ClosureEntityView v;
  if (this->cover_ == nullptr)
    return v;
  v.position = this->cover_->position;
  switch (this->cover_->current_operation) {
  case cover::COVER_OPERATION_OPENING:
    v.operation = ClosureEntityOperation::OPENING;
    break;
  case cover::COVER_OPERATION_CLOSING:
    v.operation = ClosureEntityOperation::CLOSING;
    break;
  default:
    break;
  }
  return v;
}

void MatterClosureMapping::command_open_() { this->cover_->make_call().set_command_open().perform(); }

void MatterClosureMapping::command_close_() { this->cover_->make_call().set_command_close().perform(); }

void MatterClosureMapping::command_stop_() { this->cover_->make_call().set_command_stop().perform(); }

// ---------------------------------------------------------------------------
// ESPHome -> Matter (CHIP task)
// ---------------------------------------------------------------------------

bool MatterClosureMapping::write_cluster_(const MatterClosurePush &s) {
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get({this->endpoint_id(), chip::app::Clusters::ClosureControl::Id});
  if (server == nullptr)
    return false;
  auto *cluster = static_cast<cc::ClosureControlCluster *>(server);

  cc::CurrentPositionEnum current = cc::CurrentPositionEnum::kPartiallyOpened;
  if (s.current == ClosurePosition::FULLY_OPENED)
    current = cc::CurrentPositionEnum::kFullyOpened;
  else if (s.current == ClosurePosition::FULLY_CLOSED)
    current = cc::CurrentPositionEnum::kFullyClosed;

  // OverallCurrentState must be non-null for the cluster to accept MoveTo; an
  // unknown position is a null Position inside the struct.
  chip::app::DataModel::Nullable<cc::CurrentPositionEnum> position_value = chip::app::DataModel::NullNullable;
  if (s.known)
    position_value = chip::app::DataModel::MakeNullable(current);
  cc::GenericOverallCurrentState current_state;
  current_state.position = chip::MakeOptional(position_value);
  (void) cluster->SetOverallCurrentState(chip::app::DataModel::MakeNullable(current_state));

  chip::app::DataModel::Nullable<cc::GenericOverallTargetState> target_state = chip::app::DataModel::NullNullable;
  if (s.moving && s.target >= 0) {
    cc::GenericOverallTargetState target_value;
    target_value.position = chip::MakeOptional(chip::app::DataModel::MakeNullable(
        s.target == 1 ? cc::TargetPositionEnum::kMoveToFullyOpen : cc::TargetPositionEnum::kMoveToFullyClosed));
    target_state = chip::app::DataModel::MakeNullable(target_value);
  }
  (void) cluster->SetOverallTargetState(target_state);

  (void) cluster->SetMainState(s.moving ? cc::MainStateEnum::kMoving : cc::MainStateEnum::kStopped);
  if (this->take_completion(s))
    (void) cluster->GenerateMovementCompletedEvent();
  return true;
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_COVER && USE_MATTER_CLOSURE
