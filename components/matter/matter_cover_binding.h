#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "matter_binding.h"

#include <cmath>
#include <cstdint>

// Window Covering state machine on the binding framework. Free of ESPHome
// entity and CHIP cluster headers so it can be host-tested
// (tests/cpp/test_entity_bindings.cpp); matter_cover.cpp supplies the entity
// hooks.
namespace esphome::matter {

namespace conversion {

// Matter Percent100ths: 0 = fully open, 10000 = fully closed.
// ESPHome cover position/tilt: 1.0 = fully open, 0.0 = fully closed.
constexpr uint16_t kCoverMatterOpen = 0;
constexpr uint16_t kCoverMatterClosed = 10000;

// Returns false when the ESPHome value is not a finite number (unknown).
inline bool cover_to_matter(float esphome_value, uint16_t &matter_value) {
  if (!std::isfinite(esphome_value))
    return false;
  float clamped = esphome_value < 0.0f ? 0.0f : (esphome_value > 1.0f ? 1.0f : esphome_value);
  matter_value = static_cast<uint16_t>(std::lround((1.0f - clamped) * kCoverMatterClosed));
  return true;
}

inline float cover_from_matter(uint16_t matter_value) {
  if (matter_value > kCoverMatterClosed)
    matter_value = kCoverMatterClosed;
  return 1.0f - static_cast<float>(matter_value) / static_cast<float>(kCoverMatterClosed);
}

} // namespace conversion

enum class CoverEntityOperation : uint8_t { IDLE, OPENING, CLOSING };

// What the binding reads from the ESPHome cover.
struct CoverEntityView {
  float position{NAN};
  float tilt{NAN};
  CoverEntityOperation operation{CoverEntityOperation::IDLE};
};

// Cover state as written to the Window Covering cluster.
struct MatterCoverPush {
  bool valid{false};
  bool has_tilt{false};
  bool lift_known{false};
  bool tilt_known{false};
  uint16_t lift{0};
  uint16_t lift_target{0};
  uint16_t tilt{0};
  uint16_t tilt_target{0};
  uint8_t operational_status{0};
};

// Controller requests, merged while they wait for the main loop. UpOrOpen and
// DownOrClose request lift and tilt in one go; they end up as one cover call.
struct MatterCoverRequest {
  bool has_lift{false};
  bool has_tilt{false};
  uint16_t lift{0};
  uint16_t tilt{0};
  // Stop is applied before the movement, like a stop followed by a request.
  bool stop{false};
};

// ESPHome cover <-> Window Covering cluster.
//
// Matter -> ESPHome: HandleMovement / HandleStopMotion (CHIP task, after
// connectedhomeip has set the TargetPosition attributes) submit a request. The
// main loop applies lift and tilt requests of the same burst as a single cover
// call; a stop discards the pending movement.
// ESPHome -> Matter: the position, target and operational status are written
// with attribute::report() (no attribute callbacks) when the cover publishes;
// a burst of publishes collapses into the newest state. After a controller
// command the binding waits SETTLE_MS for the cover to publish and otherwise
// pushes the unchanged state, so a refused command does not leave a stale
// target behind. A stop pushes at once.
class MatterCoverBinding : public MatterBinding<MatterCoverPush, MatterCoverRequest> {
public:
  static constexpr uint16_t SETTLE_MS = 200;

  // Window Covering (0x0102) attribute ids.
  static constexpr uint32_t WC_CLUSTER = 0x0102;
  static constexpr uint32_t WC_OPERATIONAL_STATUS = 0x000A;
  static constexpr uint32_t WC_CURRENT_LIFT = 0x000E;
  static constexpr uint32_t WC_CURRENT_TILT = 0x000F;
  static constexpr uint32_t WC_TARGET_LIFT = 0x000B;
  static constexpr uint32_t WC_TARGET_TILT = 0x000C;

  explicit MatterCoverBinding(uint16_t endpoint_id)
      : MatterBinding<MatterCoverPush, MatterCoverRequest>(
            endpoint_id, BindingPolicy{BootSync::ESPHOME_WINS, SETTLE_MS, true}) {}

  // CHIP task.
  void submit_movement(bool lift, uint16_t target) {
    MatterCoverRequest r;
    if (lift) {
      r.has_lift = true;
      r.lift = target;
    } else {
      r.has_tilt = true;
      r.tilt = target;
    }
    this->submit_inbound(r);
  }
  void submit_stop() {
    MatterCoverRequest r;
    r.stop = true;
    this->submit_inbound(r);
  }

protected:
  // ---- Entity side (main loop)
  virtual CoverEntityView entity_() = 0;
  // Performs one cover call. `lift`/`tilt` are set when requested; `stop` is a
  // separate call made before.
  virtual void command_stop_() = 0;
  virtual void command_move_(bool has_lift, uint16_t lift, bool has_tilt, uint16_t tilt) = 0;

