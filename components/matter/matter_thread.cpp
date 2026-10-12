#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "matter_thread.h"

#include "esphome/core/log.h"

#ifdef MATTER_HOST_TEST
#include <atomic>
#else
#include <platform/CHIPDeviceLayer.h>

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include <cstdlib>
#endif

static const char *const TAG = "matter.thread";

namespace esphome::matter::thread {

#ifdef MATTER_HOST_TEST

namespace {
thread_local HostRole host_role = HostRole::ANY;
std::atomic<uint32_t> host_violations{0};
} // namespace

void capture_main_loop() {}
bool is_chip_thread() {
  return host_role == HostRole::ANY || host_role == HostRole::CHIP;
}
bool is_main_loop() {
  return host_role == HostRole::ANY || host_role == HostRole::MAIN;
}
void host_set_role(HostRole role) { host_role = role; }
uint32_t host_violation_count() { return host_violations.load(); }

void violation(const char *expected, const char *file, int line) {
  host_violations.fetch_add(1);
  ESP_LOGE(TAG, "Thread violation: expected %s at %s:%d", expected, file, line);
}

#else

namespace {
TaskHandle_t main_task = nullptr;
} // namespace

void capture_main_loop() {
  if (main_task == nullptr)
    main_task = xTaskGetCurrentTaskHandle();
}

bool is_chip_thread() {
  return chip::DeviceLayer::PlatformMgr().IsChipStackLockedByCurrentThread();
}

bool is_main_loop() {
  return main_task == nullptr || xTaskGetCurrentTaskHandle() == main_task;
}

void violation(const char *expected, const char *file, int line) {
  ESP_LOGE(TAG, "Thread violation: expected %s at %s:%d", expected, file, line);
  abort();
}

#endif

} // namespace esphome::matter::thread

#endif // USE_MATTER
