#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_MATTER_ICD)
#include "matter_icd.h"

#include "esphome/core/log.h"
#include "matter_attributes.h"
#include "matter_component.h"

#include <app/icd/server/ICDNotifier.h>
#include <app/server/Server.h>
#include <platform/CHIPDeviceLayer.h>

namespace esphome::matter {

static const char *const TAG = "matter.icd";

namespace {

// Runs on the CHIP thread, synchronously inside the ICD manager: only hop to the main loop, never block or take
// locks here (the CHIP lock is already held).
class IcdObserver : public chip::app::ICDStateObserver {
public:
  void OnEnterActiveMode() override { this->forward_(true); }
  void OnEnterIdleMode() override { this->forward_(false); }
  void OnTransitionToIdle() override {}
  void OnICDModeChange() override {}

private:
  void forward_(bool active) {
    if (global_matter_component == nullptr)
      return;
    global_matter_component->defer_to_main_loop([active]() {
      if (active)
        MatterIcd::instance().fire_active();
      else
        MatterIcd::instance().fire_idle();
    });
  }
};

IcdObserver s_observer;  // NOLINT(cppcoreguidelines-avoid-non-const-global-variables)

}  // namespace

MatterIcd &MatterIcd::instance() {
  static MatterIcd inst;
  return inst;
}

void MatterIcd::start() {
  if (this->started_)
    return;
  this->started_ = true;
#if CHIP_CONFIG_ENABLE_ICD_SERVER
  // Registration is CHIP-side work: run it on the Matter thread (it holds the CHIP lock there).
  run_on_matter_thread([]() {
    auto *slot = chip::Server::GetInstance().GetICDManager().RegisterObserver(&s_observer);
    if (slot == nullptr) {
      // The observer pool has 3 entries and CHIP uses 2 of them.
      ESP_LOGE(TAG, "No free ICD observer slot; on_active / on_idle will not fire");
      return;
    }
    ESP_LOGD(TAG, "ICD state observer registered");
  });
  // The ICD manager enters active mode during Server::Init(), before anything can observe it.
  if (global_matter_component != nullptr)
    global_matter_component->defer_to_main_loop([]() { MatterIcd::instance().fire_active(); });
#else
  ESP_LOGE(TAG, "matter: icd: is configured but connectedhomeip was built without the ICD server");
#endif
}

void MatterIcd::trigger_active() {
#if CHIP_CONFIG_ENABLE_ICD_SERVER
  // ICDNotifier must be used from the CHIP thread.
  run_on_matter_thread([]() { chip::app::ICDNotifier::GetInstance().NotifyNetworkActivityNotification(); });
#endif
}

void MatterIcd::fire_active() {
  ESP_LOGV(TAG, "active");
  for (auto &cb : this->active_callbacks_)
    cb();
}

void MatterIcd::fire_idle() {
  ESP_LOGV(TAG, "idle");
  for (auto &cb : this->idle_callbacks_)
    cb();
}

}  // namespace esphome::matter

#endif  // USE_MATTER && USE_MATTER_ICD
