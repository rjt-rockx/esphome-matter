#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "esphome/components/network/util.h"
#ifdef USE_OPENTHREAD
#include "esphome/components/openthread/openthread.h"
#endif // USE_OPENTHREAD
#include "esphome/core/application.h"
#include "esphome/core/log.h"
#include "matter_commissioning.h"
#include "matter_component.h"
#include "matter_icd.h"
#include "matter_identify.h"
#include "matter_network.h"
#include "matter_plan.h"

#include <algorithm>
#include <cinttypes>
#include <cstdio>
#include <cstring>
#include <esp_matter_client.h>
#include <nvs.h>
#ifdef USE_MATTER_ICD_LIGHT_SLEEP
#include <esp_pm.h>
#endif
#include <string>

#include <app/server/Server.h>
#include <crypto/CHIPCryptoPAL.h>
#if CHIP_DEVICE_CONFIG_ENABLE_THREAD
#include <platform/ESP32/ThreadStackManagerImpl.h>
#endif // CHIP_DEVICE_CONFIG_ENABLE_THREAD
#ifdef USE_OPENTHREAD
#include <openthread/ip6.h>
#endif // USE_OPENTHREAD

static const char *const TAG = "matter";
static const char *const TAG_EVENT = "matter.event";

static constexpr const char *NVS_NAMESPACE = "chip-factory";

