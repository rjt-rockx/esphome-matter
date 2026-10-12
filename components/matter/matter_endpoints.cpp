#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "esphome/core/log.h"
#include "matter_actions.h"
#include "matter_component.h"

#include <algorithm>
#include <esp_matter_cluster.h>

static const char *const TAG = "matter";

namespace esphome::matter {

namespace {

// Mirrors the start of esp-matter's private node layout to advance its endpoint
// allocator watermark.
struct EspMatterNodeHeader {
  void *endpoint_list;
  uint16_t min_unused_endpoint_id;
};

} // namespace

void MatterComponent::register_endpoint(uint16_t endpoint_id,
                                        MatterEndpointBuildFn build_fn) {
  for (const auto &registration : this->endpoint_registrations_) {
    if (registration.endpoint_id == endpoint_id)
      return;
  }
  this->endpoint_registrations_.push_back({endpoint_id, build_fn});
}

bool MatterEndpointMappingBase::has_server_cluster(uint32_t cluster_id) const {
  auto *endpoint = esp_matter::endpoint::get(this->endpoint_id());
  if (endpoint == nullptr)
    return false;
  auto *cluster = esp_matter::cluster::get(endpoint, cluster_id);
  return cluster != nullptr && (esp_matter::cluster::get_flags(cluster) &
                                esp_matter::CLUSTER_FLAG_SERVER);
}

namespace {
const char *last_failed_step = nullptr;
} // namespace

bool record_build_failure(const char *step) {
  last_failed_step = step;
  return false;
}

const char *last_build_failure() { return last_failed_step; }

bool MatterComponent::create_endpoints_(esp_matter::node_t *node) {
  if (!this->endpoint_registrations_.empty()) {
    // esp-matter only resumes endpoint IDs below its private
    // min_unused_endpoint_id. ESPHome creates all static endpoints before
    // esp_matter::start(), so advance the single node's allocator watermark
    // once before resuming them.
    auto max_registration =
        std::max_element(this->endpoint_registrations_.begin(),
                         this->endpoint_registrations_.end(),
                         [](const auto &lhs, const auto &rhs) {
                           return lhs.endpoint_id < rhs.endpoint_id;
                         });
    uint16_t max_endpoint_id = max_registration->endpoint_id;
    auto *node_header = reinterpret_cast<EspMatterNodeHeader *>(node);
    if (node_header->min_unused_endpoint_id <= max_endpoint_id)
      node_header->min_unused_endpoint_id = max_endpoint_id + 1;
  }

  // Create endpoints
  for (const auto &registration : this->endpoint_registrations_) {
    uint16_t endpoint_id = registration.endpoint_id;
    esp_matter::endpoint_t *endpoint = nullptr;

    if (endpoint_id == 0) {
      // The root node endpoint was created by esp_matter::node::create() with
      // its mandatory clusters (including Descriptor). The build function only
      // adds utility device types and clusters (Power Source, Electrical Sensor).
      endpoint = esp_matter::endpoint::get(node, endpoint_id);
      if (endpoint == nullptr) {
        ESP_LOGE(TAG, "Root node endpoint is missing");
        return MATTER_BUILD_FAIL("root node endpoint is missing");
      }
    } else {
      if (esp_matter::endpoint::get(node, endpoint_id) != nullptr) {
        ESP_LOGE(TAG, "Matter endpoint id %u is already in use", endpoint_id);
        return MATTER_BUILD_FAIL("endpoint id already in use");
      }

      endpoint = esp_matter::endpoint::resume(
          node, esp_matter::ENDPOINT_FLAG_NONE, endpoint_id, nullptr);
      if (endpoint == nullptr) {
        ESP_LOGE(TAG, "Failed to create endpoint %u", endpoint_id);
        return MATTER_BUILD_FAIL("esp_matter::endpoint::resume failed");
      }

      // Create "empty" descriptor cluster. Connectedhomip fills this internally.
      esp_matter::cluster::descriptor::config_t descriptor_config;
      esp_matter::cluster_t *descriptor_cluster =
          esp_matter::cluster::descriptor::create(
              endpoint, &descriptor_config, esp_matter::CLUSTER_FLAG_SERVER);
      if (descriptor_cluster == nullptr) {
        ESP_LOGE(TAG, "Failed to create descriptor cluster for endpoint %u",
                 endpoint_id);
        return MATTER_BUILD_FAIL("descriptor cluster create failed");
      }
    }

    last_failed_step = nullptr;
    if (registration.build_fn == nullptr || !registration.build_fn(endpoint)) {
      // The generated callback names the failing step (device type, cluster,
      // feature or attribute); see MATTER_BUILD_FAIL.
      ESP_LOGE(TAG, "Failed to build endpoint %u. Failing step: %s",
               endpoint_id,
               last_failed_step != nullptr ? last_failed_step : "(unknown)");
      if (last_failed_step == nullptr)
        MATTER_BUILD_FAIL("endpoint build callback failed");
      return false;
    }

    ESP_LOGD(TAG, "Endpoint created: id=%u", endpoint_id);
  }

  if (!this->conformance_.apply(node))
    return MATTER_BUILD_FAIL("conformance check failed");

  register_client_request_callbacks();

  return true;
}

// Wires ESPHome entities to Matter attributes. Must run after
// esp_matter::start().
void MatterComponent::initialize_endpoint_mappings_() {
  for (auto *mapping : this->mappings_) {
    mapping->initialize();
  }
}

} // namespace esphome::matter

#endif // USE_MATTER
