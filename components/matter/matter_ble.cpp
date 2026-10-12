#include "matter_ble.h"
#ifdef USE_MATTER_BLE

#include "esphome/core/log.h"

#include <platform/PlatformManager.h>

namespace esphome::matter {

static const char *const TAG = "matter.ble";

void MatterBle::handle_event_(const chip::DeviceLayer::ChipDeviceEvent *event,
                              intptr_t arg) {
  // Runs on the CHIP task; only logging here (ESP_LOG is thread safe).
  using chip::DeviceLayer::DeviceEventType;
  switch (event->Type) {
  case DeviceEventType::kCHIPoBLEAdvertisingChange:
    ESP_LOGI(TAG, "BLE advertising %s",
             event->CHIPoBLEAdvertisingChange.Result ==
                     chip::DeviceLayer::kActivity_Started
                 ? "started"
                 : "stopped");
    break;
  case DeviceEventType::kCHIPoBLEConnectionEstablished:
    ESP_LOGI(TAG, "BLE connection established");
    break;
  case DeviceEventType::kCHIPoBLEConnectionClosed:
    ESP_LOGI(TAG, "BLE connection closed");
    break;
  case DeviceEventType::kCHIPoBLEConnectionError:
    ESP_LOGW(TAG, "BLE connection error");
    break;
  case DeviceEventType::kCommissioningComplete:
    ESP_LOGI(TAG, "Commissioning complete; CHIP releases BLE "
                  "(CONFIG_USE_BLE_ONLY_FOR_COMMISSIONING)");
    break;
  default:
    break;
  }
}

void MatterBle::setup() {
  chip::DeviceLayer::PlatformMgr().LockChipStack();
  CHIP_ERROR err =
      chip::DeviceLayer::PlatformMgr().AddEventHandler(handle_event_, 0);
  chip::DeviceLayer::PlatformMgr().UnlockChipStack();
  if (err != CHIP_NO_ERROR) {
    ESP_LOGW(TAG, "Failed to register BLE event handler: %" CHIP_ERROR_FORMAT,
             err.Format());
  }
}

void MatterBle::dump_config() {
  ESP_LOGCONFIG(TAG, "Matter BLE commissioning:");
  ESP_LOGCONFIG(TAG, "  Host stack: NimBLE");
  ESP_LOGCONFIG(TAG, "  Released after commissioning: yes");
}

} // namespace esphome::matter

#endif // USE_MATTER_BLE
