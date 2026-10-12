#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "matter_binding.h"

#include <atomic>
#include <cmath>
#include <cstdint>

// Closure Control state machine on the binding framework. Free of ESPHome
// entity and CHIP cluster headers so it can be host-tested
// (tests/cpp/test_entity_bindings.cpp); matter_closure.cpp supplies the entity
// hooks and the cluster writes.
namespace esphome::matter {

enum class ClosureEntityOperation : uint8_t { IDLE, OPENING, CLOSING };

struct ClosureEntityView {
  float position{NAN};
  ClosureEntityOperation operation{ClosureEntityOperation::IDLE};
};

// CurrentPositionEnum values the binding can report.
enum class ClosurePosition : uint8_t { PARTIALLY_OPENED, FULLY_CLOSED, FULLY_OPENED };

// One snapshot to write to the Closure Control cluster.
struct MatterClosurePush {
  bool valid{false};
  bool known{false};
  ClosurePosition current{ClosurePosition::PARTIALLY_OPENED};
  bool moving{false};
  // -1 none, 0 closed, 1 open: end the current movement is heading to.
  int8_t target{-1};
  // Number of movements that ended at their target so far. MovementCompleted
  // is emitted once per increment even when snapshots were coalesced.
  uint32_t completed_count{0};
};

enum class ClosureCommand : uint8_t { NONE, OPEN, CLOSE, STOP };

// ESPHome cover <-> Closure Control cluster (Positioning only).
//
// Matter -> ESPHome: MoveTo (fully open / fully closed) and Stop reach the
// delegate on the CHIP task and are submitted; the main loop commands the
// cover. The newest command wins.
// ESPHome -> Matter: on every cover state a snapshot is captured on the main
// loop and written on the CHIP task: MainState (Moving while the cover is
// opening or closing, else Stopped), OverallCurrentState.Position
// (FullyClosed/FullyOpened only when idle at an end, PartiallyOpened otherwise,
// null while unknown) and OverallTargetState (the end of the direction of
// travel while moving, else null). MovementCompleted is emitted when a movement
// ends at its target end, never for a stopped or interrupted one, and never
// twice. A snapshot that was queued before a controller command is not written
// over it (the framework's stale push gate); the apply pushes the fresh state.
class MatterClosureBinding : public MatterBinding<MatterClosurePush, ClosureCommand> {
public:
  // An idle cover within this distance of an end is reported as fully open / closed.
  static constexpr float END_TOLERANCE = 0.01f;
  static constexpr uint8_t MAX_PUSH_RETRIES = 20;
  static constexpr uint32_t PUSH_RETRY_MS = 500;

  explicit MatterClosureBinding(uint16_t endpoint_id)
      : MatterBinding<MatterClosurePush, ClosureCommand>(
            endpoint_id, BindingPolicy{BootSync::ESPHOME_WINS, 0, true}) {}

  // CHIP task.
  void submit_command(ClosureCommand cmd) { this->submit_inbound(cmd); }

  static bool at_end(float position, bool open) {
    if (!std::isfinite(position))
      return false;
    return open ? position >= 1.0f - END_TOLERANCE : position <= END_TOLERANCE;
  }

protected:
  // ---- Entity side (main loop)
  virtual ClosureEntityView entity_() = 0;
  virtual void command_open_() = 0;
  virtual void command_close_() = 0;
  virtual void command_stop_() = 0;
  // ---- Matter side (CHIP, inside the PushScope). Returns false when the
  // cluster is not registered (yet); the push is then retried.
  virtual bool write_cluster_(const MatterClosurePush &s) = 0;
  // Whether the cover can be stopped (logged when not).
  virtual bool can_stop_() { return true; }

  void setup_() override {}

  // Main loop: the entity's state callback.
  void on_entity_state_() { this->request_push(); }

