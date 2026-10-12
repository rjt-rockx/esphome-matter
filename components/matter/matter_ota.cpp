#include "matter_ota.h"
#ifdef USE_MATTER_OTA

#include "esphome/core/log.h"
#include "matter_component.h"

#include <esp_matter_ota.h>
#include <platform/ConfigurationManager.h>
#include <platform/PlatformManager.h>

namespace esphome::matter {

static const char *const TAG = "matter.ota";

MatterOtaStateTrigger::MatterOtaStateTrigger(MatterOta *parent) {
  parent->add_state_trigger(this);
}

MatterOtaEventTrigger::MatterOtaEventTrigger(MatterOta *parent,
                                             MatterOtaEvent event)
    : event_(event) {
  parent->add_event_trigger(this);
}

static const char *ota_state_name(chip::DeviceLayer::OtaState state) {
  switch (state) {
  case chip::DeviceLayer::kOtaSpaceAvailable:
    return "space_available";
  case chip::DeviceLayer::kOtaDownloadInProgress:
    return "download_in_progress";
  case chip::DeviceLayer::kOtaDownloadComplete:
    return "download_complete";
  case chip::DeviceLayer::kOtaDownloadFailed:
    return "download_failed";
  case chip::DeviceLayer::kOtaDownloadAborted:
    return "download_aborted";
  case chip::DeviceLayer::kOtaApplyInProgress:
    return "apply_in_progress";
  case chip::DeviceLayer::kOtaApplyComplete:
    return "apply_complete";
  case chip::DeviceLayer::kOtaApplyFailed:
    return "apply_failed";
  default:
    return "unknown";
  }
}

void MatterOta::handle_event_(const chip::DeviceLayer::ChipDeviceEvent *event,
                              intptr_t arg) {
  // Runs on the CHIP task: defer everything to the ESPHome main loop.
  if (event->Type != chip::DeviceLayer::DeviceEventType::kOtaStateChanged)
    return;
  auto *self = reinterpret_cast<MatterOta *>(arg);
  chip::DeviceLayer::OtaState state = event->OtaStateChanged.newState;
  self->defer([self, state]() { self->on_ota_state_(state); });
}

void MatterOta::on_ota_state_(chip::DeviceLayer::OtaState state) {
  using namespace chip::DeviceLayer;
  const char *name = ota_state_name(state);
  ESP_LOGI(TAG, "Matter OTA state: %s", name);

  for (auto *trigger : this->state_triggers_)
    trigger->trigger(std::string(name));

  MatterOtaEvent ev;
  switch (state) {
  case kOtaDownloadInProgress:
    ev = MatterOtaEvent::DOWNLOAD_STARTED;
    break;
  case kOtaDownloadComplete:
    ev = MatterOtaEvent::DOWNLOAD_COMPLETE;
    break;
  case kOtaDownloadFailed:
  case kOtaDownloadAborted:
    ev = MatterOtaEvent::DOWNLOAD_FAILED;
    break;
  case kOtaApplyInProgress:
    ev = MatterOtaEvent::APPLY_STARTED;
    break;
  case kOtaApplyFailed:
    ev = MatterOtaEvent::APPLY_FAILED;
    break;
  default:
    return;
  }
  for (auto *trigger : this->event_triggers_) {
    if (trigger->event() == ev)
      trigger->trigger();
  }
}

void MatterOta::setup() {
  // Without a running Matter stack there is nothing to configure or observe.
  if (global_matter_component == nullptr || global_matter_component->is_failed()) {
    this->mark_failed();
    return;
  }
  // The requestor itself is initialized and started by esp_matter::start()
  // (esp_matter_ota_requestor_init / _start) when CONFIG_ENABLE_OTA_REQUESTOR
  // is set; only tune it and observe its state here.
  esp_matter_ota_config_t ota_config{};
  ota_config.periodic_query_timeout = this->query_interval_;
  ota_config.watchdog_timeout = this->watchdog_timeout_;

  // The driver's timers live on the CHIP task, so hold the stack lock while
  // changing them and registering the handler (one-off at boot, like dump_config).
  chip::DeviceLayer::PlatformMgr().LockChipStack();
  esp_err_t err = esp_matter_ota_requestor_set_config(ota_config);
  CHIP_ERROR cerr = chip::DeviceLayer::PlatformMgr().AddEventHandler(
      handle_event_, reinterpret_cast<intptr_t>(this));
  chip::DeviceLayer::PlatformMgr().UnlockChipStack();
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "Failed to configure OTA requestor: %s",
             esp_err_to_name(err));
  }
  if (cerr != CHIP_NO_ERROR) {
    ESP_LOGW(TAG, "Failed to register OTA event handler: %" CHIP_ERROR_FORMAT,
             cerr.Format());
  }
}

void MatterOta::dump_config() {
  ESP_LOGCONFIG(TAG, "Matter OTA Requestor:");
  ESP_LOGCONFIG(TAG, "  Query interval: %us", (unsigned) this->query_interval_);
  ESP_LOGCONFIG(TAG, "  Watchdog timeout: %us",
                (unsigned) this->watchdog_timeout_);
  uint32_t version = 0;
  if (chip::DeviceLayer::ConfigurationMgr().GetSoftwareVersion(version) ==
      CHIP_NO_ERROR) {
    ESP_LOGCONFIG(TAG, "  Software version: %u", (unsigned) version);
  }
}

} // namespace esphome::matter

#endif // USE_MATTER_OTA
