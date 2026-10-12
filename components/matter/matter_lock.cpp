#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_LOCK) && defined(USE_MATTER_LOCK)

#include "esphome/core/log.h"
#include "matter_component.h"
#include "matter_lock.h"

#include <app-common/zap-generated/attributes/Accessors.h>
#include <app/clusters/door-lock-server/door-lock-server.h>
#include <esp_matter_cluster.h>
#include <platform/CHIPDeviceLayer.h>

#include <vector>

static const char *const TAG = "matter.lock";

namespace esphome::matter {

namespace {

// Mappings are registered during setup() and never removed, so the CHIP task
// can safely read this afterwards.
std::vector<MatterLockMapping *> &lock_mappings() {
  static std::vector<MatterLockMapping *> mappings;
  return mappings;
}

} // namespace

void MatterComponent::register_lock(lock::Lock *lock, uint16_t endpoint_id) {
  auto *mapping = new MatterLockMapping(lock, endpoint_id);
  lock_mappings().push_back(mapping);
  this->mappings_.push_back(mapping);
}

MatterLockMapping::MatterLockMapping(lock::Lock *lock, uint16_t endpoint_id)
    : MatterEndpointMappingBase(endpoint_id), lock_(lock) {}

MatterLockMapping *MatterLockMapping::find(uint16_t endpoint_id) {
  for (auto *mapping : lock_mappings()) {
    if (mapping->endpoint_id() == endpoint_id)
      return mapping;
  }
  return nullptr;
}

void MatterLockMapping::initialize() {
  if (this->lock_ == nullptr)
    return;

  using namespace chip::app::Clusters;
  this->has_door_state_ =
      esp_matter::attribute::get(this->endpoint_id(), DoorLock::Id,
                                 DoorLock::Attributes::DoorState::Id) != nullptr;

  this->lock_->add_on_state_callback(
      [this](lock::LockState state) { this->on_lock_state_(state); });

  // Publish the initial state from the main loop to read the lock state
  // safely.
  global_matter_component->defer_to_main_loop(
      [this]() { this->on_lock_state_(this->lock_->state); });
}

void MatterLockMapping::handle_operation(
    MatterLockOperation operation, const MatterLockOperationOrigin &origin) {
  global_matter_component->defer_to_main_loop(
      [this, operation, origin]() { this->apply_operation_(operation, origin); });
}

void MatterLockMapping::apply_operation_(
    MatterLockOperation operation, const MatterLockOperationOrigin &origin) {
  lock::LockState target = lock::LOCK_STATE_UNLOCKED;
  switch (operation) {
  case MatterLockOperation::LOCK:
    target = lock::LOCK_STATE_LOCKED;
    break;
  case MatterLockOperation::UNLOCK:
    target = lock::LOCK_STATE_UNLOCKED;
    break;
  case MatterLockOperation::UNBOLT:
    target = this->lock_->traits.get_supports_open()
                 ? lock::LOCK_STATE_OPEN
                 : lock::LOCK_STATE_UNLOCKED;
    break;
  }

  ESP_LOGD(TAG, "Matter door lock operation on endpoint %u: %u",
           this->endpoint_id(), static_cast<unsigned>(operation));
  this->pending_origin_ = origin;

  // The lock may already be in the requested state, in which case it won't
  // publish anything. Report right away so the operation is acknowledged.
  if (this->lock_->state == target) {
    this->on_lock_state_(target);
    return;
  }

  switch (target) {
  case lock::LOCK_STATE_LOCKED:
    this->lock_->lock();
    break;
  case lock::LOCK_STATE_OPEN:
    this->lock_->open();
    break;
  default:
    this->lock_->unlock();
    break;
  }
}

void MatterLockMapping::on_lock_state_(lock::LockState state) {
  switch (state) {
  case lock::LOCK_STATE_LOCKING:
  case lock::LOCK_STATE_UNLOCKING:
  case lock::LOCK_STATE_OPENING:
    // Matter has no transitional lock states. Keep the previous state and
    // report the final state when the lock settles.
    return;
  default:
    this->push_state_to_matter_(state);
    break;
  }
}

// Pushes a settled ESPHome lock state to the Matter Door Lock cluster.
//
// ESPHome LOCKED   -> LockState Locked
// ESPHome UNLOCKED -> LockState Unlocked
// ESPHome OPEN     -> LockState Unlatched
// ESPHome JAMMED   -> LockState NotFullyLocked (+ DoorState DoorJammed when the
//                     DoorPositionSensor feature is enabled)
// ESPHome NONE     -> LockState NotFullyLocked
void MatterLockMapping::push_state_to_matter_(lock::LockState state) {
  using chip::app::Clusters::DoorLock::DlLockState;
  DlLockState matter_state = DlLockState::kNotFullyLocked;
  bool jammed = false;
  switch (state) {
  case lock::LOCK_STATE_LOCKED:
    matter_state = DlLockState::kLocked;
    break;
  case lock::LOCK_STATE_UNLOCKED:
    matter_state = DlLockState::kUnlocked;
    break;
  case lock::LOCK_STATE_OPEN:
    matter_state = DlLockState::kUnlatched;
    break;
  case lock::LOCK_STATE_JAMMED:
    jammed = true;
    break;
  default:
    break;
  }

  // Attribute a state change to the remote operation only once.
  MatterLockOperationOrigin origin = this->pending_origin_;
  this->pending_origin_ = MatterLockOperationOrigin{};

  uint16_t eid = this->endpoint_id();
  bool update_door_state =
      this->has_door_state_ && (jammed || this->door_jammed_);
  this->door_jammed_ = jammed;

  chip::DeviceLayer::SystemLayer().ScheduleLambda(
      [eid, matter_state, jammed, update_door_state, origin]() {
        using namespace chip::app::Clusters;
        auto &server = DoorLockServer::Instance();
        if (origin.valid) {
          server.SetLockState(
              eid, matter_state, DoorLock::OperationSourceEnum::kRemote,
              chip::app::DataModel::NullNullable,
              chip::app::DataModel::NullNullable,
              chip::app::DataModel::MakeNullable(
                  static_cast<chip::FabricIndex>(origin.fabric_index)),
              chip::app::DataModel::MakeNullable(
                  static_cast<chip::NodeId>(origin.node_id)));
        } else {
          server.SetLockState(eid, matter_state,
                              DoorLock::OperationSourceEnum::kManual);
        }
        if (update_door_state) {
          if (jammed)
            server.SetDoorState(eid, DoorLock::DoorStateEnum::kDoorJammed);
          else
            DoorLock::Attributes::DoorState::SetNull(eid);
        }
      });
}

} // namespace esphome::matter

