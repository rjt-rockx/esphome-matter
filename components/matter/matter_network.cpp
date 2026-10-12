#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "matter_network.h"

#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#ifdef USE_OPENTHREAD
#include "esphome/components/openthread/openthread.h"
#include <openthread/thread.h>
#endif // USE_OPENTHREAD

#include <algorithm>
#include <cinttypes>
#include <cstdio>

#include <app/InteractionModelEngine.h>
#include <app/ReadHandler.h>
#include <app/SubscriptionResumptionStorage.h>
#include <app/server/Dnssd.h>
#include <platform/CHIPDeviceLayer.h>
#include <system/SystemClock.h>

namespace {

static const char *const TAG = "matter.net";
static const char *const TAG_TIMING = "matter.timing";

// With `matter: debug: true` the reconnect milestones are logged at INFO level
// (and the helper polls for subscription state). Without it they are only
// visible at VERBOSE level, which is compiled out of normal builds.
#ifdef MATTER_DEBUG_NETWORK
#define MATTER_TIMING_LOG(...) ESP_LOGI(TAG_TIMING, __VA_ARGS__)
#else
#define MATTER_TIMING_LOG(...) ESP_LOGV(TAG_TIMING, __VA_ARGS__)
#endif

using esphome::matter::NetworkMark;

constexpr size_t MARK_COUNT = 6;

const char *mark_name(NetworkMark mark) {
  switch (mark) {
  case NetworkMark::IP_UP:
    return "IP up";
  case NetworkMark::DNSSD_PUBLISH:
    return "DNS-SD publish";
  case NetworkMark::SRP_REGISTERED:
    return "SRP registered";
  case NetworkMark::CONTROLLER_RESOLVED:
    return "Controller resolved";
  case NetworkMark::CASE_ESTABLISHED:
    return "CASE session established";
  case NetworkMark::SUBSCRIPTION_RESUMED:
    return "Subscription active";
  }
  return "?";
}

// Only touched from the CHIP task, except for network_mark() which is cheap
// and tolerates the (benign) race with the other callers.
uint32_t mark_ms[MARK_COUNT]{};
bool marked[MARK_COUNT]{};
bool connectivity_lost{false};
bool summary_logged{false};

// DNS-SD (re-)advertisement scheduling.
constexpr uint32_t RETRY_DELAY_MIN_MS = 1000;
constexpr uint32_t RETRY_DELAY_MAX_MS = 30000;
constexpr uint32_t THREAD_READVERTISE_DELAY_MS = 500;
bool start_server_pending{false};
uint32_t retry_delay_ms{RETRY_DELAY_MIN_MS};
const char *start_server_reason{""};

void reset_marks() {
  for (size_t i = 0; i < MARK_COUNT; i++) {
    marked[i] = false;
    mark_ms[i] = 0;
  }
  summary_logged = false;
}

void log_summary() {
  const auto idx = [](NetworkMark m) { return static_cast<size_t>(m); };
  if (summary_logged || !marked[idx(NetworkMark::IP_UP)] ||
      !marked[idx(NetworkMark::DNSSD_PUBLISH)] ||
      !marked[idx(NetworkMark::CASE_ESTABLISHED)] ||
      !marked[idx(NetworkMark::SUBSCRIPTION_RESUMED)])
    return;
  summary_logged = true;
  const uint32_t base = mark_ms[idx(NetworkMark::IP_UP)];
  MATTER_TIMING_LOG("Reconnect summary (ms after IP up): dnssd=%" PRIu32
                    " srp=%s controller_resolved=%s case=%" PRIu32
                    " subscription=%" PRIu32,
                    mark_ms[idx(NetworkMark::DNSSD_PUBLISH)] - base,
                    marked[idx(NetworkMark::SRP_REGISTERED)] ? "yes" : "n/a",
                    marked[idx(NetworkMark::CONTROLLER_RESOLVED)] ? "yes"
                                                                  : "no",
                    mark_ms[idx(NetworkMark::CASE_ESTABLISHED)] - base,
                    mark_ms[idx(NetworkMark::SUBSCRIPTION_RESUMED)] - base);
}

void start_server_timer_cb(chip::System::Layer *, void *) {
  start_server_pending = false;
#ifdef USE_OPENTHREAD
  // Thread: only (re-)advertise once we are attached, otherwise the next role
  // change will trigger us again.
  {
    auto lock = esphome::openthread::InstanceLock::try_acquire(200);
    if (lock) {
      const otDeviceRole role = otThreadGetDeviceRole(lock.get_instance());
      if (role == OT_DEVICE_ROLE_DISABLED || role == OT_DEVICE_ROLE_DETACHED) {
        ESP_LOGD(TAG, "Skipping DNS-SD re-advertise (%s): Thread role is %s",
                 start_server_reason, otThreadDeviceRoleToString(role));
        return;
      }
    }
  }
#endif // USE_OPENTHREAD
  ESP_LOGD(TAG, "Re-advertising DNS-SD (%s)", start_server_reason);
  chip::app::DnssdServer::Instance().StartServer();
}

void schedule_start_server(uint32_t delay_ms, const char *reason) {
  if (start_server_pending)
    return;
  CHIP_ERROR err = chip::DeviceLayer::SystemLayer().StartTimer(
      chip::System::Clock::Milliseconds32(delay_ms), start_server_timer_cb,
      nullptr);
  if (err != CHIP_NO_ERROR) {
    ESP_LOGW(TAG, "Failed to schedule DNS-SD re-advertise: %" CHIP_ERROR_FORMAT,
             err.Format());
    return;
  }
  start_server_pending = true;
  start_server_reason = reason;
  ESP_LOGD(TAG, "DNS-SD re-advertise scheduled in %" PRIu32 " ms (%s)",
           delay_ms, reason);
}

#ifdef MATTER_DEBUG_NETWORK
constexpr uint32_t SUBSCRIPTION_POLL_MS = 1000;
constexpr uint32_t SUBSCRIPTION_POLL_MAX = 900; // 15 minutes
uint32_t subscription_polls{0};

void subscription_poll_cb(chip::System::Layer *, void *) {
  auto *engine = chip::app::InteractionModelEngine::GetInstance();
  const uint32_t active = engine->GetNumActiveReadHandlers(
      chip::app::ReadHandler::InteractionType::Subscribe);
  if (active > 0) {
    char detail[48];
    snprintf(detail, sizeof(detail), "%" PRIu32 " active subscription(s)",
             active);
    esphome::matter::network_mark(NetworkMark::SUBSCRIPTION_RESUMED, detail);
    return;
  }
  if (++subscription_polls < SUBSCRIPTION_POLL_MAX) {
    chip::DeviceLayer::SystemLayer().StartTimer(
        chip::System::Clock::Milliseconds32(SUBSCRIPTION_POLL_MS),
        subscription_poll_cb, nullptr);
  } else {
    ESP_LOGI(TAG_TIMING, "No subscription became active within 15 minutes");
  }
}

void start_subscription_poll() {
#if CHIP_CONFIG_PERSIST_SUBSCRIPTIONS
  auto *engine = chip::app::InteractionModelEngine::GetInstance();
  auto *storage = engine->GetSubscriptionResumptionStorage();
  if (storage != nullptr) {
    uint32_t count = 0;
    uint16_t max_min_interval = 0;
    auto *iterator = storage->IterateSubscriptions();
    if (iterator != nullptr) {
      chip::app::SubscriptionResumptionStorage::SubscriptionInfo info;
      while (iterator->Next(info)) {
        count++;
        max_min_interval = std::max(max_min_interval, info.mMinInterval);
      }
      iterator->Release();
    }
    ESP_LOGI(TAG_TIMING,
             "%" PRIu32 " persisted subscription(s); CHIP resumes them after "
             "the largest persisted MinInterval (%u s) plus session setup",
             count, static_cast<unsigned>(max_min_interval));
  }
#endif // CHIP_CONFIG_PERSIST_SUBSCRIPTIONS
  subscription_polls = 0;
  chip::DeviceLayer::SystemLayer().StartTimer(
      chip::System::Clock::Milliseconds32(SUBSCRIPTION_POLL_MS),
      subscription_poll_cb, nullptr);
}
#endif // MATTER_DEBUG_NETWORK

} // namespace

