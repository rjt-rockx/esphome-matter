#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "esphome/core/log.h"
#include "matter_conformance.h"

#include <cstring>
#include <limits>

#include <app-common/zap-generated/ids/Clusters.h>
#include <esp_matter_cluster.h>
#include <esp_matter_providers.h>
#include <nvs.h>
#include <sdkconfig.h>

#include <app/data-model-provider/Provider.h>
#include <lib/support/CHIPMem.h>
#include <lib/support/Span.h>
#include <platform/DeviceInfoProvider.h>

static const char *const TAG = "matter.conformance";

namespace esphome::matter {

namespace {

constexpr const char *NVS_NAMESPACE = "esphome-matter";
constexpr const char *NVS_KEY_HASH = "cfg-hash";
constexpr const char *NVS_KEY_VERSION = "cfg-ver";
constexpr const char *FIXED_LABEL_NAME = "name";

// ConfigurationVersion served by Basic Information, see
// esphome_matter_configuration_version() below.
uint32_t s_configuration_version = 1; // NOLINT

} // namespace

MatterConformance::Entry &MatterConformance::entry_(uint16_t endpoint_id) {
  for (auto &entry : this->entries_) {
    if (entry.endpoint_id == endpoint_id)
      return entry;
  }
  this->entries_.push_back({endpoint_id});
  return this->entries_.back();
}

void MatterConformance::register_parent(uint16_t endpoint_id,
                                        uint16_t parent_id) {
  this->entry_(endpoint_id).parent_id = parent_id;
}

void MatterConformance::register_label(uint16_t endpoint_id,
                                       const char *label) {
  this->entry_(endpoint_id).label = label;
}

void MatterConformance::register_tag(uint16_t endpoint_id, uint8_t namespace_id,
                                     uint8_t tag, const char *label) {
  this->entry_(endpoint_id).tags.push_back({namespace_id, tag, label});
}

bool MatterConformance::apply(esp_matter::node_t *node) const {
  for (const auto &entry : this->entries_) {
    auto *endpoint = esp_matter::endpoint::get(node, entry.endpoint_id);
    if (endpoint == nullptr)
      continue;

    // esp_matter::endpoint::resume() leaves the parent unset (0 by accident);
    // always set it explicitly so PartsList is correct for nested endpoints.
    auto *parent = esp_matter::endpoint::get(node, entry.parent_id);
    if (parent == nullptr) {
      ESP_LOGE(TAG, "Endpoint %u has unknown parent endpoint %u",
               entry.endpoint_id, entry.parent_id);
      return false;
    }
    if (esp_matter::endpoint::set_parent_endpoint(endpoint, parent) != ESP_OK) {
      ESP_LOGE(TAG, "Failed to set parent %u of endpoint %u", entry.parent_id,
               entry.endpoint_id);
      return false;
    }

    if (entry.tags.empty())
      continue;
    if (entry.tags.size() > MAX_TAGS) {
      ESP_LOGE(TAG, "Endpoint %u has more than %u semantic tags",
               entry.endpoint_id, static_cast<unsigned>(MAX_TAGS));
      return false;
    }
    chip::app::DataModel::Provider::SemanticTag tags[MAX_TAGS];
    size_t count = 0;
    for (const auto &tag : entry.tags) {
      auto &out = tags[count++];
      out.namespaceID = tag.namespace_id;
      out.tag = tag.tag;
      if (tag.label != nullptr && tag.label[0] != '\0') {
        out.label.SetValue(chip::app::DataModel::MakeNullable(
            chip::CharSpan::fromCharString(tag.label)));
      }
    }
    // The label buffers are string literals and outlive the endpoint.
    if (esp_matter::endpoint::set_semantic_tags(endpoint, tags, count) !=
        ESP_OK) {
      ESP_LOGE(TAG, "Failed to set semantic tags of endpoint %u",
               entry.endpoint_id);
      return false;
    }
    auto *descriptor = esp_matter::cluster::get(
        endpoint, chip::app::Clusters::Descriptor::Id);
    if (descriptor == nullptr ||
        esp_matter::cluster::descriptor::feature::tag_list::add(descriptor) !=
            ESP_OK) {
      ESP_LOGE(TAG, "Failed to enable Descriptor TagList on endpoint %u",
               entry.endpoint_id);
      return false;
    }
    ESP_LOGD(TAG, "Endpoint %u: parent %u, %u semantic tag(s)",
             entry.endpoint_id, entry.parent_id,
             static_cast<unsigned>(count));
  }
  return true;
}

// ---------------------------------------------------------------------------
// ConfigurationVersion
//
// The ESP32 ConfigurationManager neither stores nor increments the Basic
// Information ConfigurationVersion: it serves the compile time constant
// CHIP_DEVICE_CONFIG_DEVICE_CONFIGURATION_VERSION and rejects
// StoreConfigurationVersion. matter_chip_config.h redirects that constant to
// esphome_matter_configuration_version() so the value can follow the endpoint
// composition.
//
// The codegen computes a hash of the composition. At boot the last seen hash and
// version are read from NVS; a different hash increments the version. Devices
// that ran firmware without this feature (no record) and report 1 move to 2,
// because their composition may have changed since a controller last read it.
// ---------------------------------------------------------------------------
void MatterConformance::init_configuration_version() const {
  nvs_handle_t handle;
  esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "Cannot open NVS: %s; ConfigurationVersion stays at %u",
             esp_err_to_name(err),
             static_cast<unsigned>(s_configuration_version));
    return;
  }
  uint32_t stored_hash = 0;
  uint32_t version = 0;
  bool have_record =
      nvs_get_u32(handle, NVS_KEY_HASH, &stored_hash) == ESP_OK &&
      nvs_get_u32(handle, NVS_KEY_VERSION, &version) == ESP_OK && version >= 1;

  bool changed = false;
  if (!have_record) {
    version = 2;
    changed = true;
  } else if (stored_hash != this->structure_hash_) {
    version = version == std::numeric_limits<uint32_t>::max() ? 1 : version + 1;
    changed = true;
  }
  if (changed) {
    if (nvs_set_u32(handle, NVS_KEY_HASH, this->structure_hash_) != ESP_OK ||
        nvs_set_u32(handle, NVS_KEY_VERSION, version) != ESP_OK ||
        nvs_commit(handle) != ESP_OK) {
      ESP_LOGW(TAG, "Failed to persist the ConfigurationVersion");
    }
    ESP_LOGI(TAG,
             "Endpoint composition %s (hash 0x%08X): ConfigurationVersion %u",
             have_record ? "changed" : "first seen",
             static_cast<unsigned>(this->structure_hash_),
             static_cast<unsigned>(version));
  } else {
    ESP_LOGD(TAG, "Endpoint composition unchanged: ConfigurationVersion %u",
             static_cast<unsigned>(version));
  }
  nvs_close(handle);
  s_configuration_version = version;
}

