#include "matter_device_info.h"
#ifdef USE_MATTER

#include "esphome/core/log.h"

#include <cstdio>
#include <cstring>
#include <string>

#include <esp_mac.h>
#include <sdkconfig.h>

#include <esp_matter_providers.h>
#include <app/persistence/AttributePersistenceProviderInstance.h>
#include <app/persistence/DefaultAttributePersistenceProvider.h>
#include <app/server/Server.h>
#include <clusters/BasicInformation/Ids.h>
#include <platform/CHIPDeviceLayer.h>
#include <platform/ConfigurationManager.h>
#include <platform/ESP32/ConfigurationManagerImpl.h>
#include <platform/ESP32/ESP32Config.h>
#include <platform/internal/GenericDeviceInstanceInfoProvider.h>

namespace esphome::matter {

static const char *const TAG = "matter.device_info";

namespace {

#if CONFIG_CUSTOM_DEVICE_INSTANCE_INFO_PROVIDER

// Derives from the stock ESP32 GenericDeviceInstanceInfoProvider (the one ConfigurationManager installs by default)
// and only replaces the two values that otherwise fall back to build-time placeholders: the "TEST_VERSION" hardware
// version string and the CONFIG_USE_TEST_SERIAL_NUMBER serial.
class EsphomeDeviceInstanceInfoProvider
    : public chip::DeviceLayer::Internal::GenericDeviceInstanceInfoProvider<chip::DeviceLayer::Internal::ESP32Config> {
 public:
  explicit EsphomeDeviceInstanceInfoProvider(const DeviceInfoConfig &cfg)
      : GenericDeviceInstanceInfoProvider(chip::DeviceLayer::ConfigurationManagerImpl::GetDefaultInstance()),
        cfg_(cfg) {
    if (!cfg.serial_number.empty()) {
      snprintf(this->serial_, sizeof(this->serial_), "%s", cfg.serial_number.c_str());
    } else {
      uint8_t mac[6] = {0};
      esp_read_mac(mac, ESP_MAC_WIFI_STA);
      snprintf(this->serial_, sizeof(this->serial_), "%02X%02X%02X%02X%02X%02X", mac[0], mac[1], mac[2], mac[3], mac[4],
               mac[5]);
    }
  }

  CHIP_ERROR GetSerialNumber(char *buf, size_t buf_size) override {
    size_t len = strlen(this->serial_);
    VerifyOrReturnError(len < buf_size, CHIP_ERROR_BUFFER_TOO_SMALL);
    memcpy(buf, this->serial_, len + 1);
    return CHIP_NO_ERROR;
  }

  CHIP_ERROR GetHardwareVersionString(char *buf, size_t buf_size) override {
    if (this->cfg_.hardware_version_string.empty())
      return GenericDeviceInstanceInfoProvider::GetHardwareVersionString(buf, buf_size);
    size_t len = this->cfg_.hardware_version_string.size();
    VerifyOrReturnError(len < buf_size, CHIP_ERROR_BUFFER_TOO_SMALL);
    memcpy(buf, this->cfg_.hardware_version_string.c_str(), len + 1);
    return CHIP_NO_ERROR;
  }

 protected:
  const DeviceInfoConfig &cfg_;
  char serial_[chip::DeviceLayer::ConfigurationManager::kMaxSerialNumberLength + 1]{};
};

EsphomeDeviceInstanceInfoProvider *s_provider = nullptr;

#endif  // CONFIG_CUSTOM_DEVICE_INSTANCE_INFO_PROVIDER

// esp-matter 1.6 implements Basic Information with CHIP's code-driven BasicInformationCluster. It keeps NodeLabel only
// in the AttributePersistenceProvider (key = endpoint 0 / cluster 0x28 / attribute 0x05, a length prefixed string) and
// ignores the esp-matter attribute store, so basic_information.node_label in the node config never reaches a controller.
// This provider wraps the stock persistence provider and answers reads of a NodeLabel that has never been written with
// the configured default. Writes (from a controller) go straight to storage and win from then on; a factory reset
// erases them and the default comes back. Nothing is ever written on the controller's behalf.
class NodeLabelDefaultPersistence : public chip::app::AttributePersistenceProvider {
 public:
  void set_default(const std::string &label) { this->default_ = label; }

  CHIP_ERROR WriteValue(const chip::app::ConcreteAttributePath &path, const chip::ByteSpan &value) override {
    ReturnErrorOnFailure(this->ensure_init_());
    return this->inner_.WriteValue(path, value);
  }