namespace esphome::matter {

MatterComponent *global_matter_component =
    nullptr; // NOLINT(cppcoreguidelines-avoid-non-const-global-variables)

static void event_callback(const ChipDeviceEvent *event, intptr_t arg) {
  MatterCommissioning::instance().handle_event(event);
  network_on_device_event(event);
  switch (event->Type) {
  case chip::DeviceLayer::DeviceEventType::kInterfaceIpAddressChanged:
    ESP_LOGD(TAG_EVENT, "Interface IP Address changed");
    break;
  case chip::DeviceLayer::DeviceEventType::kCommissioningComplete:
    ESP_LOGI(TAG_EVENT, "Commissioning complete");
    break;
  case chip::DeviceLayer::DeviceEventType::kFailSafeTimerExpired:
    ESP_LOGI(TAG_EVENT, "Commissioning failed, fail safe timer expired");
    break;
  case chip::DeviceLayer::DeviceEventType::kCommissioningSessionStarted:
    ESP_LOGI(TAG_EVENT, "Commissioning session started");
    break;
  case chip::DeviceLayer::DeviceEventType::kCommissioningSessionStopped:
    ESP_LOGI(TAG_EVENT, "Commissioning session stopped");
    break;
  case chip::DeviceLayer::DeviceEventType::kCommissioningWindowOpened:
    ESP_LOGI(TAG_EVENT, "Commissioning window opened");
    break;
  case chip::DeviceLayer::DeviceEventType::kCommissioningWindowClosed:
    ESP_LOGI(TAG_EVENT, "Commissioning window closed");
    break;
  case chip::DeviceLayer::DeviceEventType::kFabricRemoved:
    ESP_LOGI(TAG_EVENT, "Fabric removed");
#ifdef USE_TEXT_SENSOR
    global_matter_component->schedule_fabric_sensor_update();
#endif
    break;
  case chip::DeviceLayer::DeviceEventType::kFabricWillBeRemoved:
    ESP_LOGI(TAG_EVENT, "Fabric will be removed");
    break;
  case chip::DeviceLayer::DeviceEventType::kFabricUpdated:
    ESP_LOGI(TAG_EVENT, "Fabric is updated");
#ifdef USE_TEXT_SENSOR
    global_matter_component->schedule_fabric_sensor_update();
#endif
    break;
  case chip::DeviceLayer::DeviceEventType::kFabricCommitted:
    ESP_LOGI(TAG_EVENT, "Fabric is committed");
#ifdef USE_TEXT_SENSOR
    global_matter_component->schedule_fabric_sensor_update();
#endif
    break;
  case chip::DeviceLayer::DeviceEventType::kDnssdRestartNeeded:
    ESP_LOGD(TAG_EVENT, "DNS-SD restart needed");
    break;
  case chip::DeviceLayer::DeviceEventType::kBindingsChangedViaCluster:
    ESP_LOGI(TAG_EVENT, "Bindings updated");
    break;
  case chip::DeviceLayer::DeviceEventType::kServerReady:
    ESP_LOGI(TAG_EVENT, "Server ready!");
    break;
  case chip::DeviceLayer::DeviceEventType::kSecureSessionEstablished: {
    const auto &s = event->SecureSessionEstablished;
    ESP_LOGI(TAG_EVENT,
             "Secure session established: %s node=0x%016" PRIx64
             " fabric=%u session=%u",
             s.SecureSessionType == 2   ? "CASE"
             : s.SecureSessionType == 1 ? "PASE"
                                        : "?",
             s.PeerNodeId, s.FabricIndex, s.LocalSessionId);
    break;
  }
  case chip::DeviceLayer::DeviceEventType::kCHIPoBLEAdvertisingChange:
    ESP_LOGI(TAG_EVENT, "BLE advertising %s",
             event->CHIPoBLEAdvertisingChange.Result ==
                     chip::DeviceLayer::kActivity_Started
                 ? "started"
                 : "stopped");
    break;
  case chip::DeviceLayer::DeviceEventType::kCHIPoBLEConnectionEstablished:
    ESP_LOGI(TAG_EVENT, "BLE connection established");
    break;
  case chip::DeviceLayer::DeviceEventType::kCHIPoBLEConnectionClosed:
    ESP_LOGI(TAG_EVENT, "BLE connection closed");
    break;
  case chip::DeviceLayer::DeviceEventType::kCHIPoBLEConnectionError:
    ESP_LOGW(TAG_EVENT, "BLE connection error");
    break;
  case chip::DeviceLayer::DeviceEventType::kBLEDeinitialized:
    ESP_LOGI(TAG_EVENT, "BLE deinitialized and its memory reclaimed");
    break;
  case chip::DeviceLayer::DeviceEventType::kWiFiConnectivityChange: // 0x8000
    ESP_LOGV(TAG_EVENT, "WiFiConnectivityChange");
    break;
  case chip::DeviceLayer::DeviceEventType::
      kThreadConnectivityChange: { // 0x8001
    switch (event->ThreadConnectivityChange.Result) {
    case chip::DeviceLayer::kConnectivity_Established:
      ESP_LOGI(TAG_EVENT, "ThreadConnectivityChange: established");
      break;
    case chip::DeviceLayer::kConnectivity_Lost:
      ESP_LOGW(TAG_EVENT, "ThreadConnectivityChange: lost");
      break;
    case chip::DeviceLayer::kConnectivity_NoChange:
      ESP_LOGV(TAG_EVENT, "ThreadConnectivityChange: no_change");
      break;
    default:
      ESP_LOGV(TAG_EVENT, "ThreadConnectivityChange: unknown (%d)",
               static_cast<int>(event->ThreadConnectivityChange.Result));
      break;
    }
    break;
  }
  case chip::DeviceLayer::DeviceEventType::
      kInternetConnectivityChange: // 0x8002
    ESP_LOGV(TAG_EVENT, "InternetConnectivityChange");
    break;
  case chip::DeviceLayer::DeviceEventType::kThreadStateChange: { // 0x800B
#ifdef USE_OPENTHREAD
    const auto &thread_state = event->ThreadStateChange;
    if (thread_state.RoleChanged) {
      auto lock = esphome::openthread::InstanceLock::try_acquire(1000);
      if (lock) {
        const otDeviceRole role = otThreadGetDeviceRole(lock.get_instance());
        ESP_LOGD(TAG_EVENT, "Thread role changed to %s",
                 otThreadDeviceRoleToString(role));
      } else {
        ESP_LOGW(TAG_EVENT, "Thread role changed; unable to acquire instance");
      }
    }
    if (thread_state.NetDataChanged) {
      ESP_LOGV(TAG_EVENT, "Thread network data changed");
    }
    if (thread_state.ChildNodesChanged) {
      ESP_LOGV(TAG_EVENT, "Thread child nodes changed");
    }
    if (thread_state.AddressChanged) {
      ESP_LOGD(TAG_EVENT, "Thread address(es) changed");
      auto lock = esphome::openthread::InstanceLock::try_acquire(1000);
      if (lock) {
        otInstance *instance = lock.get_instance();
        for (const otNetifAddress *addr = otIp6GetUnicastAddresses(instance);
             addr != nullptr; addr = addr->mNext) {
          char address[OT_IP6_ADDRESS_STRING_SIZE];
          otIp6AddressToString(&addr->mAddress, address, sizeof(address));
          if (addr->mValid)
            ESP_LOGD(TAG_EVENT, "  address %s/%u rloc=%s", address,
                     static_cast<unsigned>(addr->mPrefixLength),
                     YESNO(addr->mRloc));
          else
            ESP_LOGW(TAG_EVENT, "  [INVALID] address %s/%u rloc=%s", address,
                     static_cast<unsigned>(addr->mPrefixLength),
                     YESNO(addr->mRloc));
        }
      } else {
        ESP_LOGW(TAG_EVENT,
                 "ThreadStateChange: address list unavailable; failed to "
                 "acquire OpenThread lock");
      }
    }
    if (!thread_state.RoleChanged && !thread_state.AddressChanged &&
        !thread_state.NetDataChanged && !thread_state.ChildNodesChanged) {
      ESP_LOGV(TAG_EVENT, "ThreadStateChange: flags=0x%08" PRIx32,
               thread_state.OpenThread.Flags);
    }
#endif // USE_OPENTHREAD
    break;
  }
  case chip::DeviceLayer::DeviceEventType::kDnssdInitialized: // 0x8012
    ESP_LOGD(TAG_EVENT, "DnssdInitialized");
    break;
  default:
    ESP_LOGV(TAG_EVENT, "0x%04X", event->Type);
    break;
  }
}

void MatterComponent::setup() {
  global_matter_component = this;
  if (!MatterCommissioning::instance().init(MATTER_PERSIST_PASSCODE_MODE)) {
    this->mark_failed();
    return;
  }

  // Always update device-name so it stays in sync if the ESPHome device name
  // changes. This is the DN TXT record in _matterc._udp — what controllers show
  // in their UI.
  {
    nvs_handle_t h;
    if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h) == ESP_OK) {
      nvs_set_str(h, "device-name", App.get_name().c_str());
      nvs_commit(h);
      nvs_close(h);
    }
  }

  this->conformance_.init_configuration_version();
  this->conformance_.install_labels();

  esp_matter::node::config_t node_config;
  device_info_fill_root_config(this->device_info_, node_config);