namespace esphome::matter {

void network_mark(NetworkMark mark, const char *detail) {
  const size_t index = static_cast<size_t>(mark);
  const uint32_t now = millis();
  const bool first = !marked[index];
  if (first) {
    marked[index] = true;
    mark_ms[index] = now;
  }
  const bool have_base = marked[static_cast<size_t>(NetworkMark::IP_UP)];
  const uint32_t since_ip =
      have_base ? now - mark_ms[static_cast<size_t>(NetworkMark::IP_UP)] : 0;
  MATTER_TIMING_LOG("[t=%" PRIu32 " ms, +%" PRIu32 " ms since IP up%s] %s%s%s%s",
                    now, since_ip, have_base ? "" : " (IP not seen yet)",
                    mark_name(mark), first ? "" : " (again)",
                    detail != nullptr ? ": " : "",
                    detail != nullptr ? detail : "");
  if (first)
    log_summary();
}

void network_schedule_dnssd_retry(const char *reason) {
  const uint32_t delay = retry_delay_ms;
  if (!start_server_pending)
    retry_delay_ms = std::min(retry_delay_ms * 2, RETRY_DELAY_MAX_MS);
  schedule_start_server(delay, reason);
}

void network_dnssd_publish_succeeded() { retry_delay_ms = RETRY_DELAY_MIN_MS; }

void network_on_device_event(const chip::DeviceLayer::ChipDeviceEvent *event) {
  using namespace chip::DeviceLayer;
  switch (event->Type) {
  case DeviceEventType::kInterfaceIpAddressChanged: {
    const auto type = event->InterfaceIpAddressChanged.Type;
    if (type == InterfaceIpChangeType::kIpV4_Assigned ||
        type == InterfaceIpChangeType::kIpV6_Assigned) {
      if (connectivity_lost) {
        // A reconnect after a network outage: measure from scratch.
        reset_marks();
        connectivity_lost = false;
      }
      network_mark(NetworkMark::IP_UP, type == InterfaceIpChangeType::kIpV4_Assigned
                                           ? "IPv4 assigned"
                                           : "IPv6 assigned");
      // On Wi-Fi/Ethernet esp-matter calls DnssdServer::StartServer() from its
      // own event handler for this event, so nothing else to do here.
    } else {
      connectivity_lost = true;
      MATTER_TIMING_LOG("[t=%" PRIu32 " ms] IP lost", millis());
    }
    break;
  }
  case DeviceEventType::kThreadConnectivityChange:
    if (event->ThreadConnectivityChange.Result == kConnectivity_Established) {
      if (connectivity_lost) {
        reset_marks();
        connectivity_lost = false;
      }
      network_mark(NetworkMark::IP_UP, "Thread connectivity established");
#ifdef USE_OPENTHREAD
      // esp-matter's own Thread re-advertise is disabled (ESPHome owns the
      // OpenThread stack), so re-advertise here.
      schedule_start_server(THREAD_READVERTISE_DELAY_MS,
                            "Thread connectivity established");
#endif // USE_OPENTHREAD
    } else if (event->ThreadConnectivityChange.Result == kConnectivity_Lost) {
      connectivity_lost = true;
      MATTER_TIMING_LOG("[t=%" PRIu32 " ms] Thread connectivity lost", millis());
    }
    break;
#ifdef USE_OPENTHREAD
  case DeviceEventType::kThreadStateChange:
    if (event->ThreadStateChange.RoleChanged)
      schedule_start_server(THREAD_READVERTISE_DELAY_MS, "Thread role changed");
    else if (event->ThreadStateChange.AddressChanged)
      schedule_start_server(THREAD_READVERTISE_DELAY_MS,
                            "Thread address changed");
    break;
#endif // USE_OPENTHREAD
  case DeviceEventType::kDnssdInitialized:
    MATTER_TIMING_LOG("[t=%" PRIu32 " ms] DNS-SD initialized", millis());
    break;
  case DeviceEventType::kServerReady:
    MATTER_TIMING_LOG("[t=%" PRIu32 " ms] Matter server ready", millis());
#ifdef MATTER_DEBUG_NETWORK
    start_subscription_poll();
#endif // MATTER_DEBUG_NETWORK
    break;
  case DeviceEventType::kSecureSessionEstablished: {
    const auto &s = event->SecureSessionEstablished;
    if (s.SecureSessionType == 2) { // CASE
      char detail[64];
      snprintf(detail, sizeof(detail), "node=0x%016" PRIx64 " fabric=%u",
               s.PeerNodeId, static_cast<unsigned>(s.FabricIndex));
      network_mark(NetworkMark::CASE_ESTABLISHED, detail);
    }
    break;
  }
  default:
    break;
  }
}

} // namespace esphome::matter

#endif // USE_MATTER
