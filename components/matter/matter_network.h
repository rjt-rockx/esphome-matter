#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include <cstdint>

#include <platform/CHIPDeviceEvent.h>

namespace esphome::matter {

// Milestones on the way from boot to a fully reconnected device. They are
// logged with timestamps (tag "matter.timing") so users can measure how long a
// restarted device takes to come back. See docs/troubleshooting-network.md.
enum class NetworkMark : uint8_t {
  IP_UP,
  DNSSD_PUBLISH,
  SRP_REGISTERED,
  CONTROLLER_RESOLVED,
  CASE_ESTABLISHED,
  SUBSCRIPTION_RESUMED,
};

// Feeds every CHIP device event to the network helper. Must be called from the
// CHIP task (i.e. from the esp_matter event callback). Records the timing
// marks and re-advertises DNS-SD on Thread connectivity changes.
void network_on_device_event(const chip::DeviceLayer::ChipDeviceEvent *event);

// Records a milestone (first occurrence only counts towards the summary).
// Must be called from the CHIP task; the bookkeeping is not synchronized.
void network_mark(NetworkMark mark, const char *detail = nullptr);

// Schedules a retry of the operational/commissionable DNS-SD publication with
// exponential backoff. Must be called from the CHIP task.
void network_schedule_dnssd_retry(const char *reason);

// Bracket one DNS-SD update (CHIP's RemoveServices ... FinalizeServiceUpdate).
// The retry backoff is only reset when an update finished without any failed
// publish; resetting after each single successful publish would retry at the
// minimum delay forever when one of several services keeps failing.
void network_dnssd_update_started();
void network_dnssd_update_finished();

} // namespace esphome::matter

#endif // USE_MATTER