// ---------------------------------------------------------------------------
// FixedLabel
//
// The FixedLabel cluster reads its LabelList from the DeviceInfoProvider, which
// esp-matter only registers when configured. This provider serves one
// {"name": <label>} entry per labelled endpoint and nothing else.
// ---------------------------------------------------------------------------
#ifdef CONFIG_CUSTOM_DEVICE_INFO_PROVIDER

namespace {

struct FixedLabelEntry {
  uint16_t endpoint_id;
  const char *label;
};
std::vector<FixedLabelEntry> s_fixed_labels; // NOLINT

class EmptyUserLabelIterator
    : public chip::DeviceLayer::DeviceInfoProvider::UserLabelIterator {
public:
  size_t Count() override { return 0; }
  bool Next(chip::DeviceLayer::DeviceInfoProvider::UserLabelType &) override { return false; }
  void Release() override { chip::Platform::Delete(this); }
};

class EmptyLocalesIterator
    : public chip::DeviceLayer::DeviceInfoProvider::SupportedLocalesIterator {
public:
  size_t Count() override { return 0; }
  bool Next(chip::CharSpan &) override { return false; }
  void Release() override { chip::Platform::Delete(this); }
};

class EmptyCalendarTypesIterator
    : public chip::DeviceLayer::DeviceInfoProvider::
          SupportedCalendarTypesIterator {
public:
  size_t Count() override { return 0; }
  bool Next(chip::DeviceLayer::DeviceInfoProvider::CalendarType &) override { return false; }
  void Release() override { chip::Platform::Delete(this); }
};

class FixedLabelIteratorImpl
    : public chip::DeviceLayer::DeviceInfoProvider::FixedLabelIterator {
public:
  explicit FixedLabelIteratorImpl(chip::EndpointId endpoint)
      : endpoint_(endpoint) {}
  size_t Count() override {
    size_t count = 0;
    for (const auto &entry : s_fixed_labels) {
      if (entry.endpoint_id == this->endpoint_)
        count++;
    }
    return count;
  }
  bool Next(chip::DeviceLayer::DeviceInfoProvider::FixedLabelType &output) override {
    while (this->index_ < s_fixed_labels.size()) {
      const auto &entry = s_fixed_labels[this->index_++];
      if (entry.endpoint_id != this->endpoint_)
        continue;
      output.label = chip::CharSpan::fromCharString(FIXED_LABEL_NAME);
      output.value = chip::CharSpan::fromCharString(entry.label);
      return true;
    }
    return false;
  }
  void Release() override { chip::Platform::Delete(this); }

private:
  chip::EndpointId endpoint_;
  size_t index_{0};
};

// User labels (UserLabel cluster) are not supported by this component.
class EsphomeDeviceInfoProvider : public chip::DeviceLayer::DeviceInfoProvider {
public:
  FixedLabelIterator *IterateFixedLabel(chip::EndpointId endpoint) override {
    return chip::Platform::New<FixedLabelIteratorImpl>(endpoint);
  }
  UserLabelIterator *IterateUserLabel(chip::EndpointId) override {
    return chip::Platform::New<EmptyUserLabelIterator>();
  }
  SupportedLocalesIterator *IterateSupportedLocales() override {
    return chip::Platform::New<EmptyLocalesIterator>();
  }
  SupportedCalendarTypesIterator *IterateSupportedCalendarTypes() override {
    return chip::Platform::New<EmptyCalendarTypesIterator>();
  }

protected:
  CHIP_ERROR SetUserLabelAt(chip::EndpointId, size_t,
                            const UserLabelType &) override {
    return CHIP_ERROR_UNSUPPORTED_CHIP_FEATURE;
  }
  CHIP_ERROR DeleteUserLabelAt(chip::EndpointId, size_t) override {
    return CHIP_ERROR_UNSUPPORTED_CHIP_FEATURE;
  }
  CHIP_ERROR SetUserLabelLength(chip::EndpointId, size_t) override {
    return CHIP_ERROR_UNSUPPORTED_CHIP_FEATURE;
  }
  CHIP_ERROR GetUserLabelLength(chip::EndpointId, size_t &val) override {
    val = 0;
    return CHIP_NO_ERROR;
  }
};

EsphomeDeviceInfoProvider *s_info_provider = nullptr; // NOLINT

} // namespace

void MatterConformance::install_labels() const {
  s_fixed_labels.clear();
  for (const auto &entry : this->entries_) {
    if (entry.label != nullptr)
      s_fixed_labels.push_back({entry.endpoint_id, entry.label});
  }
  if (s_fixed_labels.empty())
    return;
  if (s_info_provider == nullptr)
    s_info_provider = new EsphomeDeviceInfoProvider(); // NOLINT: firmware lifetime
  esp_matter::set_custom_device_info_provider(s_info_provider);
}

#else

void MatterConformance::install_labels() const {
  for (const auto &entry : this->entries_) {
    if (entry.label != nullptr) {
      ESP_LOGW(TAG, "CONFIG_CUSTOM_DEVICE_INFO_PROVIDER is off; endpoint "
                    "labels are ignored");
      return;
    }
  }
}

#endif // CONFIG_CUSTOM_DEVICE_INFO_PROVIDER

} // namespace esphome::matter

// Referenced through CHIP_DEVICE_CONFIG_DEVICE_CONFIGURATION_VERSION in
// matter_chip_config.h.
extern "C" uint32_t esphome_matter_configuration_version(void) {
  return esphome::matter::s_configuration_version;
}

#endif // USE_MATTER
