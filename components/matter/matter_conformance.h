#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include <cstdint>
#include <vector>

#include <esp_matter.h>

namespace esphome::matter {

// Endpoint composition details that make the node spec-conformant: explicit
// parent endpoints (Descriptor PartsList), Descriptor TagList semantic tags,
// FixedLabel labels and the Basic Information ConfigurationVersion.
// Filled by generated code before Matter starts; see docs/composition.md.
class MatterConformance {
public:
  // Strings must stay valid for the lifetime of the firmware (string literals).
  void register_parent(uint16_t endpoint_id, uint16_t parent_id);
  void register_label(uint16_t endpoint_id, const char *label);
  void register_tag(uint16_t endpoint_id, uint8_t namespace_id, uint8_t tag,
                    const char *label);
  void set_structure_hash(uint32_t hash) { this->structure_hash_ = hash; }

  // Applies parents and tags once every endpoint has been created.
  bool apply(esp_matter::node_t *node) const;

  // Persists the structure hash and derives the ConfigurationVersion. Must run
  // before esp_matter::start().
  void init_configuration_version() const;
  // Installs the DeviceInfoProvider that serves the FixedLabel labels. Must run
  // before esp_matter::start().
  void install_labels() const;

private:
  static constexpr size_t MAX_TAGS = 3; // ESP_MATTER_MAX_SEMANTIC_TAG_COUNT

  struct Tag {
    uint8_t namespace_id;
    uint8_t tag;
    const char *label; // "" when absent
  };
  struct Entry {
    uint16_t endpoint_id;
    uint16_t parent_id = 0;
    const char *label = nullptr;
    std::vector<Tag> tags;
  };

  Entry &entry_(uint16_t endpoint_id);

  std::vector<Entry> entries_;
  uint32_t structure_hash_{0};
};

} // namespace esphome::matter

#endif // USE_MATTER
