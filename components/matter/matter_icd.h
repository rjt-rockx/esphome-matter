#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_MATTER_ICD)
#include "esphome/core/automation.h"

#include <functional>
#include <vector>

namespace esphome::matter {

// Matter ICD (battery powered Thread device) glue, EXPERIMENTAL and not verified on hardware. See docs/icd.md.
//
// One chip::app::ICDStateObserver is registered with CHIP's ICD manager (the observer pool has exactly one free
// slot). Its callbacks run on the CHIP thread; they are forwarded to the ESPHome main loop where the on_active /
// on_idle triggers run.
class MatterIcd {
public:
  static MatterIcd &instance();

  // Main loop, after esp_matter::start(). Registers the state observer with the ICD manager and, since the ICD starts
  // in active mode before anybody can observe it, delivers one initial on_active to the main loop.
  void start();

  // Main loop.
  void add_active_callback(std::function<void()> &&cb) { this->active_callbacks_.push_back(std::move(cb)); }
  void add_idle_callback(std::function<void()> &&cb) { this->idle_callbacks_.push_back(std::move(cb)); }

  // Any thread. Keeps the device in active mode for the active mode threshold
  // (ICDNotifier::NotifyNetworkActivityNotification, run on the CHIP thread).
  void trigger_active();

  // Main loop only.
  void fire_active();
  void fire_idle();

private:
  bool started_{false};
  std::vector<std::function<void()>> active_callbacks_;
  std::vector<std::function<void()>> idle_callbacks_;
};

class MatterIcdActiveTrigger : public Trigger<> {
public:
  MatterIcdActiveTrigger() {
    MatterIcd::instance().add_active_callback([this]() { this->trigger(); });
  }
};

class MatterIcdIdleTrigger : public Trigger<> {
public:
  MatterIcdIdleTrigger() {
    MatterIcd::instance().add_idle_callback([this]() { this->trigger(); });
  }
};

template <typename... Ts> class MatterIcdTriggerActiveAction : public Action<Ts...> {
public:
  void play(const Ts &...x) override { MatterIcd::instance().trigger_active(); }
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_MATTER_ICD
