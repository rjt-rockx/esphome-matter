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

// An idle cover within this distance of an end is reported as fully open / closed.
constexpr float kEndTolerance = 0.01f;
constexpr uint8_t kMaxPushRetries = 20;
constexpr uint32_t kPushRetryMs = 500;

bool at_end(float position, bool open) {
  if (!std::isfinite(position))
    return false;
  return open ? position >= 1.0f - kEndTolerance : position <= kEndTolerance;
}

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
    : MatterEndpointMappingBase(endpoint_id), cover_(cover) {
  this->has_stop_ = cover->get_traits().get_supports_stop();
}

void MatterClosureMapping::initialize() {
  if (this->cover_ == nullptr)
    return;
  this->has_stop_ = this->cover_->get_traits().get_supports_stop();
  this->cover_->add_on_state_callback([this]() { this->request_push_(); });
  global_matter_component->defer_to_main_loop([this]() { this->request_push_(); });
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
  // The cluster sets MainState to Stopped after Success. Without a stop action
  // the cover keeps moving, so stop_() pushes the real state right after.
  global_matter_component->defer_to_main_loop([this]() { this->stop_(); });
  return Status::Success;
}

Status MatterClosureMapping::HandleCalibrateCommand() { return Status::UnsupportedCommand; }

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------

void MatterClosureMapping::move_(bool open) {
  ESP_LOGD(TAG, "Matter MoveTo %s on endpoint %u", open ? "open" : "close", this->endpoint_id());
  bool idle = this->cover_->current_operation == cover::COVER_OPERATION_IDLE;
  if (idle && at_end(this->cover_->position, open)) {
    // Nothing to do. No movement, so no MovementCompleted; the cluster already
    // set MainState to Moving, which the push settles.
    this->target_ = -1;
  } else {
    this->target_ = open ? 1 : 0;
    auto call = this->cover_->make_call();
    if (open)
      call.set_command_open();
    else
      call.set_command_close();
    call.perform();
  }
  this->request_push_();
}

void MatterClosureMapping::stop_() {
  // An interrupted movement does not complete.
  this->target_ = -1;
  if (this->has_stop_)
    this->cover_->make_call().set_command_stop().perform();
  else
    ESP_LOGW(TAG, "Stop on endpoint %u ignored: the cover has no stop action", this->endpoint_id());
  this->request_push_();
}

// ---------------------------------------------------------------------------
// ESPHome -> Matter
// ---------------------------------------------------------------------------

void MatterClosureMapping::request_push_() {
  if (this->push_in_flight_) {
    this->push_dirty_ = true;
    return;
  }
  this->push_now_();
}

// Reads the cover state now (main loop) and sends it to the CHIP task. Only one
// update is in flight at a time.
void MatterClosureMapping::push_now_() {
  this->push_in_flight_ = true;
  this->push_dirty_ = false;

  float position = this->cover_->position;
  auto operation = this->cover_->current_operation;
  bool moving = operation != cover::COVER_OPERATION_IDLE;
  bool opening = operation == cover::COVER_OPERATION_OPENING;

  // A movement started from ESPHome heads for the end of its direction of travel.
  if (moving && this->target_ < 0)
    this->target_ = opening ? 1 : 0;
  bool completed = this->moving_ && !moving && this->target_ >= 0 && at_end(position, this->target_ == 1);
  int8_t target = this->target_;
  if (!moving)
    this->target_ = -1;
  this->moving_ = moving;

  bool known = std::isfinite(position);
  cc::CurrentPositionEnum current = cc::CurrentPositionEnum::kPartiallyOpened;
  if (!moving) {
    if (at_end(position, true))
      current = cc::CurrentPositionEnum::kFullyOpened;
    else if (at_end(position, false))
      current = cc::CurrentPositionEnum::kFullyClosed;
  }

  uint16_t eid = this->endpoint_id();
  chip::DeviceLayer::SystemLayer().ScheduleLambda([this, eid, known, current, moving, target, completed]() {
    auto *server =
        esp_matter::data_model::provider::get_instance().registry().Get({eid, chip::app::Clusters::ClosureControl::Id});
    if (server == nullptr) {
      global_matter_component->defer_to_main_loop([this]() { this->push_done_(false); });
      return;
    }
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
    if (moving && target >= 0) {
      cc::GenericOverallTargetState target_value;
      target_value.position = chip::MakeOptional(chip::app::DataModel::MakeNullable(
          target == 1 ? cc::TargetPositionEnum::kMoveToFullyOpen : cc::TargetPositionEnum::kMoveToFullyClosed));
      target_state = chip::app::DataModel::MakeNullable(target_value);
    }
    (void) cluster->SetOverallTargetState(target_state);

    (void) cluster->SetMainState(moving ? cc::MainStateEnum::kMoving : cc::MainStateEnum::kStopped);
    if (completed)
      (void) cluster->GenerateMovementCompletedEvent();
    global_matter_component->defer_to_main_loop([this]() { this->push_done_(true); });
  });
}

void MatterClosureMapping::push_done_(bool applied) {
  this->push_in_flight_ = false;
  if (!applied) {
    // The cluster is not registered yet (startup): try again shortly so
    // OverallCurrentState gets populated.
    if (this->push_retries_ < kMaxPushRetries) {
      this->push_retries_++;
      this->push_in_flight_ = true;  // block other pushes until the retry runs
      global_matter_component->run_on_main_loop_after(kPushRetryMs, [this]() {
        this->push_in_flight_ = false;
        this->push_now_();
      });
    } else {
      ESP_LOGW(TAG, "Closure Control cluster of endpoint %u never became available", this->endpoint_id());
    }
    return;
  }
  this->push_retries_ = 0;
  if (this->push_dirty_)
    this->push_now_();
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_COVER && USE_MATTER_CLOSURE
