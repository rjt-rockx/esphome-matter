#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "matter_binding.h"

#include <atomic>
#include <cstdint>

// Door Lock state machine on the binding framework. Free of ESPHome entity and
// CHIP cluster headers so it can be host-tested (tests/cpp/test_entity_bindings.cpp);
// matter_lock.cpp supplies the entity and DoorLockServer hooks.
namespace esphome::matter {

// Operation received from a Matter controller on the Door Lock cluster.
enum class MatterLockOperation : uint8_t {
  LOCK,
  UNLOCK,
  UNBOLT,
};

// Fabric and node that issued a remote operation (only valid when `valid`).
struct MatterLockOperationOrigin {
  bool valid{false};
  uint8_t fabric_index{0};
  uint64_t node_id{0};
};

// State of the ESPHome lock as the binding sees it.
enum class LockEntityState : uint8_t {
  NONE,
  LOCKED,
  UNLOCKED,
  OPEN,
  JAMMED,
  // LOCKING, UNLOCKING, OPENING: Matter has no transitional lock states.
  TRANSITIONAL,
};

// Door Lock LockState (DlLockState) values the binding can push.
enum class MatterLockPosition : uint8_t {
  NOT_FULLY_LOCKED,
  LOCKED,
  UNLOCKED,
  UNLATCHED,
};

// One settled state to publish. `seq` identifies the publish: 0 is "nothing".
struct MatterLockPush {
  uint32_t seq{0};
  MatterLockPosition position{MatterLockPosition::NOT_FULLY_LOCKED};
  bool jammed{false};
  // false only for the boot publish: no LockOperation event, nothing operated.
  bool announce{true};
  // DoorState must be written (DoorJammed, or null again after a jam).
  bool update_door_state{false};
  // Set once, for the first publish after a remote operation.
  MatterLockOperationOrigin origin;
};

struct MatterLockRequest {
  MatterLockOperation operation{MatterLockOperation::LOCK};
  MatterLockOperationOrigin origin;
};

// ESPHome lock <-> Door Lock cluster.
//
// Matter -> ESPHome: the door lock server's emberAfPluginDoorLockOn*Command
// callbacks (CHIP task) call handle_operation(), which submits the request;
// the main loop commands the lock (last request in a burst wins).
// ESPHome -> Matter: a settled lock state is published to the DoorLockServer
// on the CHIP task. Transitional states are not published (Matter has no such
// state): the previous state stays until the lock settles. A burst of publishes
// collapses to the newest one, which inherits the remote origin and the boot
// DoorState fix-up of the ones it replaced.
class MatterLockBinding : public MatterBinding<MatterLockPush, MatterLockRequest> {
public:
  explicit MatterLockBinding(uint16_t endpoint_id)
      : MatterBinding<MatterLockPush, MatterLockRequest>(
            endpoint_id, BindingPolicy{BootSync::ESPHOME_WINS, 0, false}) {}

  // CHIP task.
  void handle_operation(MatterLockOperation operation,
                        const MatterLockOperationOrigin &origin) {
    this->submit_inbound(MatterLockRequest{operation, origin});
  }

protected:
  // ---- Entity side (main loop)
  virtual LockEntityState entity_state_() = 0;
  virtual bool supports_open_() = 0;
  // `target` is LOCKED, UNLOCKED or OPEN.
  virtual void command_(LockEntityState target) = 0;
  // ---- Matter side (CHIP, inside the PushScope)
  virtual void write_door_lock_(const MatterLockPush &push) = 0;

  // Main loop: the entity's state callback.
  void on_entity_state_() {
    if (this->entity_state_() == LockEntityState::TRANSITIONAL)
      return;
    this->publish_();
  }

  void setup_() override { this->fresh_ = true; }

  // Publish the entity state now (a settled state, or an acknowledged no-op).
  void publish_() {
    this->fresh_ = true;
    this->request_push();
  }

  MatterLockPush capture_() override {
    // Reconcile pushes (policy, skipped push) and unrelated captures resend the
    // last publish, which write_matter_() recognises as already written.
    if (!this->fresh_)
      return this->last_;
    this->fresh_ = false;

    LockEntityState st = this->entity_state_();
    MatterLockPush p;
    p.position = MatterLockPosition::NOT_FULLY_LOCKED;
    p.jammed = st == LockEntityState::JAMMED;
    switch (st) {
    case LockEntityState::LOCKED:
      p.position = MatterLockPosition::LOCKED;
      break;
    case LockEntityState::UNLOCKED:
      p.position = MatterLockPosition::UNLOCKED;
      break;
    case LockEntityState::OPEN:
      p.position = MatterLockPosition::UNLATCHED;
      break;
    default:
      break;
    }
    p.announce = this->booted_;
    p.origin = this->pending_origin_;
    this->pending_origin_ = MatterLockOperationOrigin{};

    // esp-matter creates DoorState as DoorOpen, which would be a false claim.
    // The boot publish (and any change of the jam state) updates it.
    p.update_door_state =
        this->has_door_state_ && (p.jammed || this->door_jammed_ || !p.announce);
    this->door_jammed_ = p.jammed;

    // A publish that was never written is replaced, not lost.
    bool unsent = this->last_.seq != 0 &&
                  this->last_.seq != this->written_seq_.load();
    if (unsent) {
      if (!p.origin.valid)
        p.origin = this->last_.origin;
      p.update_door_state = p.update_door_state || this->last_.update_door_state;
    }
    this->booted_ = true;
    p.seq = ++this->seq_;
    this->last_ = p;
    return p;
  }

  void write_matter_(const MatterLockPush &s) override {
    if (s.seq == 0 || s.seq == this->written_seq_.load())
      return;
    this->written_seq_.store(s.seq);
    this->write_door_lock_(s);
  }

  void apply_inbound_(const MatterLockRequest &req) override {
    LockEntityState target = LockEntityState::UNLOCKED;
    switch (req.operation) {
    case MatterLockOperation::LOCK:
      target = LockEntityState::LOCKED;
      break;
    case MatterLockOperation::UNLOCK:
      target = LockEntityState::UNLOCKED;
      break;
    case MatterLockOperation::UNBOLT:
      target = this->supports_open_() ? LockEntityState::OPEN
                                      : LockEntityState::UNLOCKED;
      break;
    }
    ESP_LOGD("matter.lock", "Matter door lock operation on endpoint %u: %u",
             this->endpoint_id(), static_cast<unsigned>(req.operation));
    this->pending_origin_ = req.origin;

    // The lock may already be in the requested state, in which case it won't
    // publish anything. Report right away so the operation is acknowledged.
    if (this->entity_state_() == target) {
      this->publish_();
      return;
    }
    this->command_(target);
  }

  bool has_door_state_{false};

private:
  // Main loop.
  bool fresh_{false};
  bool booted_{false};
  bool door_jammed_{false};
  // Origin of the pending remote operation.
  MatterLockOperationOrigin pending_origin_;
  uint32_t seq_{0};
  MatterLockPush last_;
  // CHIP task writes, main loop reads.
  std::atomic<uint32_t> written_seq_{0};
};

} // namespace esphome::matter

#endif // USE_MATTER
