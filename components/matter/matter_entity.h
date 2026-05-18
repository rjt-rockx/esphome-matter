#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include <cstdint>
#include <esp_matter.h>

namespace esphome::matter {

// Base class for per-entity Matter glue. Each opted-in ESPHome entity owns a
// MatterEntity subclass that knows how to build its endpoint and translate
// entity state both ways.
class MatterEntity {
 public:
  virtual ~MatterEntity() = default;

  // Called during MatterComponent::setup() once the node is created.
  // Subclasses should create their endpoint and remember its id.
  virtual void register_with_node(::esp_matter::node_t *node) = 0;

  // Inbound: Matter wrote an attribute on this entity's endpoint.
  virtual void on_matter_attribute_update(uint32_t cluster_id, uint32_t attribute_id,
                                          esp_matter_attr_val_t *val) = 0;

  uint16_t endpoint_id() const { return this->endpoint_id_; }

 protected:
  uint16_t endpoint_id_{0};
};

}  // namespace esphome::matter

#endif
