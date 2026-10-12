#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_LOCK)

#include "esphome/components/lock/lock.h"
#include "matter_lock_binding.h"

#include <esp_matter.h>

#include <cstdint>

namespace esphome::matter {

// Binds an ESPHome lock to the Matter Door Lock cluster of an endpoint, through
// the binding framework (see matter_lock_binding.h for the state machine).
//
// Matter -> ESPHome: the door lock server calls the
// emberAfPluginDoorLockOn*Command callbacks (defined in matter_lock.cpp) on the
// CHIP task. These are routed to handle_operation(), which defers to the
// ESPHome main loop.
// ESPHome -> Matter: the lock state is published to the DoorLockServer from the
// CHIP task whenever the ESPHome lock publishes a settled state.
class MatterLockMapping : public MatterLockBinding {
public:
  MatterLockMapping(lock::Lock *lock, uint16_t endpoint_id);

  // Returns the mapping for an endpoint or nullptr.
  static MatterLockMapping *find(uint16_t endpoint_id);

protected:
  void setup_() override;
  LockEntityState entity_state_() override;
  bool supports_open_() override;
  void command_(LockEntityState target) override;
  void write_door_lock_(const MatterLockPush &push) override;

  lock::Lock *lock_;
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_LOCK
