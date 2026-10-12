#include "matter_device_info.h"
#ifdef USE_MATTER

#include "esphome/core/log.h"

#include <cstdio>
#include <cstring>

#include <esp_mac.h>
#include <sdkconfig.h>

#include <esp_matter_providers.h>
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

}  // namespace

void device_info_fill_root_config(const DeviceInfoConfig &cfg, ::esp_matter::node::config_t &node_config) {
  auto &bi = node_config.root_node.basic_information;
  snprintf(bi.node_label, sizeof(bi.node_label), "%s", cfg.node_label.c_str());
  if (!cfg.unique_id.empty())
    snprintf(bi.unique_id, sizeof(bi.unique_id), "%s", cfg.unique_id.c_str());
}

void device_info_install_provider(const DeviceInfoConfig &cfg) {
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