  // Main loop: the entity's state callback.
  void on_entity_state_() {
    // At rest the target is the current position: forget the last request.
    if (this->entity_().operation == CoverEntityOperation::IDLE) {
      this->target_lift_valid_ = false;
      this->target_tilt_valid_ = false;
    }
    this->request_push();
  }

  void setup_() override {}

  void merge_inbound_(MatterCoverRequest &acc, const MatterCoverRequest &in) override {
    if (in.stop) {
      // A stop discards the movement requested before it.
      acc.has_lift = false;
      acc.has_tilt = false;
      acc.stop = true;
      return;
    }
    if (in.has_lift) {
      acc.has_lift = true;
      acc.lift = in.lift;
    }
    if (in.has_tilt) {
      acc.has_tilt = true;
      acc.tilt = in.tilt;
    }
  }

  void apply_inbound_(const MatterCoverRequest &req) override {
    if (req.stop) {
      this->target_lift_valid_ = false;
      this->target_tilt_valid_ = false;
      this->command_stop_();
      // The cover might not publish a state when it was already idle.
      this->request_push();
    }
    if (req.has_lift) {
      this->target_lift_ = req.lift;
      this->target_lift_valid_ = true;
    }
    if (req.has_tilt) {
      this->target_tilt_ = req.tilt;
      this->target_tilt_valid_ = true;
    }
    if (req.has_lift || req.has_tilt)
      this->command_move_(req.has_lift, req.lift, req.has_tilt, req.tilt);
  }

  MatterCoverPush capture_() override {
    MatterCoverPush p;
    CoverEntityView v = this->entity_();
    p.valid = true;
    p.has_tilt = this->has_tilt_;
    p.lift_known = conversion::cover_to_matter(v.position, p.lift);
    p.tilt_known = this->has_tilt_ && conversion::cover_to_matter(v.tilt, p.tilt);

    uint8_t state = 0; // OperationalState::Stall
    if (v.operation == CoverEntityOperation::OPENING)
      state = 1; // MovingUpOrOpen
    else if (v.operation == CoverEntityOperation::CLOSING)
      state = 2; // MovingDownOrClose

    // ESPHome has no target position. At rest the target is the current
    // position; while moving it is the last Matter request, or the end of the
    // direction of travel when ESPHome started the movement itself.
    p.lift_target = p.lift;
    p.tilt_target = p.tilt;
    if (state != 0) {
      if (p.lift_known) {
        bool consistent = this->target_lift_valid_ &&
                          (state == 1 ? this->target_lift_ <= p.lift : this->target_lift_ >= p.lift);
        p.lift_target = consistent ? this->target_lift_
                                   : (state == 1 ? conversion::kCoverMatterOpen : conversion::kCoverMatterClosed);
      }
      if (this->target_tilt_valid_)
        p.tilt_target = this->target_tilt_;
    }
    // Global (bits 0-1) and lift (bits 2-3) follow the cover operation. Tilt
    // (bits 4-5) is left at stopped because ESPHome has a single operation.
    p.operational_status = static_cast<uint8_t>(state | (state << 2));
    return p;
  }

  void write_matter_(const MatterCoverPush &s) override {
    if (!s.valid)
      return;
    this->report_position_(WC_TARGET_LIFT, s.lift_known, s.lift_target);
    this->report_position_(WC_CURRENT_LIFT, s.lift_known, s.lift);
    if (s.has_tilt) {
      this->report_position_(WC_TARGET_TILT, s.tilt_known, s.tilt_target);
      this->report_position_(WC_CURRENT_TILT, s.tilt_known, s.tilt);
    }
    esp_matter_attr_val_t status_val = esp_matter_bitmap8(s.operational_status);
    binding_platform()->write_attribute(this->endpoint_id_, WC_CLUSTER, WC_OPERATIONAL_STATUS, status_val, true);
  }

  bool has_tilt_{false};
  bool has_position_{false};
  bool has_stop_{false};

private:
  void report_position_(uint32_t attribute_id, bool known, uint16_t value) {
    esp_matter_attr_val_t val =
        esp_matter_nullable_uint16(known ? nullable<uint16_t>(value) : nullable<uint16_t>());
    binding_platform()->write_attribute(this->endpoint_id_, WC_CLUSTER, attribute_id, val, true);
  }

  // Main loop state: the last Matter request while it is moving.
  uint16_t target_lift_{0};
  uint16_t target_tilt_{0};
  bool target_lift_valid_{false};
  bool target_tilt_valid_{false};
};

} // namespace esphome::matter

#endif // USE_MATTER