// ---------------------------------------------------------------------------
// Door Lock server plugin callbacks.
//
// The connectedhomeip door lock server only ships weak callbacks that reject
// every remote operation, and expects the application to define the
// cluster init callback. This is the single definition of these callbacks in
// the build; they are compiled only when an ESPHome lock is exposed through
// `lock_id`. Operations are dispatched per endpoint to the bound ESPHome lock.
// They run on the CHIP task, so the actual lock operation is deferred to the
// ESPHome main loop.
//
// No PIN is required: the PIN code argument is ignored. The door lock server
// validates a supplied PIN against its credential database (or rejects it when
// the PIN and User features aren't enabled) before calling these.
// ---------------------------------------------------------------------------

namespace {

bool route_door_lock_operation(
    chip::EndpointId endpoint_id,
    const chip::app::DataModel::Nullable<chip::FabricIndex> &fabric_index,
    const chip::app::DataModel::Nullable<chip::NodeId> &node_id,
    esphome::matter::MatterLockOperation operation,
    chip::app::Clusters::DoorLock::OperationErrorEnum &err) {
  auto *mapping = esphome::matter::MatterLockMapping::find(endpoint_id);
  if (mapping == nullptr ||
      esphome::matter::global_matter_component == nullptr) {
    err = chip::app::Clusters::DoorLock::OperationErrorEnum::kUnspecified;
    return false;
  }
  esphome::matter::MatterLockOperationOrigin origin;
  if (!fabric_index.IsNull() && !node_id.IsNull()) {
    origin.valid = true;
    origin.fabric_index = fabric_index.Value();
    origin.node_id = node_id.Value();
  }
  mapping->handle_operation(operation, origin);
  return true;
}

} // namespace

// Not provided by esp-matter. Initializes the DoorLockServer for the endpoint.
void emberAfDoorLockClusterInitCallback(chip::EndpointId endpoint) {
  DoorLockServer::Instance().InitServer(endpoint);
}

bool emberAfPluginDoorLockOnDoorLockCommand(
    chip::EndpointId endpointId,
    const chip::app::DataModel::Nullable<chip::FabricIndex> &fabricIdx,
    const chip::app::DataModel::Nullable<chip::NodeId> &nodeId,
    const chip::Optional<chip::ByteSpan> &pinCode,
    chip::app::Clusters::DoorLock::OperationErrorEnum &err) {
  return route_door_lock_operation(endpointId, fabricIdx, nodeId,
                                   esphome::matter::MatterLockOperation::LOCK,
                                   err);
}

bool emberAfPluginDoorLockOnDoorUnlockCommand(
    chip::EndpointId endpointId,
    const chip::app::DataModel::Nullable<chip::FabricIndex> &fabricIdx,
    const chip::app::DataModel::Nullable<chip::NodeId> &nodeId,
    const chip::Optional<chip::ByteSpan> &pinCode,
    chip::app::Clusters::DoorLock::OperationErrorEnum &err) {
  return route_door_lock_operation(endpointId, fabricIdx, nodeId,
                                   esphome::matter::MatterLockOperation::UNLOCK,
                                   err);
}

bool emberAfPluginDoorLockOnDoorUnboltCommand(
    chip::EndpointId endpointId,
    const chip::app::DataModel::Nullable<chip::FabricIndex> &fabricIdx,
    const chip::app::DataModel::Nullable<chip::NodeId> &nodeId,
    const chip::Optional<chip::ByteSpan> &pinCode,
    chip::app::Clusters::DoorLock::OperationErrorEnum &err) {
  return route_door_lock_operation(endpointId, fabricIdx, nodeId,
                                   esphome::matter::MatterLockOperation::UNBOLT,
                                   err);
}

#endif // USE_MATTER && USE_LOCK && USE_MATTER_LOCK
