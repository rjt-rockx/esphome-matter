#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "esphome/core/log.h"
#include "matter_attributes.h"
#include "matter_component.h"
#include "matter_delegates.h"

#include <chrono>
#include <condition_variable>
#include <memory>
#include <mutex>

static const char *const TAG = "matter.delegate";

namespace esphome::matter {

namespace {

// Main loop only.
MatterCommandOutcome *g_active_outcome = nullptr;

struct SharedRequest {
  std::mutex mutex;
  std::condition_variable condition;
  bool started{false};
  bool done{false};
  bool abandoned{false};
  MatterCommandOutcome outcome;
};

} // namespace

MatterCommandOutcome *active_command_outcome() { return g_active_outcome; }

bool run_on_main_loop_and_wait(
    std::function<void(MatterCommandOutcome &)> work,
    MatterCommandOutcome &outcome, uint32_t timeout_ms) {
  if (global_matter_component == nullptr)
    return false;

  auto request = std::make_shared<SharedRequest>();
  defer_to_main_loop(global_matter_component, [request, work]() {
    {
      std::lock_guard<std::mutex> guard(request->mutex);
      if (request->abandoned)
        return; // The CHIP task gave up; the controller was told it failed.
      request->started = true;
    }
    MatterCommandOutcome local;
    g_active_outcome = &local;
    work(local);
    g_active_outcome = nullptr;
    {
      std::lock_guard<std::mutex> guard(request->mutex);
      request->outcome = std::move(local);
      request->done = true;
    }
    request->condition.notify_all();
  });

  std::unique_lock<std::mutex> lock(request->mutex);
  bool finished = request->condition.wait_for(
      lock, std::chrono::milliseconds(timeout_ms),
      [&request]() { return request->done; });
  if (!finished) {
    // If the work has started, it is allowed to finish; the controller gets a failure anyway.
    request->abandoned = !request->started;
    ESP_LOGW(TAG, "Main loop did not answer within %u ms",
             static_cast<unsigned>(timeout_ms));
    return false;
  }
  outcome = std::move(request->outcome);
  return true;
}

// Defined in matter_modes.cpp / matter_operational_state.cpp.
#ifdef USE_MATTER_MODES
DelegateAttach attach_mode_delegate(esp_matter::endpoint_t *endpoint,
                                    uint32_t cluster_id);
#endif
#ifdef USE_MATTER_OPERATIONAL_STATE
DelegateAttach
attach_operational_state_delegate(esp_matter::endpoint_t *endpoint,
                                  uint32_t cluster_id);
#endif
#ifdef USE_MATTER_LAUNDRY_CONTROLS
DelegateAttach
attach_laundry_controls_delegate(esp_matter::endpoint_t *endpoint,
                                 uint32_t cluster_id);
#endif

bool attach_delegate(esp_matter::endpoint_t *endpoint, uint32_t cluster_id) {
#ifdef USE_MATTER_MODES
  switch (attach_mode_delegate(endpoint, cluster_id)) {
  case DelegateAttach::OK:
    return true;
  case DelegateAttach::FAILED:
    return false;
  default:
    break;
  }
#endif
#ifdef USE_MATTER_OPERATIONAL_STATE
  switch (attach_operational_state_delegate(endpoint, cluster_id)) {
  case DelegateAttach::OK:
    return true;
  case DelegateAttach::FAILED:
    return false;
  default:
    break;
  }
#endif
#ifdef USE_MATTER_LAUNDRY_CONTROLS
  switch (attach_laundry_controls_delegate(endpoint, cluster_id)) {
  case DelegateAttach::OK:
    return true;
  case DelegateAttach::FAILED:
    return false;
  default:
    break;
  }
#endif
  ESP_LOGE(TAG, "No delegate configured for cluster 0x%08x on endpoint %u",
           static_cast<unsigned>(cluster_id),
           esp_matter::endpoint::get_id(endpoint));
  return false;
}

} // namespace esphome::matter

#endif // USE_MATTER
