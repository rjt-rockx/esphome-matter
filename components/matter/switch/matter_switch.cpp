#include "matter_switch.h"
#ifdef USE_MATTER

#include "esphome/core/log.h"
#include <esp_matter.h>
#include <app-common/zap-generated/cluster-objects.h>

namespace esphome::matter {

static const char *const TAG = "matter.switch";

void MatterSwitch::dump_config() {
  LOG_SWITCH("", "Matter Switch", this);
  ESP_LOGCONFIG(TAG, "  Endpoint: %u", this->endpoint_id_);
  ESP_LOGCONFIG(TAG, "  Device type: 0x%04X", this->device_type_);
}

void MatterSwitch::register_with_node(::esp_matter::node_t *node) {
  using namespace ::esp_matter;
  endpoint_t *ep = nullptr;
  switch (this->device_type_) {
    case 0x010A: {  // on_off_plug_in_unit
      endpoint::on_off_plug_in_unit::config_t cfg;
      cfg.on_off.on_off = false;
      ep = endpoint::on_off_plug_in_unit::create(node, &cfg, ENDPOINT_FLAG_NONE, this);
      break;
    }
    default:
      ESP_LOGE(TAG, "device_type 0x%04X not yet supported by matter switch", this->device_type_);
      return;
  }
  if (ep == nullptr) {
    ESP_LOGE(TAG, "failed to create matter switch endpoint");
    return;
  }
  this->endpoint_id_ = endpoint::get_id(ep);
  ESP_LOGI(TAG, "registered switch on endpoint %u", this->endpoint_id_);
}

void MatterSwitch::write_state(bool state) {
  // Local toggle: publish state to Matter and notify ESPHome subscribers.
  this->publish_state(state);

  if (this->endpoint_id_ == 0)
    return;

  esp_matter_attr_val_t val = esp_matter_bool(state);
  ::esp_matter::lock::chip_stack_lock(portMAX_DELAY);
  ::esp_matter::attribute::update(this->endpoint_id_,
                                  chip::app::Clusters::OnOff::Id,
                                  chip::app::Clusters::OnOff::Attributes::OnOff::Id, &val);
  ::esp_matter::lock::chip_stack_unlock();
}

void MatterSwitch::on_matter_attribute_update(uint32_t cluster_id, uint32_t attribute_id,
                                              esp_matter_attr_val_t *val) {
  if (cluster_id != chip::app::Clusters::OnOff::Id ||
      attribute_id != chip::app::Clusters::OnOff::Attributes::OnOff::Id) {
    return;
  }
  if (val == nullptr || val->type != ESP_MATTER_VAL_TYPE_BOOLEAN)
    return;
  // Reflect the inbound state but do NOT re-publish back into Matter or we'd loop.
  this->publish_state(val->val.b);
}

}  // namespace esphome::matter

#endif