#ifdef USE_MATTER_ICD_LIT
  // User Active Mode Trigger (mandatory with LIT): how the user can wake the device. The ICD Management cluster
  // reads both when the server starts, so they must be in the attribute store before esp_matter::start().
  node_config.root_node.icd_user_active_mode_trigger.user_active_mode_trigger_hint = MATTER_ICD_UAT_HINT;
  snprintf(node_config.root_node.icd_user_active_mode_trigger.user_active_mode_trigger_instruction,
           sizeof(node_config.root_node.icd_user_active_mode_trigger.user_active_mode_trigger_instruction), "%s",
           MATTER_ICD_UAT_INSTRUCTION);
#endif
  esp_matter::node_t *node = esp_matter::node::create(
      &node_config, endpoint_attribute_update_cb, matter_identification_cb);
  if (node == nullptr) {
    ESP_LOGE(TAG, "Failed to create Matter node");
    this->mark_failed();
    return;
  }

  if (!this->create_endpoints_(node)) {
    this->mark_failed();
    return;
  }

#ifdef USE_OPENTHREAD
  // ESPHome owns the OpenThread task/stack. connectedhomeip still needs its
  // ThreadStackManager bound to that existing instance for Thread diagnostics
  // and other Thread helpers.
  if (chip::DeviceLayer::ThreadStackMgrImpl()._AttachToThreadStack() !=
      CHIP_NO_ERROR) {
    ESP_LOGE(TAG, "Failed to attach ESPHome OpenThread stack to "
                  "connectedhomeip ThreadStackManager");
    this->mark_failed();
    return;
  }
