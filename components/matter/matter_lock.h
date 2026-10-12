#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_LOCK)

#include "esphome/components/lock/lock.h"
#include "matter_endpoints.h"

#include <esp_matter.h>

#include <cstdint>

namespace esphome::matter {

// Operation received from a Matter controller on the Door Lock cluster.
enum class MatterLockOperation : uint8_t {
  LOCK,
  UNLOCK,
  UNBOLT,
};

// Fabric and node that issued a remote operation (only valid when `valid`).
struct MatterLockOperationOrigin {
  bool valid{false};
  uint8_t fabric_index{0};
  uint64_t node_id{0};
};

// Binds an ESPHome lock to the Matter Door Lock cluster of an endpoint.
//
// Matter -> ESPHome: the door lock server calls the
// emberAfPluginDoorLockOn*Command callbacks (defined in matter_lock.cpp) on the
// CHIP task. These are routed to handle_operation(), which defers to the
// ESPHome main loop.
// ESPHome -> Matter: the lock state is published to the DoorLockServer from the
// CHIP task whenever the ESPHome lock publishes a state.
class MatterLockMapping : public MatterEndpointMappingBase {
public:
  MatterLockMapping(lock::Lock *lock, uint16_t endpoint_id);

  void initialize() override;

  // Returns the mapping for an endpoint or nullptr.
  static MatterLockMapping *find(uint16_t endpoint_id);

  // Called on the CHIP task.
  void handle_operation(MatterLockOperation operation,
                        const MatterLockOperationOrigin &origin);

protected:
  void apply_operation_(MatterLockOperation operation,
                        const MatterLockOperationOrigin &origin);
  void on_lock_state_(lock::LockState state);
  void push_state_to_matter_(lock::LockState state);

  lock::Lock *lock_;
  bool has_door_state_{false};
  bool door_jammed_{false};
  // Origin of the pending remote operation. Only accessed on the main loop.
  MatterLockOperationOrigin pending_origin_;
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_LOCK
