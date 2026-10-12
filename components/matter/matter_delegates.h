#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include <esp_matter.h>

#include <cstdint>
#include <functional>
#include <string>

namespace esphome::matter {

// Shared plumbing of the delegate layer (mode clusters, Mode Select, Operational State, RVC Operational State).
// See docs/delegates.md.
//
// Matter delegate callbacks (ChangeToMode, Pause, Start, ...) run on the CHIP task and must answer
// synchronously, while ESPHome automations run on the main loop. The helper below bridges the two: the CHIP task
// posts the work to the main loop and waits (bounded) for the *synchronous part* of the user automation to finish.
// The automation can veto the command with a `matter.mode.reject` / `matter.operational_state.reject` action; if it
// does not, the command is accepted. Actions after a `delay:` are not part of the decision.

// Outcome of a command that may be vetoed by a user automation.
struct MatterCommandOutcome {
  bool rejected{false};
  // Mode clusters: ModeBase/derived StatusCode. Operational state: ErrorStateEnum value.
  uint8_t status{0};
  // Mode clusters: StatusText. Operational state: ErrorStateLabel.
  std::string text;
  // Operational state: ErrorStateDetails.
  std::string details;
};

// Longest time the CHIP task is blocked waiting for the main loop.
constexpr uint32_t MATTER_DELEGATE_TIMEOUT_MS = 750;

// Called on the CHIP task. Runs `work` on the main loop and waits until it returns or `timeout_ms` passed.
// Returns false on timeout (`work` then does not run if it had not started yet).
bool run_on_main_loop_and_wait(
    std::function<void(MatterCommandOutcome &)> work,
    MatterCommandOutcome &outcome,
    uint32_t timeout_ms = MATTER_DELEGATE_TIMEOUT_MS);

// Main loop only: the outcome of the command whose automation is currently running (nullptr otherwise).
MatterCommandOutcome *active_command_outcome();

// Result of a delegate attach attempt of one family of clusters.
enum class DelegateAttach : uint8_t { NOT_HANDLED, OK, FAILED };

// Called from the endpoint build callback (before esp_matter::start) once the cluster exists: installs the delegate
// of a delegate-driven cluster and creates the commands/events that the cluster needs for the configured delegate.
// Returns false (and logs) when the cluster has no delegate configuration or the setup failed.
bool attach_delegate(esp_matter::endpoint_t *endpoint, uint32_t cluster_id);

} // namespace esphome::matter

#endif // USE_MATTER
