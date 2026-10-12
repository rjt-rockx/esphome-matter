#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "esphome/core/log.h"
#include "matter_component.h"
#include "matter_generic_switch.h"

#include <app/clusters/switch-server/SwitchCluster.h>
#include <clusters/Switch/ClusterId.h>
#include <clusters/Switch/Enums.h>
#include <data_model_provider/esp_matter_data_model_provider.h>
#include <esp_matter_cluster.h>
#include <platform/CHIPDeviceLayer.h>

#include <cinttypes>

namespace esphome::matter {

static const char *const TAG = "matter.switch";

namespace {

constexpr uint32_t kFeatureMapAttributeId = 0xFFFC;

namespace switch_event = esp_matter::cluster::switch_cluster::event;

// Sets CurrentPosition on the code-driven Switch cluster server. Must run on the
// Matter thread. esp_matter::attribute::report/update only change esp-matter's
// attribute store, which this cluster does not read.
void set_current_position(uint16_t endpoint_id, uint8_t position) {
  auto *server =
      esp_matter::data_model::provider::get_instance().registry().Get(
          {endpoint_id, chip::app::Clusters::Switch::Id});
  if (server == nullptr) {
    ESP_LOGW(TAG, "Endpoint %u has no Switch cluster", endpoint_id);
    return;
  }
  auto *cluster = static_cast<chip::app::Clusters::SwitchCluster *>(server);
  if (cluster->SetCurrentPosition(position) != CHIP_NO_ERROR)
    ESP_LOGW(TAG, "Invalid Switch position %u on endpoint %u", position,
             endpoint_id);
}

void send_on_matter_thread(uint16_t endpoint_id, MatterSwitchEvent event,
                           uint8_t position, uint8_t count) {
  esp_err_t err = ESP_OK;
  switch (event) {
  case MatterSwitchEvent::SWITCH_LATCHED:
    set_current_position(endpoint_id, position);
    err = switch_event::send_switch_latched(endpoint_id, position);
    break;
  case MatterSwitchEvent::INITIAL_PRESS:
    set_current_position(endpoint_id, position);
    err = switch_event::send_initial_press(endpoint_id, position);
    break;
  case MatterSwitchEvent::LONG_PRESS:
    err = switch_event::send_long_press(endpoint_id, position);
    break;
  case MatterSwitchEvent::SHORT_RELEASE:
    err = switch_event::send_short_release(endpoint_id, position);
    set_current_position(endpoint_id, 0);
    break;
  case MatterSwitchEvent::LONG_RELEASE:
    err = switch_event::send_long_release(endpoint_id, position);
    set_current_position(endpoint_id, 0);
    break;
  case MatterSwitchEvent::MULTI_PRESS_ONGOING:
    set_current_position(endpoint_id, position);
    err = switch_event::send_multi_press_ongoing(endpoint_id, position, count);
    break;
  case MatterSwitchEvent::MULTI_PRESS_COMPLETE:
    err = switch_event::send_multi_press_complete(endpoint_id, position, count);
    break;
  }
  // ESP_OK only means the cluster exists. Matter silently drops events whose
  // Switch feature is not enabled or whose position is out of range.
  if (err != ESP_OK)
    ESP_LOGW(TAG, "Failed to send switch event %u on endpoint %u: %s",
             static_cast<unsigned>(event), endpoint_id, esp_err_to_name(err));
}

} // namespace

void set_switch_position(uint16_t endpoint_id, uint8_t position) {
  CHIP_ERROR err = chip::DeviceLayer::SystemLayer().ScheduleLambda(
      [endpoint_id, position]() { set_current_position(endpoint_id, position); });
  if (err != CHIP_NO_ERROR)
    ESP_LOGE(TAG, "Failed to schedule switch position: %" CHIP_ERROR_FORMAT,
             err.Format());
}

void send_switch_event(uint16_t endpoint_id, MatterSwitchEvent event,
                       uint8_t position, uint8_t count) {
  ESP_LOGD(TAG, "Switch event %u: endpoint=%u position=%u count=%u",
           static_cast<unsigned>(event), endpoint_id, position, count);
  CHIP_ERROR err = chip::DeviceLayer::SystemLayer().ScheduleLambda(
      [endpoint_id, event, position, count]() {
        send_on_matter_thread(endpoint_id, event, position, count);
      });
  if (err != CHIP_NO_ERROR)
    ESP_LOGE(TAG, "Failed to schedule switch event: %" CHIP_ERROR_FORMAT,
             err.Format());
}

#ifdef USE_BINARY_SENSOR

void MatterGenericSwitchMapping::initialize() {
  if (this->sensor_ == nullptr)
    return;
  using namespace chip::app::Clusters;
  if (!this->has_server_cluster(Switch::Id)) {
    ESP_LOGE(TAG,
             "Endpoint %u has no Switch cluster. Switch events are disabled",
             this->endpoint_id());
    return;
  }

  // Only emit events of the features that are enabled on the endpoint: Matter
  // silently drops the events of disabled features.
  esp_matter_attr_val_t value;
  uint32_t feature_map = 0;
  if (esp_matter::attribute::get_val(this->endpoint_id(), Switch::Id,
                                     kFeatureMapAttributeId,
                                     &value) == ESP_OK)
    feature_map = value.val.u32;
  auto has_feature = [feature_map](Switch::Feature feature) {
    return (feature_map & static_cast<uint32_t>(feature)) != 0;
  };
  this->latching_ = has_feature(Switch::Feature::kLatchingSwitch);
  this->release_ = has_feature(Switch::Feature::kMomentarySwitchRelease);
  this->long_press_ = has_feature(Switch::Feature::kMomentarySwitchLongPress);
  this->multi_press_ = has_feature(Switch::Feature::kMomentarySwitchMultiPress);
  // Action switches never send MultiPressOngoing.
  this->action_switch_ = has_feature(Switch::Feature::kActionSwitch);
  if (this->multi_press_ &&
      esp_matter::attribute::get_val(this->endpoint_id(), Switch::Id,
                                     Switch::Attributes::MultiPressMax::Id,
                                     &value) == ESP_OK)
    this->multi_press_max_ = value.val.u8;

  this->sensor_->add_on_state_callback(
      [this](bool pressed) { this->on_state_(pressed); });
}

void MatterGenericSwitchMapping::dump_config() {
  ESP_LOGCONFIG(TAG,
                "Matter Switch:\n"
                "  Endpoint: %u\n"
                "  Type: %s\n"
                "  Release: %s, long press: %s (%" PRIu32
                " ms), multi press: %s (max %u, window %" PRIu32 " ms)",
                this->endpoint_id(), this->latching_ ? "latching" : "momentary",
                YESNO(this->release_), YESNO(this->long_press_),
                this->long_press_ms_, YESNO(this->multi_press_),
                this->multi_press_max_, this->multi_press_window_ms_);
}

void MatterGenericSwitchMapping::on_state_(bool pressed) {
  if (this->latching_) {
    send_switch_event(this->endpoint_id(), MatterSwitchEvent::SWITCH_LATCHED,
                      pressed ? 1 : 0);
  } else if (pressed) {
    this->on_press_();
  } else {
    this->on_release_();
  }
}

void MatterGenericSwitchMapping::on_press_() {
  if (this->pressed_)
    return;
  this->pressed_ = true;
  this->long_press_sent_ = false;

  // A new press within the window continues the multi-press sequence.
  this->cancel_timeout("multi_press");
  if (!this->multi_press_)
    this->press_count_ = 0;
  if (this->press_count_ < UINT8_MAX)
    this->press_count_++;
  if (this->press_count_ > this->multi_press_max_)
    this->multi_press_overflow_ = true;

  // TC_SWTCH 2.5 (momentary): every press is an InitialPress, and presses
  // 2..MultiPressMax are followed by a MultiPressOngoing. TC_SWTCH 2.6 (action
  // switch): only the first press of a sequence is an InitialPress and there is
  // no MultiPressOngoing. More than MultiPressMax presses abort the sequence:
  // no MultiPressOngoing, and the sequence completes with 0 presses.
  const bool first_press = this->press_count_ == 1;
  if (first_press || !this->action_switch_) {
    send_switch_event(this->endpoint_id(), MatterSwitchEvent::INITIAL_PRESS);
    if (!first_press && this->multi_press_ && !this->multi_press_overflow_)
      send_switch_event(this->endpoint_id(),
                        MatterSwitchEvent::MULTI_PRESS_ONGOING, 1,
                        this->press_count_);
  } else {
    // No event moves CurrentPosition for this press.
    set_switch_position(this->endpoint_id(), 1);
  }

  // A press that continues a multi-press sequence is never a long press.
  if (this->long_press_ && this->press_count_ == 1) {
    this->set_timeout("long_press", this->long_press_ms_, [this]() {
      this->long_press_sent_ = true;
      this->press_count_ = 0;
      this->multi_press_overflow_ = false;
      send_switch_event(this->endpoint_id(), MatterSwitchEvent::LONG_PRESS);
    });
  }
}

void MatterGenericSwitchMapping::on_release_() {
  if (!this->pressed_)
    return;
  this->pressed_ = false;
  this->cancel_timeout("long_press");

  if (this->long_press_sent_) {
    this->long_press_sent_ = false;
    send_switch_event(this->endpoint_id(), MatterSwitchEvent::LONG_RELEASE);
    return;
  }

  if (this->release_)
    send_switch_event(this->endpoint_id(), MatterSwitchEvent::SHORT_RELEASE);
  else
    set_switch_position(this->endpoint_id(), 0);

  if (this->multi_press_) {
    this->set_timeout("multi_press", this->multi_press_window_ms_,
                      [this]() { this->finish_multi_press_(); });
  } else {
    this->press_count_ = 0;
  }
}

void MatterGenericSwitchMapping::finish_multi_press_() {
  uint8_t count = this->multi_press_overflow_ ? 0 : this->press_count_;
  this->press_count_ = 0;
  this->multi_press_overflow_ = false;
  send_switch_event(this->endpoint_id(),
                    MatterSwitchEvent::MULTI_PRESS_COMPLETE, 1, count);
}

#endif // USE_BINARY_SENSOR

} // namespace esphome::matter

#endif // USE_MATTER