  CHIP_ERROR ReadValue(const chip::app::ConcreteAttributePath &path, chip::MutableByteSpan &value) override {
    ReturnErrorOnFailure(this->ensure_init_());
    CHIP_ERROR err = this->inner_.ReadValue(path, value);
    // Any failure other than a too-small buffer means "nothing usable stored yet" (the stock KVS reports
    // CHIP_ERROR_PERSISTED_STORAGE_VALUE_NOT_FOUND), so the default is served.
    if (err == CHIP_NO_ERROR || err == CHIP_ERROR_BUFFER_TOO_SMALL || !this->is_node_label_(path) ||
        this->default_.empty())
      return err;
    // Pascal string as stored by CHIP: one length byte followed by the characters.
    size_t len = this->default_.size();
    VerifyOrReturnError(value.size() >= len + 1, CHIP_ERROR_BUFFER_TOO_SMALL);
    value.data()[0] = static_cast<uint8_t>(len);
    memcpy(value.data() + 1, this->default_.data(), len);
    value.reduce_size(len + 1);
    return CHIP_NO_ERROR;
  }

 protected:
  static bool is_node_label_(const chip::app::ConcreteAttributePath &path) {
    return path.mEndpointId == chip::kRootEndpointId &&
           path.mClusterId == chip::app::Clusters::BasicInformation::Id &&
           path.mAttributeId == chip::app::Clusters::BasicInformation::Attributes::NodeLabel::Id;
  }

  // The persistent storage only exists once the CHIP server is initialised, which is before the first read/write.
  CHIP_ERROR ensure_init_() {
    if (this->inited_)
      return CHIP_NO_ERROR;
    ReturnErrorOnFailure(this->inner_.Init(&chip::Server::GetInstance().GetPersistentStorage()));
    this->inited_ = true;
    return CHIP_NO_ERROR;
  }

  chip::app::DefaultAttributePersistenceProvider inner_;
  std::string default_;
  bool inited_{false};
};

NodeLabelDefaultPersistence s_node_label_persistence;

}  // namespace

void device_info_fill_root_config(const DeviceInfoConfig &cfg, ::esp_matter::node::config_t &node_config) {
  auto &bi = node_config.root_node.basic_information;
  // Only seeds the esp-matter attribute store (and creates the attribute); see NodeLabelDefaultPersistence for the
  // value controllers actually read.
  snprintf(bi.node_label, sizeof(bi.node_label), "%s", cfg.node_label.c_str());
  if (!cfg.unique_id.empty())
    snprintf(bi.unique_id, sizeof(bi.unique_id), "%s", cfg.unique_id.c_str());
}

void device_info_install_provider(const DeviceInfoConfig &cfg) {
  // Must be installed before esp_matter::start(): the data model provider only installs its own persistence provider
  // when none is set, and BasicInformation loads NodeLabel from it while the server starts.
  if (chip::app::GetAttributePersistenceProvider() == nullptr) {
    s_node_label_persistence.set_default(cfg.node_label.substr(0, 32));
    chip::app::SetAttributePersistenceProvider(&s_node_label_persistence);
  } else {
    ESP_LOGW(TAG, "an attribute persistence provider is already installed; node_label default is not applied");
  }
#if CONFIG_CUSTOM_DEVICE_INSTANCE_INFO_PROVIDER
  // The provider is only *registered* by esp-matter inside esp_matter::start() -> setup_providers(), after the CHIP
  // stack (and therefore ConfigurationManagerImpl) is initialised. Constructing it here only stores references.
  if (s_provider == nullptr)
    s_provider = new EsphomeDeviceInstanceInfoProvider(cfg);  // NOLINT: lives for the lifetime of the firmware
  ::esp_matter::set_custom_device_instance_info_provider(s_provider);
#else
  ESP_LOGW(TAG, "CONFIG_CUSTOM_DEVICE_INSTANCE_INFO_PROVIDER is off; serial/hardware version overrides are ignored");
#endif
}

void device_info_apply_post_start(const DeviceInfoConfig &cfg) {
  if (cfg.unique_id.empty())
    return;
  // The generated UniqueID lives in the CHIP config store; overwrite it so the attribute is stable and user chosen.
  chip::DeviceLayer::PlatformMgr().LockChipStack();
  CHIP_ERROR err = chip::DeviceLayer::ConfigurationMgr().StoreUniqueId(cfg.unique_id.c_str(), cfg.unique_id.size());
  chip::DeviceLayer::PlatformMgr().UnlockChipStack();
  if (err != CHIP_NO_ERROR)
    ESP_LOGW(TAG, "failed to store unique_id: %" CHIP_ERROR_FORMAT, err.Format());
}

void device_info_dump_config(const DeviceInfoConfig &cfg) {
  ESP_LOGCONFIG(TAG,
                "  Node Label: %s\n"
                "  Serial Number: %s\n"
                "  Hardware Version String: %s\n"
                "  Software Version String: %s\n"
                "  Max Fabrics: %u",
                cfg.node_label.c_str(), cfg.serial_number.empty() ? "(from MAC)" : cfg.serial_number.c_str(),
                cfg.hardware_version_string.c_str(), cfg.software_version_string.c_str(), cfg.max_fabrics);
}

}  // namespace esphome::matter

#endif