#endif // USE_OPENTHREAD

#ifdef USE_MATTER_ICD_LIGHT_SLEEP
  // Automatic light sleep: the CPU sleeps whenever FreeRTOS is idle (tickless idle), and the 802.15.4 driver holds a PM
  // lock while the radio is awake. Nobody else calls esp_pm_configure() in an ESPHome firmware.
#if CONFIG_PM_ENABLE && CONFIG_FREERTOS_USE_TICKLESS_IDLE
  {
    esp_pm_config_t pm_config = {
        .max_freq_mhz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ,
        .min_freq_mhz = CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ,
        .light_sleep_enable = true,
    };
    esp_err_t pm_err = esp_pm_configure(&pm_config);
    if (pm_err != ESP_OK)
      ESP_LOGE(TAG, "esp_pm_configure (light sleep) failed: %s", esp_err_to_name(pm_err));
    else
      ESP_LOGI(TAG, "ICD light sleep enabled");
  }
#else
  ESP_LOGE(TAG, "matter: icd: light_sleep is on but CONFIG_PM_ENABLE / CONFIG_FREERTOS_USE_TICKLESS_IDLE are not set");
#endif
#endif // USE_MATTER_ICD_LIGHT_SLEEP

  /* Matter start */
  device_info_install_provider(this->device_info_);
  esp_err_t err = esp_matter::start(event_callback);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to start Matter: %s", esp_err_to_name(err));
    this->mark_failed();
    return;
  } else {
    ESP_LOGD(TAG, "Matter started successfully");
  }

  device_info_apply_post_start(this->device_info_);
#ifdef USE_MATTER_ICD
  MatterIcd::instance().start();
#endif
  esp_matter::client::binding_manager_init();
  replay_attribute_triggers(this);
  this->initialize_endpoint_mappings_();
#ifdef USE_TEXT_SENSOR
  this->publish_fabric_sensors_();
#endif
}

void MatterComponent::factory_reset() {
  MatterCommissioning::instance().factory_reset();
}

void MatterComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "Matter:");
  if (this->is_failed()) {
    ESP_LOGE(TAG, "  Failed to initialize!");
    return;
  }
  device_info_dump_config(this->device_info_);
#ifdef USE_MATTER_PLAN
  dump_plan();
#endif // USE_MATTER_PLAN

  MatterCommissioning::instance().dump_config();

  chip::DeviceLayer::PlatformMgr().LockChipStack();
  auto &server = chip::Server::GetInstance();
  ESP_LOGCONFIG(
      TAG, "  Commissioning window: %s",
      server.GetCommissioningWindowManager().IsCommissioningWindowOpen()
          ? "open"
          : "closed");
  const auto &fabric_table = server.GetFabricTable();
  if (fabric_table.FabricCount() == 0) {
    ESP_LOGCONFIG(TAG, "  Fabrics: none");
  } else {
    ESP_LOGCONFIG(TAG, "  Fabrics:");
    for (const auto &fabric : fabric_table) {
      char label[chip::kFabricLabelMaxLengthInBytes + 1] = {0};
      auto label_span = fabric.GetFabricLabel();
      memcpy(label, label_span.data(),
             std::min(label_span.size(), sizeof(label) - 1));
      uint64_t compressed_fabric_id = fabric.GetCompressedFabricId();
      uint64_t node_id = fabric.GetNodeId();
      ESP_LOGCONFIG(
          TAG,
          "    [%u] CompressedFabricId: 0x%08" PRIx32 "%08" PRIx32
          ", NodeId: 0x%08" PRIx32 "%08" PRIx32 " (%" PRIu64 ")"
          ", VendorId: 0x%04x%s%s",
          fabric.GetFabricIndex(), (uint32_t)(compressed_fabric_id >> 32),
          (uint32_t)(compressed_fabric_id), (uint32_t)(node_id >> 32),
          (uint32_t)(node_id), node_id, (uint16_t)fabric.GetVendorId(),
          label[0] ? ", Label: " : "", label);
    }
  }
  chip::DeviceLayer::PlatformMgr().UnlockChipStack();
}

} // namespace esphome::matter

#endif // USE_MATTER
