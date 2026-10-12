#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_VALVE)

#include "esphome/components/valve/valve.h"
#include "matter_binding.h"

#include <app/clusters/valve-configuration-and-control-server/valve-configuration-and-control-delegate.h>
#include <esp_matter.h>

#include <atomic>
#include <cstdint>

namespace esphome::matter {

class MatterValveMapping;

// Delegate called by the Valve Configuration and Control cluster (on the CHIP
// task) for Open and Close commands. It only forwards to the mapping, which
// defers the work to the ESPHome main loop.
class MatterValveDelegate
    : public chip::app::Clusters::ValveConfigurationAndControl::Delegate {
public:
  explicit MatterValveDelegate(MatterValveMapping *mapping)
      : mapping_(mapping) {}

  chip::app::DataModel::Nullable<chip::Percent>
  HandleOpenValve(chip::app::DataModel::Nullable<chip::Percent> level) override;
  CHIP_ERROR HandleCloseValve() override;
  void HandleRemainingDurationTick(uint32_t duration) override {}

protected:
  MatterValveMapping *mapping_;
};

// ESPHome valve state as mirrored into the cluster.
struct MatterValveState {
  bool valid{false};
  uint8_t level{0};
  chip::app::Clusters::ValveConfigurationAndControl::ValveStateEnum state{
      chip::app::Clusters::ValveConfigurationAndControl::ValveStateEnum::
          kTransitioning};
};

// A pending Open or Close command.
struct MatterValveCommand {
  bool open{false};
  bool has_level{false};
  uint8_t level{100};
};

// Binds an ESPHome valve to the Matter Valve Configuration and Control cluster
// of an endpoint, through the binding framework.
//
// Matter -> ESPHome: Open and Close commands reach the delegate (CHIP task),
// which submits them; the main loop performs a valve call.
// ESPHome -> Matter: the valve state is published to the cluster
// (UpdateCurrentState / UpdateCurrentLevel) whenever the ESPHome valve
// publishes, coalesced to the latest state. Without this, CurrentState would
// stay at Transitioning after a command because the cluster never settles it
// by itself. After a command the binding waits (settle) for the valve to
// publish and otherwise pushes the unchanged state, so a valve that is already
// at the target does not leave CurrentState at Transitioning.
class MatterValveMapping
    : public MatterBinding<MatterValveState, MatterValveCommand> {
public:
  // Wait this long for the valve to publish after a command before pushing.
  static constexpr uint16_t SETTLE_MS = 200;

  MatterValveMapping(valve::Valve *valve, uint16_t endpoint_id);

  MatterValveDelegate *delegate() { return &this->delegate_; }

  // Called on the CHIP task by the delegate.
  chip::app::DataModel::Nullable<chip::Percent>
  handle_open(chip::app::DataModel::Nullable<chip::Percent> level);
  void handle_close();

protected:
  void setup_() override;
  MatterValveState capture_() override;
  void write_matter_(const MatterValveState &s) override;
  void apply_inbound_(const MatterValveCommand &cmd) override;

  valve::Valve *valve_;
  MatterValveDelegate delegate_;
  bool has_level_{false};
  // Last level pushed to Matter, returned to the cluster when opening.
  std::atomic<uint8_t> current_level_{0};
};

// Returns the delegate for an endpoint (as expected by the `delegate` field of
// the esp-matter valve_configuration_and_control config) or nullptr.
void *get_valve_delegate(uint16_t endpoint_id);

} // namespace esphome::matter

#endif // USE_MATTER && USE_VALVE
