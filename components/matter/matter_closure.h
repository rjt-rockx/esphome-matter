#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_COVER) && defined(USE_MATTER_CLOSURE)

#include "esphome/components/cover/cover.h"
#include "matter_closure_binding.h"

#include <app/clusters/closure-control-server/ClosureControlClusterDelegate.h>
#include <esp_matter.h>

#include <cstdint>

namespace esphome::matter {

// Binds an ESPHome cover (garage door, gate, door) to the Matter Closure
// device type, i.e. the Closure Control cluster (Matter 1.5).
//
// Matter -> ESPHome: the cluster calls the delegate on the CHIP task for MoveTo
// (fully open / fully closed) and Stop. The work is deferred to the ESPHome
// main loop.
// ESPHome -> Matter: on every cover state the cluster object is updated on the
// CHIP task (state machine in matter_closure_binding.h): MainState (Moving while
// the cover is opening or closing, else Stopped), OverallCurrentState.Position
// (FullyClosed/FullyOpened only when idle at an end, PartiallyOpened otherwise,
// null while unknown) and OverallTargetState (the end of the direction of
// travel while moving, else null). MovementCompleted is emitted only when a
// movement ends at its target end. The cover state is read on the main loop and
// updates are coalesced by the binding framework, so an older state never
// overwrites a newer one.
//
// Only the Positioning feature is served. Closure Control is code driven in
// esp-matter, so the values are written through the cluster object and not
// through the attribute store.
class MatterClosureMapping : public MatterClosureBinding,
                             public chip::app::Clusters::ClosureControl::ClosureControlClusterDelegate {
public:
  MatterClosureMapping(cover::Cover *cover, uint16_t endpoint_id);

  // ClosureControlClusterDelegate (CHIP task)
  chip::Protocols::InteractionModel::Status HandleStopCommand() override;
  chip::Protocols::InteractionModel::Status HandleMoveToCommand(
      const chip::Optional<chip::app::Clusters::ClosureControl::TargetPositionEnum> &position,
      const chip::Optional<bool> &latch,
      const chip::Optional<chip::app::Clusters::Globals::ThreeLevelAutoEnum> &speed) override;
  chip::Protocols::InteractionModel::Status HandleCalibrateCommand() override;
  bool IsReadyToMove() override { return true; }
  chip::ElapsedS GetCalibrationCountdownTime() override { return 0; }
  chip::ElapsedS GetMovingCountdownTime() override { return 0; }
  chip::ElapsedS GetWaitingForMotionCountdownTime() override { return 0; }

protected:
  void setup_() override;
  ClosureEntityView entity_() override;
  void command_open_() override;
  void command_close_() override;
  void command_stop_() override;
  bool can_stop_() override { return this->has_stop_; }
  bool write_cluster_(const MatterClosurePush &s) override;

  cover::Cover *cover_;
  // Cached at construction/initialisation: the delegate runs on the CHIP task
  // and must not touch the cover traits.
  bool has_stop_{false};
};

// Delegate for the `delegate` field of the esp-matter closure_control config of
// an endpoint (nullptr when no cover is registered for it).
void *get_closure_delegate(uint16_t endpoint_id);

} // namespace esphome::matter

#endif // USE_MATTER && USE_COVER && USE_MATTER_CLOSURE
