#include "matter.h"
#ifdef USE_MATTER

#include "matter_entity.h"
#include "esphome/core/log.h"

#include <nvs_flash.h>
#include <esp_matter.h>
#include <esp_matter_ota.h>
#include <app/server/Server.h>
#include <app/server/CommissioningWindowManager.h>
#include <setup_payload/QRCodeSetupPayloadGenerator.h>
#include <setup_payload/ManualSetupPayloadGenerator.h>
#include <setup_payload/SetupPayload.h>

namespace esphome::matter {

static const char *const TAG = "matter";

static MatterComponent *g_matter = nullptr;

static esp_err_t matter_attribute_update_cb(::esp_matter::attribute::callback_type_t type, uint16_t endpoint_id,
                                            uint32_t cluster_id, uint32_t attribute_id,
                                            esp_matter_attr_val_t *val, void *priv_data) {
  if (type != ::esp_matter::attribute::PRE_UPDATE || g_matter == nullptr || priv_data == nullptr) {
    return ESP_OK;
  }
  auto *entity = reinterpret_cast<MatterEntity *>(priv_data);
  entity->on_matter_attribute_update(cluster_id, attribute_id, val);
  return ESP_OK;
}

static esp_err_t matter_identification_cb(::esp_matter::identification::callback_type_t type, uint16_t endpoint_id,
                                          uint8_t effect_id, uint8_t effect_variant, void *priv_data) {
  ESP_LOGI(TAG, "identify ep=%u type=%u effect=%u", endpoint_id, type, effect_id);
  return ESP_OK;
}

static void matter_event_cb(const ChipDeviceEvent *event, intptr_t /*arg*/) {
  using namespace chip::DeviceLayer;
  if (g_matter == nullptr)
    return;
  switch (event->Type) {
    case DeviceEventType::kCommissioningComplete:
      ESP_LOGI(TAG, "commissioning complete");
      g_matter->fire_commission_trigger(true);
      break;
    case DeviceEventType::kFabricRemoved: {
      ESP_LOGI(TAG, "fabric removed");
      g_matter->fire_commission_trigger(false);
      auto &mgr = chip::Server::GetInstance().GetCommissioningWindowManager();
      if (chip::Server::GetInstance().GetFabricTable().FabricCount() == 0 &&
          !mgr.IsCommissioningWindowOpen()) {
        (void) mgr.OpenBasicCommissioningWindow(chip::System::Clock::Seconds16(300),
                                                chip::CommissioningWindowAdvertisement::kDnssdOnly);
      }
      break;
    }
    case DeviceEventType::kBLEDeinitialized:
      ESP_LOGI(TAG, "BLE deinitialized");
      break;
    default:
      break;
  }
}

float MatterComponent::get_setup_priority() const {
  // Matter must come up after WiFi but before user-facing entities expect a fabric.
  return setup_priority::AFTER_WIFI;
}

void MatterComponent::setup() {
  g_matter = this;
  ESP_LOGI(TAG, "starting matter (vid=0x%04X pid=0x%04X discriminator=%u)", this->vendor_id_, this->product_id_,
           this->discriminator_);

  esp_err_t err = nvs_flash_init_partition("nvs_chip");
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "nvs_chip init failed (%d); falling back to default nvs", err);
  }

  ::esp_matter::node::config_t node_config;
  ::esp_matter::node_t *node = ::esp_matter::node::create(&node_config, matter_attribute_update_cb,
                                                          matter_identification_cb);
  if (node == nullptr) {
    ESP_LOGE(TAG, "failed to create matter node");
    this->mark_failed();
    return;
  }

  for (auto *entity : this->entities_) {
    entity->register_with_node(node);
  }

  err = ::esp_matter::start(matter_event_cb);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "esp_matter::start failed: %d", err);
    this->mark_failed();
    return;
  }

  // Log QR + manual pairing code so users can commission on first boot.
  chip::PayloadContents payload;
  payload.version = 0;
  payload.vendorID = this->vendor_id_;
  payload.productID = this->product_id_;
  payload.discriminator.SetLongValue(this->discriminator_);
  payload.setUpPINCode = this->passcode_;
  payload.rendezvousInformation.SetValue(chip::RendezvousInformationFlags(chip::RendezvousInformationFlag::kBLE));
  payload.commissioningFlow = chip::CommissioningFlow::kStandard;

  char qr_buf[chip::QRCodeBasicSetupPayloadGenerator::kMaxQRCodeBase38RepresentationLength + 1];
  chip::MutableCharSpan qr_span(qr_buf);
  if (chip::QRCodeBasicSetupPayloadGenerator(payload).payloadBase38Representation(qr_span) == CHIP_NO_ERROR) {
    ESP_LOGI(TAG, "QR: MT:%s", qr_buf);
    ESP_LOGI(TAG, "QR URL: https://project-chip.github.io/connectedhomeip/qrcode.html?data=MT%%3A%s", qr_buf);
  }

  char manual_buf[chip::kManualSetupLongCodeCharLength + 1];
  chip::MutableCharSpan manual_span(manual_buf);
  if (chip::ManualSetupPayloadGenerator(payload).payloadDecimalStringRepresentation(manual_span) == CHIP_NO_ERROR) {
    ESP_LOGI(TAG, "Manual pairing code: %s", manual_buf);
  }

  this->disable_loop();
}

void MatterComponent::loop() {}

void MatterComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "Matter:");
  ESP_LOGCONFIG(TAG, "  Vendor ID: 0x%04X", this->vendor_id_);
  ESP_LOGCONFIG(TAG, "  Product ID: 0x%04X", this->product_id_);
  ESP_LOGCONFIG(TAG, "  Discriminator: %u", this->discriminator_);
  ESP_LOGCONFIG(TAG, "  Endpoints: %u", (unsigned) this->entities_.size());
  if (this->vendor_id_ == 0xFFF2) {
    ESP_LOGW(TAG, "Using CSA test DAC (VID 0xFFF2). Apple Home will warn 'Uncertified Accessory'.");
  }
}

void MatterComponent::factory_reset() {
  ESP_LOGW(TAG, "matter factory reset triggered");
  ::esp_matter::factory_reset();
}

void MatterComponent::open_commissioning_window(uint16_t seconds) {
  auto &mgr = chip::Server::GetInstance().GetCommissioningWindowManager();
  if (mgr.IsCommissioningWindowOpen())
    return;
  (void) mgr.OpenBasicCommissioningWindow(chip::System::Clock::Seconds16(seconds),
                                          chip::CommissioningWindowAdvertisement::kDnssdOnly);
}

}  // namespace esphome::matter

#endif
