#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "esphome/components/switch/switch.h"
#include "esphome/core/component.h"
#include "../matter.h"
#include "../matter_entity.h"

namespace esphome::matter {

class MatterSwitch : public switch_::Switch, public Component, public MatterEntity {
 public:
  void setup() override {}
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::DATA; }

  void set_parent(MatterComponent *parent) { this->parent_ = parent; }
  void set_device_type(uint16_t device_type) { this->device_type_ = device_type; }
  void set_requested_endpoint_id(uint16_t ep) { this->requested_endpoint_id_ = ep; }

  void register_with_node(::esp_matter::node_t *node) override;
  void on_matter_attribute_update(uint32_t cluster_id, uint32_t attribute_id,
                                  esp_matter_attr_val_t *val) override;

 protected:
  void write_state(bool state) override;

  MatterComponent *parent_{nullptr};
  uint16_t device_type_{0x010A};   // on_off_plug_in_unit
  uint16_t requested_endpoint_id_{0};
};

}  // namespace esphome::matter

#endif
