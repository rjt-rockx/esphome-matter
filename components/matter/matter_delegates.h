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
// Matter delegate callbacks (ChangeToMode, Pause, Start, ...) run on the CHIP task and must answer synchronously,
// while ESPHome automations run on the main loop. Two strategies:
//
// * Default (non-blocking): the CHIP task decides from state that is kept current in atomics and from declarative
//   YAML rules (`accept: when_idle`, `allowed_states:`), answers immediately, and the automations are posted to the
//   main loop without waiting. `matter.mode.reject` / `matter.operational_state.reject` do not apply.
// * Opt-in `decide_in_automation: true`: the CHIP task posts the automation and waits (default 250 ms) for its
//   synchronous part. A `reject` action in it vetoes the command. A late completion is reconciled (see below).

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

// Default time the CHIP task is blocked in the opt-in blocking mode.
constexpr uint32_t MATTER_DELEGATE_TIMEOUT_MS = 250;

// Length of the longest prefix of `text` that is at most `max_bytes` long and ends on a UTF-8 character boundary.
inline size_t utf8_prefix_length(const std::string &text, size_t max_bytes) {
  if (text.size() <= max_bytes)
    return text.size();
  size_t length = max_bytes;
  // Step back over continuation bytes (10xxxxxx) so a multi-byte character is not cut in half.
  while (length > 0 && (static_cast<unsigned char>(text[length]) & 0xC0) == 0x80)
    length--;
  return length;
}

// Called on the CHIP task. Runs `work` on the main loop and waits until it returns or `timeout_ms` passed.
// Returns false on timeout. A `work` that had not started when the CHIP task gave up is dropped. A `work` that was
// already running finishes on the main loop and then `late` (main loop) is called with its outcome, so the caller can
// reconcile the state it answered "failed" for.
bool run_on_main_loop_and_wait(
    std::function<void(MatterCommandOutcome &)> work,
    MatterCommandOutcome &outcome,
    uint32_t timeout_ms = MATTER_DELEGATE_TIMEOUT_MS,
    std::function<void(const MatterCommandOutcome &)> late = nullptr);

// Main loop only: the outcome of the command whose automation is currently running (nullptr otherwise).
MatterCommandOutcome *active_command_outcome();

// True when the operational state of the endpoint is one of its `idle_states` (true if none is configured).
// Implemented in matter_operational_state.cpp, only call it when USE_MATTER_OPERATIONAL_STATE is defined.
bool endpoint_is_idle(uint16_t endpoint_id);

// Result of a delegate attach attempt of one family of clusters.
enum class DelegateAttach : uint8_t { NOT_HANDLED, OK, FAILED };

// Called from the endpoint build callback (before esp_matter::start) once the cluster exists: installs the delegate
// of a delegate-driven cluster and creates the commands/events that the cluster needs for the configured delegate.
// Returns false (and logs) when the cluster has no delegate configuration or the setup failed.
bool attach_delegate(esp_matter::endpoint_t *endpoint, uint32_t cluster_id);

} // namespace esphome::matter

#endif // USE_MATTER
