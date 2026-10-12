#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "esphome/core/hal.h"

#include <platform/CHIPDeviceLayer.h>

// The main loop must not wait for the CHIP stack lock: the CHIP task can hold
// it while it waits for the main loop (e.g. a blocking delegate decision), and
// a long wait stalls every ESPHome component. Use run_on_matter_thread() for
// work that needs the lock. The one exception is diagnostic output, which
// takes the lock for a bounded time and skips the CHIP-side part on timeout.
namespace esphome::matter {

// Bounded lock for diagnostics on the main loop. Check locked() before
// touching CHIP state. Never use it for control flow.
class BoundedChipLock {
public:
  static constexpr uint32_t DEFAULT_TIMEOUT_MS = 200;

  explicit BoundedChipLock(uint32_t timeout_ms = DEFAULT_TIMEOUT_MS) {
    auto &mgr = chip::DeviceLayer::PlatformMgr();
    const uint32_t start = millis();
    while (!(this->locked_ = mgr.TryLockChipStack())) {
      if (millis() - start >= timeout_ms)
        return;
      delay(5);
    }
  }
  ~BoundedChipLock() {
    if (this->locked_)
      chip::DeviceLayer::PlatformMgr().UnlockChipStack();
  }
  BoundedChipLock(const BoundedChipLock &) = delete;
  BoundedChipLock &operator=(const BoundedChipLock &) = delete;

  bool locked() const { return this->locked_; }

private:
  bool locked_{false};
};

} // namespace esphome::matter

#endif // USE_MATTER
