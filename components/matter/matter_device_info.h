#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include <string>

#include <esp_matter.h>

namespace esphome::matter {

// Basic Information (cluster 0x0028) values configured from YAML. Empty strings mean "use the default":
//   serial_number -> derived from the factory MAC address
//   unique_id     -> random value generated and persisted by the CHIP ConfigurationManager
// node_label (served via a persistence-provider default, see .cpp), hardware_version_string and software_version_string are always filled in by codegen.
struct DeviceInfoConfig {
  std::string node_label;
  std::string serial_number;
  std::string hardware_version_string;
  std::string software_version_string;  // informational only: the value is baked in via CONFIG_APP_PROJECT_VER
  std::string unique_id;
  uint16_t max_fabrics{5};  // informational only: the value is baked in via CONFIG_MAX_FABRICS

  void set_node_label(const std::string &v) { this->node_label = v; }
  void set_serial_number(const std::string &v) { this->serial_number = v; }
  void set_hardware_version_string(const std::string &v) { this->hardware_version_string = v; }
  void set_software_version_string(const std::string &v) { this->software_version_string = v; }
  void set_unique_id(const std::string &v) { this->unique_id = v; }
  void set_max_fabrics(uint16_t v) { this->max_fabrics = v; }
};

// Fill the NodeLabel / UniqueID fields of the root node config. Call before node::create().
void device_info_fill_root_config(const DeviceInfoConfig &cfg, ::esp_matter::node::config_t &node_config);

// Register the DeviceInstanceInfoProvider that reports SerialNumber and HardwareVersionString.
// Must be called before esp_matter::start().
void device_info_install_provider(const DeviceInfoConfig &cfg);

// Persist a user supplied UniqueID. Call after esp_matter::start().
void device_info_apply_post_start(const DeviceInfoConfig &cfg);

void device_info_dump_config(const DeviceInfoConfig &cfg);

}  // namespace esphome::matter

#endif