  void apply_inbound_(const ClosureCommand &cmd) override {
    switch (cmd) {
    case ClosureCommand::OPEN:
    case ClosureCommand::CLOSE:
      this->move_(cmd == ClosureCommand::OPEN);
      break;
    case ClosureCommand::STOP:
      this->stop_();
      break;
    case ClosureCommand::NONE:
      break;
    }
    // The framework reconciles right after this returns.
  }

  MatterClosurePush capture_() override {
    ClosureEntityView v = this->entity_();
    bool moving = v.operation != ClosureEntityOperation::IDLE;
    bool opening = v.operation == ClosureEntityOperation::OPENING;

    // A movement started from ESPHome heads for the end of its direction of travel.
    if (moving && this->target_ < 0)
      this->target_ = opening ? 1 : 0;
    bool completed = this->moving_ && !moving && this->target_ >= 0 && at_end(v.position, this->target_ == 1);
    int8_t target = this->target_;
    if (!moving)
      this->target_ = -1;
    this->moving_ = moving;
    if (completed)
      this->completed_count_++;

    MatterClosurePush p;
    p.valid = true;
    p.known = std::isfinite(v.position);
    p.current = ClosurePosition::PARTIALLY_OPENED;
    if (!moving) {
      if (at_end(v.position, true))
        p.current = ClosurePosition::FULLY_OPENED;
      else if (at_end(v.position, false))
        p.current = ClosurePosition::FULLY_CLOSED;
    }
    p.moving = moving;
    p.target = target;
    p.completed_count = this->completed_count_;
    return p;
  }

  void write_matter_(const MatterClosurePush &s) override {
    if (!s.valid)
      return;
    if (!this->write_cluster_(s)) {
      this->schedule_retry_();
      return;
    }
    this->retries_.store(0);
  }

  // CHIP task. For the write_cluster_() implementation: true if this snapshot
  // carries a movement that completed and was not announced yet.
  bool take_completion(const MatterClosurePush &s) {
    if (s.completed_count == this->emitted_count_)
      return false;
    this->emitted_count_ = s.completed_count;
    return true;
  }

  // Main loop state. `moving_` and `target_` are what this binding believes,
  // independent of the cluster's MainState (the cluster sets Stopped itself
  // after a successful Stop and Moving after a successful MoveTo).
  bool moving_{false};
  int8_t target_{-1};

private:
  void move_(bool open) {
    ESP_LOGD("matter.closure", "Matter MoveTo %s on endpoint %u", open ? "open" : "close", this->endpoint_id());
    ClosureEntityView v = this->entity_();
    bool idle = v.operation == ClosureEntityOperation::IDLE;
    if (idle && at_end(v.position, open)) {
      // Nothing to do. No movement, so no MovementCompleted; the cluster already
      // set MainState to Moving, which the reconcile push settles.
      this->target_ = -1;
    } else {
      this->target_ = open ? 1 : 0;
      if (open)
        this->command_open_();
      else
        this->command_close_();
    }
  }

  void stop_() {
    // An interrupted movement does not complete.
    this->target_ = -1;
    if (this->can_stop_())
      this->command_stop_();
    else
      ESP_LOGW("matter.closure", "Stop on endpoint %u ignored: the cover has no stop action", this->endpoint_id());
  }

  // CHIP task: the cluster is not registered yet (startup). Try again shortly
  // so OverallCurrentState gets populated.
  void schedule_retry_() {
    if (this->retries_.load() >= MAX_PUSH_RETRIES) {
      if (!this->gave_up_.exchange(true))
        ESP_LOGW("matter.closure", "Closure Control cluster of endpoint %u never became available",
                 this->endpoint_id());
      return;
    }
    this->retries_.fetch_add(1);
    binding_platform()->defer_main([this]() {
      binding_platform()->main_after(PUSH_RETRY_MS, [this]() { this->request_push(); });
    });
  }

  // Main loop.
  uint32_t completed_count_{0};
  // CHIP task.
  uint32_t emitted_count_{0};
  std::atomic<uint8_t> retries_{0};
  std::atomic<bool> gave_up_{false};
};

} // namespace esphome::matter

#endif // USE_MATTER
