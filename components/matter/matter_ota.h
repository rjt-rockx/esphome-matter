#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER_OTA
#include "esphome/core/automation.h"
#include "esphome/core/component.h"

#include <platform/CHIPDeviceEvent.h>

#include <cstdint>
#include <string>
#include <vector>

namespace esphome::matter {

enum class MatterOtaEvent : uint8_t {
  DOWNLOAD_STARTED,
  DOWNLOAD_COMPLETE,
  DOWNLOAD_FAILED, // failed or aborted
  APPLY_STARTED,
  APPLY_FAILED,
};

class MatterOta;

// Fires with the OTA image processor state name, e.g. "download_in_progress".
class MatterOtaStateTrigger : public Trigger<std::string> {
public:
  explicit MatterOtaStateTrigger(MatterOta *parent);
};

class MatterOtaEventTrigger : public Trigger<> {
public:
  MatterOtaEventTrigger(MatterOta *parent, MatterOtaEvent event);
  MatterOtaEvent event() const { return this->event_; }

protected:
  MatterOtaEvent event_;
};

// Configures the Matter OTA Requestor (compiled in with
// CONFIG_ENABLE_OTA_REQUESTOR, started by esp_matter) and forwards its state
// changes to ESPHome automations. The CHIP task events are deferred to the
// ESPHome main loop.
class MatterOta : public Component {
public:
  void setup() override;
  void dump_config() override;
  // After MatterComponent (AFTER_CONNECTION - 5) so the CHIP stack is running
  // when the event handler is registered.
  float get_setup_priority() const override {
    return setup_priority::AFTER_CONNECTION - 7.0f;
  }

  void set_query_interval(uint32_t seconds) { this->query_interval_ = seconds; }
  void set_watchdog_timeout(uint32_t seconds) {
    this->watchdog_timeout_ = seconds;
  }

  void add_state_trigger(MatterOtaStateTrigger *trigger) {
    this->state_triggers_.push_back(trigger);
  }
  void add_event_trigger(MatterOtaEventTrigger *trigger) {
    this->event_triggers_.push_back(trigger);
  }

protected:
  static void handle_event_(const chip::DeviceLayer::ChipDeviceEvent *event,
                            intptr_t arg);
  void on_ota_state_(chip::DeviceLayer::OtaState state);

  uint32_t query_interval_{86400};
  uint32_t watchdog_timeout_{300};
  std::vector<MatterOtaStateTrigger *> state_triggers_;
  std::vector<MatterOtaEventTrigger *> event_triggers_;
};

} // namespace esphome::matter

#endif // USE_MATTER_OTA
