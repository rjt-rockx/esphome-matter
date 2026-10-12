#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "matter_commissioning.h"

#include "esphome/core/log.h"
#include "matter_attributes.h"
#include "matter_component.h"

#include <sdkconfig.h>

#include <algorithm>
#include <cinttypes>
#include <cstring>
#include <esp_matter.h>
#include <esp_matter_providers.h>
#include <esp_random.h>
#include <nvs.h>

#include <app/server/Server.h>
#include <crypto/CHIPCryptoPAL.h>
#include <lib/support/Base64.h>
#include <platform/CHIPDeviceLayer.h>
#include <platform/CommissionableDataProvider.h>
#include <setup_payload/ManualSetupPayloadGenerator.h>
#include <setup_payload/QRCodeSetupPayloadGenerator.h>
#include <setup_payload/SetupPayload.h>

#ifndef CONFIG_CUSTOM_COMMISSIONABLE_DATA_PROVIDER
#error "esphome-matter requires CONFIG_CUSTOM_COMMISSIONABLE_DATA_PROVIDER=y (set by components/matter/__init__.py)"
#endif

static const char *const TAG = "matter.commissioning";

// Keys in the "chip-factory" NVS namespace, matching CHIP's ESP32Config key
// names so devices flashed with older firmware keep working.
static constexpr const char *NVS_NAMESPACE = "chip-factory";
static constexpr const char *NVS_KEY_DISCRIMINATOR = "discriminator";
static constexpr const char *NVS_KEY_PASSCODE = "pin-code"; // legacy/persist only
static constexpr const char *NVS_KEY_ITERATION_COUNT = "iteration-count";
static constexpr const char *NVS_KEY_SALT = "salt";
static constexpr const char *NVS_KEY_VERIFIER = "verifier";

static constexpr uint32_t SPAKE2P_ITERATION_COUNT = 1000;

static bool is_valid_passcode(uint32_t pin) {
  if (pin == 0 || pin > 99999998)
    return false;
  static constexpr uint32_t FORBIDDEN[] = {
      11111111, 22222222, 33333333, 44444444, 55555555, 66666666,
      77777777, 88888888, 99999999, 12345678, 87654321,
  };
  for (uint32_t f : FORBIDDEN) {
    if (pin == f)
      return false;
  }
  return true;
}

static uint32_t random_passcode() {
  uint32_t passcode;
  do {
    passcode = (esp_random() % 99999998u) + 1u;
  } while (!is_valid_passcode(passcode));
  return passcode;
}

// Reads a base64 encoded blob from NVS.
static bool read_b64(nvs_handle_t handle, const char *key, uint8_t *out,
                     size_t max_len, size_t &out_len) {
  char b64[136];
  size_t size = sizeof(b64);
  if (nvs_get_str(handle, key, b64, &size) != ESP_OK || size < 2)
    return false;
  uint8_t tmp[104];
  uint32_t n = chip::Base64Decode32(b64, (uint32_t)(size - 1), tmp);
  if (n == UINT32_MAX || n > max_len || n > sizeof(tmp))
    return false;
  memcpy(out, tmp, n);
  out_len = n;
  return true;
}

static bool write_b64(nvs_handle_t handle, const char *key,
                      const uint8_t *data, size_t len) {
  char b64[136];
  if (BASE64_ENCODED_LEN(len) + 1 > sizeof(b64))
    return false;
  b64[chip::Base64Encode32(data, (uint32_t)len, b64)] = '\0';
  return nvs_set_str(handle, key, b64) == ESP_OK;
}

namespace esphome::matter {

// Serves the SPAKE2+ verifier, salt, iteration count and discriminator out of
// RAM. It deliberately never returns the passcode: CHIP only uses
// GetSetupPasscode() for informational logging, and returning an error keeps
// the passcode out of CHIP's own boot logs.
class MatterCommissionableDataProvider
    : public chip::DeviceLayer::CommissionableDataProvider {
public:
  explicit MatterCommissionableDataProvider(MatterCommissioning *owner)
      : owner_(owner) {}

  CHIP_ERROR GetSetupDiscriminator(uint16_t &discriminator) override {
    discriminator = this->owner_->identity_.discriminator;
    return CHIP_NO_ERROR;
  }
  CHIP_ERROR SetSetupDiscriminator(uint16_t) override {
    return CHIP_ERROR_NOT_IMPLEMENTED;
  }
  CHIP_ERROR GetSpake2pIterationCount(uint32_t &iteration_count) override {
    iteration_count = this->owner_->identity_.iterations;
    return CHIP_NO_ERROR;
  }
  CHIP_ERROR GetSpake2pSalt(chip::MutableByteSpan &salt) override {
    const auto &id = this->owner_->identity_;
    VerifyOrReturnError(salt.size() >= id.salt_len,
                        CHIP_ERROR_BUFFER_TOO_SMALL);
    memcpy(salt.data(), id.salt, id.salt_len);
    salt.reduce_size(id.salt_len);
    return CHIP_NO_ERROR;
  }
  CHIP_ERROR GetSpake2pVerifier(chip::MutableByteSpan &verifier,
                                size_t &out_len) override {
    const auto &id = this->owner_->identity_;
    // Valid for CHIP_ERROR_BUFFER_TOO_SMALL as well (see CommissionableDataProvider.h).
    out_len = id.verifier_len;
    VerifyOrReturnError(verifier.size() >= id.verifier_len,
                        CHIP_ERROR_BUFFER_TOO_SMALL);
    memcpy(verifier.data(), id.verifier, id.verifier_len);
    verifier.reduce_size(id.verifier_len);
    return CHIP_NO_ERROR;
  }
  CHIP_ERROR GetSetupPasscode(uint32_t &) override {
    return CHIP_ERROR_NOT_IMPLEMENTED;
  }
  CHIP_ERROR SetSetupPasscode(uint32_t) override {
    return CHIP_ERROR_NOT_IMPLEMENTED;
  }

private:
  MatterCommissioning *owner_;
};

MatterCommissioning &MatterCommissioning::instance() {
  static MatterCommissioning inst;
  return inst;
}

// Creates a new random salt + verifier (and passcode, unless fixed in YAML).
bool MatterCommissioning::generate_passcode_(Identity &id) {
#ifdef MATTER_PASSCODE
  id.passcode = MATTER_PASSCODE;
#else
  id.passcode = random_passcode();
#endif
  id.iterations = SPAKE2P_ITERATION_COUNT;
  id.salt_len = MAX_SALT_LEN;
  esp_fill_random(id.salt, id.salt_len);

  chip::Crypto::Spake2pVerifier verifier;
  if (verifier.Generate(id.iterations, chip::ByteSpan(id.salt, id.salt_len),
                        id.passcode) != CHIP_NO_ERROR) {
    ESP_LOGE(TAG, "Failed to generate Spake2p verifier");
    return false;
  }
  chip::MutableByteSpan span(id.verifier, sizeof(id.verifier));
  if (verifier.Serialize(span) != CHIP_NO_ERROR) {
    ESP_LOGE(TAG, "Failed to serialize Spake2p verifier");
    return false;
  }
  id.verifier_len = span.size();
  id.passcode_known = true;
  return true;
}

// Persists the identity. The plaintext passcode is written when
// persist_passcode is true, or until_commissioned and the passcode is still
// known (it is forgotten on the first commissioning). A passcode fixed in the
// YAML is never written (it is compiled in). Otherwise a stale "pin-code" key
// is removed.
bool MatterCommissioning::store_identity_(const Identity &id) {
  nvs_handle_t handle;
  if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle) != ESP_OK) {
    ESP_LOGE(TAG, "Failed to open NVS namespace '%s'", NVS_NAMESPACE);
    return false;
  }
  bool ok = nvs_set_u32(handle, NVS_KEY_DISCRIMINATOR, id.discriminator) ==
                ESP_OK &&
            nvs_set_u32(handle, NVS_KEY_ITERATION_COUNT, id.iterations) ==
                ESP_OK &&
            write_b64(handle, NVS_KEY_SALT, id.salt, id.salt_len) &&
            write_b64(handle, NVS_KEY_VERIFIER, id.verifier, id.verifier_len);
  const bool keep_passcode =
      id.passcode_known &&
      (this->persist_passcode_ == PersistPasscode::ALWAYS ||
       (this->persist_passcode_ == PersistPasscode::UNTIL_COMMISSIONED &&
        !this->fixed_passcode_));
  if (keep_passcode)
    ok = ok && nvs_set_u32(handle, NVS_KEY_PASSCODE, id.passcode) == ESP_OK;
  else
    nvs_erase_key(handle, NVS_KEY_PASSCODE); // ESP_ERR_NVS_NOT_FOUND is fine
  ok = ok && nvs_commit(handle) == ESP_OK;
  nvs_close(handle);
  if (!ok)
    ESP_LOGE(TAG, "Failed to store commissioning data");
  return ok;
}

bool MatterCommissioning::init(int persist_passcode_mode) {
  this->persist_passcode_ = static_cast<PersistPasscode>(persist_passcode_mode);
#ifdef MATTER_PASSCODE
  this->fixed_passcode_ = true;
#endif

  Identity &id = this->identity_;
  nvs_handle_t handle;
  if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle) != ESP_OK) {
    ESP_LOGE(TAG, "Failed to open NVS namespace '%s'", NVS_NAMESPACE);
    return false;
  }
  uint32_t disc = 0, legacy_pin = 0;
  bool have = nvs_get_u32(handle, NVS_KEY_DISCRIMINATOR, &disc) == ESP_OK &&
              nvs_get_u32(handle, NVS_KEY_ITERATION_COUNT, &id.iterations) ==
                  ESP_OK &&
              read_b64(handle, NVS_KEY_SALT, id.salt, sizeof(id.salt),
                       id.salt_len) &&
              read_b64(handle, NVS_KEY_VERIFIER, id.verifier,
                       sizeof(id.verifier), id.verifier_len) &&
              id.verifier_len == VERIFIER_LEN;
  bool have_legacy_pin =
      nvs_get_u32(handle, NVS_KEY_PASSCODE, &legacy_pin) == ESP_OK;
  nvs_close(handle);

  bool changed = false;
  if (!have) {
    // First boot (or unusable data): create a brand new identity.
    id = Identity();
#ifdef MATTER_DISCRIMINATOR
    id.discriminator = MATTER_DISCRIMINATOR;
#else
    id.discriminator = (uint16_t)(esp_random() & 0x0FFFu);
#endif
    if (!this->generate_passcode_(id))
      return false;
    changed = true;
#ifdef MATTER_PASSCODE
    ESP_LOGI(TAG, "Created the commissioning data from the passcode in the YAML");
#else
    ESP_LOGI(TAG, "Generated a new commissioning passcode");
#endif
  } else {
    id.discriminator = (uint16_t)disc;
#ifdef MATTER_DISCRIMINATOR
    if (id.discriminator != MATTER_DISCRIMINATOR) {
      id.discriminator = MATTER_DISCRIMINATOR;
      changed = true;
    }
#endif
    if (have_legacy_pin && is_valid_passcode(legacy_pin)) {
      // The plaintext passcode is in flash: either by design (true,
      // until_commissioned while uncommissioned) or left by older firmware.
      // It is usable from RAM. For until_commissioned on_server_ready_()
      // erases it if the device turns out to be commissioned.
      id.passcode = legacy_pin;
      id.passcode_known = true;
      if (this->persist_passcode_ == PersistPasscode::NEVER ||
          this->fixed_passcode_) {
        // store_identity_() erases the flash copy.
        changed = true;
        ESP_LOGI(TAG, "Migrating legacy commissioning data (plaintext "
                      "passcode removed from flash)");
      }
    }
#ifdef MATTER_PASSCODE
    // A passcode in the YAML wins. Re-derive the verifier if it changed.
    chip::Crypto::Spake2pVerifier verifier;
    uint8_t expected[VERIFIER_LEN];
    chip::MutableByteSpan span(expected, sizeof(expected));
    if (verifier.Generate(id.iterations, chip::ByteSpan(id.salt, id.salt_len),
                          MATTER_PASSCODE) != CHIP_NO_ERROR ||
        verifier.Serialize(span) != CHIP_NO_ERROR) {
      ESP_LOGE(TAG, "Failed to derive Spake2p verifier");
      return false;
    }
    if (span.size() != id.verifier_len ||
        memcmp(expected, id.verifier, id.verifier_len) != 0) {
      memcpy(id.verifier, expected, span.size());
      id.verifier_len = span.size();
      changed = true;
    }
    id.passcode = MATTER_PASSCODE;
    id.passcode_known = true;
#endif
  }
  if (changed && !this->store_identity_(id))
    return false;

  static MatterCommissionableDataProvider provider(this);
  esp_matter::set_custom_commissionable_data_provider(&provider);
  return true;
}

bool MatterCommissioning::build_codes_(uint32_t passcode,
                                       uint16_t discriminator, bool ecm,
                                       std::string &qr,
                                       std::string &manual) const {
  chip::SetupPayload payload;
  payload.version = 0;
  payload.vendorID = CHIP_DEVICE_CONFIG_DEVICE_VENDOR_ID;
  payload.productID = CHIP_DEVICE_CONFIG_DEVICE_PRODUCT_ID;
  payload.commissioningFlow = chip::CommissioningFlow::kStandard;
#ifdef CONFIG_ENABLE_CHIPOBLE
  // The device is commissioned over BLE unless the window was opened on an
  // already-commissioned device (those always use the operational network).
  if (!ecm)
    payload.rendezvousInformation.SetValue(chip::RendezvousInformationFlag::kBLE);
  else
#endif // CONFIG_ENABLE_CHIPOBLE
    payload.rendezvousInformation.SetValue(
        chip::RendezvousInformationFlag::kOnNetwork);
  payload.discriminator.SetLongValue(discriminator);
  payload.setUpPINCode = passcode;

  if (chip::QRCodeSetupPayloadGenerator(payload).payloadBase38Representation(
          qr) != CHIP_NO_ERROR) {
    qr.clear();
    return false;
  }
  if (chip::ManualSetupPayloadGenerator(payload)
          .payloadDecimalStringRepresentation(manual) != CHIP_NO_ERROR)
    manual.clear();
  return true;
}

// CHIP task (stack locked). Builds a snapshot and publishes it on the main loop.
void MatterCommissioning::refresh_(bool log_codes, bool publish) {
  auto &server = chip::Server::GetInstance();
  auto &wm = server.GetCommissioningWindowManager();
  CommissioningState s;
  s.valid = true;
  s.window_open = wm.IsCommissioningWindowOpen();
  s.fabric_count = server.GetFabricTable().FabricCount();
  s.error = this->error_.load();
  for (const auto &fabric : server.GetFabricTable()) {
    char head[16];
    snprintf(head, sizeof(head), "%u: ", (unsigned) fabric.GetFabricIndex());
    char tail[24];
    snprintf(tail, sizeof(tail), " (vendor 0x%04X)",
             (unsigned) static_cast<uint16_t>(fabric.GetVendorId()));
    const chip::CharSpan label = fabric.GetFabricLabel();
    if (!s.controllers.empty())
      s.controllers += "; ";
    s.controllers += head;
    s.controllers.append(label.data(), label.size());
    s.controllers += tail;
  }
  if (s.window_open) {
    using chip::app::Clusters::AdministratorCommissioning::
        CommissioningWindowStatusEnum;
    bool ecm = wm.CommissioningWindowStatusForCluster() ==
               CommissioningWindowStatusEnum::kEnhancedWindowOpen;
    if (ecm && this->ephemeral_active_)
      this->build_codes_(this->ephemeral_passcode_,
                         this->ephemeral_discriminator_, true, s.qr_code,
                         s.manual_code);
    else if (!ecm && this->identity_.passcode_known)
      this->build_codes_(this->identity_.passcode,
                         this->identity_.discriminator, false, s.qr_code,
                         s.manual_code);
  }
  if (log_codes && s.window_open) {
    if (!s.qr_code.empty()) {
      ESP_LOGI(TAG, "Commissioning window open. SetupQRCode: %s",
               s.qr_code.c_str());
      ESP_LOGI(TAG,
               "QR URL: "
               "https://project-chip.github.io/connectedhomeip/qrcode.html"
               "?data=%s",
               s.qr_code.c_str());
      ESP_LOGI(TAG, "Manual pairing code: %s", s.manual_code.c_str());
    } else {
      ESP_LOGI(TAG, "Commissioning window open (the device does not know the "
                    "pairing code; the controller that opened it does, see "
                    "docs/commissioning.md)");
    }
  }
  if (!publish || global_matter_component == nullptr)
    return;
  global_matter_component->defer_to_main_loop([this, s]() {
    this->state_ = s;
    for (auto &cb : this->state_callbacks_)
      cb();
  });
}

void MatterCommissioning::on_server_ready_() {
  auto &server = chip::Server::GetInstance();
  auto &wm = server.GetCommissioningWindowManager();
  const bool commissioned = server.GetFabricTable().FabricCount() > 0;
  if (!commissioned && !this->identity_.passcode_known) {
    // Uncommissioned device that has no passcode (persist_passcode: false, a
    // factory reset or decommissioning that erased it, or an upgrade). Nothing
    // is paired yet, so simply issue a fresh one.
    if (this->regenerate_identity_()) {
#ifdef MATTER_PASSCODE
      ESP_LOGI(TAG, "Device is not commissioned: reset the commissioning data "
                    "(fixed passcode from the YAML)");
#else
      ESP_LOGI(TAG, "Device is not commissioned: generated a new passcode");
#endif
      wm.CloseCommissioningWindow();
      if (wm.OpenBasicCommissioningWindow() != CHIP_NO_ERROR)
        ESP_LOGW(TAG, "Failed to re-open the commissioning window");
    }
  } else if (commissioned &&
             this->persist_passcode_ == PersistPasscode::UNTIL_COMMISSIONED &&
             !this->fixed_passcode_) {
    // Commissioned: a plaintext passcode left in flash (legacy data, or a
    // commissioning whose cleanup did not run) must not survive.
    this->identity_.passcode_known = false;
    this->identity_.passcode = 0;
    this->erase_passcode_key_();
  }
  this->refresh_(false);
}

void MatterCommissioning::on_fabric_removed_() {
  auto &server = chip::Server::GetInstance();
  auto &wm = server.GetCommissioningWindowManager();
  const bool resetting = this->resetting_.load();
  if (server.GetFabricTable().FabricCount() == 0) {
    // Last fabric gone: the device must become commissionable again. (Not when
    // resetting: the reboot follows right away and handles that.)
    // A removal that leaves the passcode known is a failed commissioning
    // (fail-safe expiry deleting the half-added fabric): keep the code the user
    // is typing.
    if (!resetting && !this->identity_.passcode_known) {
      if (this->regenerate_identity_() && wm.IsCommissioningWindowOpen())
        wm.CloseCommissioningWindow(); // re-opened below with the new identity
    }
    if (!resetting && !wm.IsCommissioningWindowOpen() &&
        wm.OpenBasicCommissioningWindow() != CHIP_NO_ERROR)
      ESP_LOGW(TAG, "Failed to re-open the commissioning window");
    if (global_matter_component != nullptr)
      global_matter_component->defer_to_main_loop([this]() {
        for (auto &cb : this->decommissioned_callbacks_)
          cb();
      });
  }
  if (!resetting)
    this->refresh_(true);
}

void MatterCommissioning::handle_event(const chip::DeviceLayer::ChipDeviceEvent *event) {
  switch (event->Type) {
  case chip::DeviceLayer::DeviceEventType::kServerReady:
    this->on_server_ready_();
    break;
  case chip::DeviceLayer::DeviceEventType::kCommissioningWindowOpened:
    this->error_ = false;
    this->refresh_(true);
    break;
  case chip::DeviceLayer::DeviceEventType::kCommissioningWindowClosed:
    if (!chip::Server::GetInstance()
             .GetCommissioningWindowManager()
             .IsCommissioningWindowOpen()) {
      this->ephemeral_active_ = false;
      this->ephemeral_passcode_ = 0;
    }
    this->refresh_(false);
    break;
  case chip::DeviceLayer::DeviceEventType::kCommissioningComplete:
    this->error_ = false;
    if (this->persist_passcode_ != PersistPasscode::ALWAYS &&
        !this->fixed_passcode_) {
      // Forget the passcode: from now on only the verifier exists. This also
      // erases the flash copy of until_commissioned (CHIP task, the same task
      // that wrote it).
      this->identity_.passcode_known = false;
      this->identity_.passcode = 0;
      if (this->persist_passcode_ == PersistPasscode::UNTIL_COMMISSIONED)
        this->erase_passcode_key_();
    }
    if (global_matter_component != nullptr)
      global_matter_component->defer_to_main_loop([this]() {
        for (auto &cb : this->commissioned_callbacks_)
          cb();
      });
    this->refresh_(false);
    break;
  case chip::DeviceLayer::DeviceEventType::kFabricRemoved:
    this->on_fabric_removed_();
    break;
  case chip::DeviceLayer::DeviceEventType::kFabricUpdated: // label changed
  case chip::DeviceLayer::DeviceEventType::kFabricCommitted:
    this->refresh_(false);
    break;
  default:
    break;
  }
}

void MatterCommissioning::dump_config() {
  ESP_LOGCONFIG(TAG, "  Discriminator: %u", this->identity_.discriminator);
  ESP_LOGCONFIG(TAG, "  Passcode storage: %s",
                this->fixed_passcode_    ? "fixed in YAML"
                : this->persist_passcode_ == PersistPasscode::ALWAYS
                    ? "plaintext in flash"
                : this->persist_passcode_ == PersistPasscode::UNTIL_COMMISSIONED
                    ? "plaintext in flash until commissioned"
                    : "verifier only");
  // The codes are only printed while a commissioning window is open (the
  // window-open event prints them too); never on a regular boot.
  chip::DeviceLayer::PlatformMgr().LockChipStack();
  this->refresh_(true, false);
  chip::DeviceLayer::PlatformMgr().UnlockChipStack();
}

void MatterCommissioning::open_window(uint32_t timeout_s) {
  chip::DeviceLayer::SystemLayer().ScheduleLambda([this, timeout_s]() {
    auto &server = chip::Server::GetInstance();
    auto &wm = server.GetCommissioningWindowManager();
    const auto timeout = chip::System::Clock::Seconds32(timeout_s);

    if (server.GetFabricTable().FabricCount() == 0) {
      if (!this->identity_.passcode_known && !this->regenerate_identity_()) {
        this->set_error_(true);
        return;
      }
      wm.CloseCommissioningWindow();
      if (wm.OpenBasicCommissioningWindow(timeout) != CHIP_NO_ERROR) {
        ESP_LOGW(TAG, "Failed to open the commissioning window");
        this->set_error_(true);
      }
      return;
    }

    uint32_t passcode = random_passcode();
    uint16_t discriminator = (uint16_t)(esp_random() & 0x0FFFu);
    uint8_t salt[MAX_SALT_LEN];
    esp_fill_random(salt, sizeof(salt));
    chip::Crypto::Spake2pVerifier verifier;
    if (verifier.Generate(SPAKE2P_ITERATION_COUNT,
                          chip::ByteSpan(salt, sizeof(salt)),
                          passcode) != CHIP_NO_ERROR) {
      ESP_LOGE(TAG, "Failed to generate Spake2p verifier");
      return;
    }
    // Attribute the window to the first fabric (AdminFabricIndex/AdminVendorId).
    chip::FabricIndex fabric_index = chip::kUndefinedFabricIndex;
    chip::VendorId vendor_id = chip::VendorId::NotSpecified;
    for (const auto &fabric : server.GetFabricTable()) {
      fabric_index = fabric.GetFabricIndex();
      vendor_id = fabric.GetVendorId();
      break;
    }
    this->ephemeral_active_ = true;
    this->ephemeral_passcode_ = passcode;
    this->ephemeral_discriminator_ = discriminator;
    wm.CloseCommissioningWindow();
    CHIP_ERROR err = wm.OpenEnhancedCommissioningWindow(
        timeout, discriminator, verifier, SPAKE2P_ITERATION_COUNT,
        chip::ByteSpan(salt, sizeof(salt)), fabric_index, vendor_id);
    if (err != CHIP_NO_ERROR) {
      ESP_LOGW(TAG, "Failed to open the commissioning window: %" CHIP_ERROR_FORMAT,
               err.Format());
      this->ephemeral_active_ = false;
      this->ephemeral_passcode_ = 0;
      this->set_error_(true);
    }
  });
}

// New passcode, salt and verifier (plus a new discriminator for
// until_commissioned, unless fixed in the YAML), persisted according to
// persist_passcode. CHIP task. Keeps the old identity on failure.
bool MatterCommissioning::regenerate_identity_() {
  Identity fresh = this->identity_;
#ifndef MATTER_DISCRIMINATOR
  if (this->persist_passcode_ == PersistPasscode::UNTIL_COMMISSIONED)
    fresh.discriminator = (uint16_t)(esp_random() & 0x0FFFu);
#endif
  if (!this->generate_passcode_(fresh) || !this->store_identity_(fresh))
    return false;
  this->identity_ = fresh;
  return true;
}

void MatterCommissioning::erase_passcode_key_() {
  nvs_handle_t h;
  if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) {
    ESP_LOGE(TAG, "Failed to open NVS namespace '%s'", NVS_NAMESPACE);
    return;
  }
  esp_err_t err = nvs_erase_key(h, NVS_KEY_PASSCODE);
  if (err == ESP_OK)
    err = nvs_commit(h);
  else if (err == ESP_ERR_NVS_NOT_FOUND)
    err = ESP_OK;
  nvs_close(h);
  if (err != ESP_OK)
    ESP_LOGE(TAG, "Failed to erase the stored passcode");
}

void MatterCommissioning::erase_identity_keys_() {
  nvs_handle_t h;
  if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK)
    return;
  for (const char *key :
       {NVS_KEY_DISCRIMINATOR, NVS_KEY_PASSCODE, NVS_KEY_ITERATION_COUNT,
        NVS_KEY_SALT, NVS_KEY_VERIFIER})
    nvs_erase_key(h, key);
  nvs_commit(h);
  nvs_close(h);
}

void MatterCommissioning::set_error_(bool error) {
  this->error_ = error;
  this->refresh_(false);
}

// Safe from any thread: all work happens on the CHIP task, which owns the
// stack lock (esp_matter::factory_reset() touches the fabric table and ends in
// ConfigurationMgr().InitiateFactoryReset()).
void MatterCommissioning::factory_reset() {
  ESP_LOGW(TAG, "Matter factory reset: removing all fabrics and rebooting");
  this->resetting_ = true;
  run_on_matter_thread([this]() { this->factory_reset_on_chip_task_(false); });
}

void MatterCommissioning::reset_identity() {
  ESP_LOGW(TAG, "Matter identity reset: removing all fabrics and creating a "
                "new passcode and discriminator on the next boot");
  this->resetting_ = true;
  run_on_matter_thread([this]() { this->factory_reset_on_chip_task_(true); });
}

// CHIP task. Erases esp-matter's own KVS namespace, then asks the CHIP task
// (this one) to delete all fabrics, clear chip-config, chip-counters and the
// CHIP KVS (fabrics, ACLs, groups, sessions, subscriptions), restore the
// Wi-Fi settings and restart. Only the esp-matter step can report an error,
// and the CHIP part is scheduled regardless.
void MatterCommissioning::factory_reset_on_chip_task_(bool erase_identity) {
  if (erase_identity)
    this->erase_identity_keys_();
  if (esp_matter::factory_reset() != ESP_OK)
    ESP_LOGE(TAG, "Failed to erase the esp-matter data; the CHIP reset still runs");
}

void MatterCommissioning::remove_fabric(uint8_t fabric_index) {
  run_on_matter_thread([fabric_index]() {
    auto &table = chip::Server::GetInstance().GetFabricTable();
    if (table.FindFabricWithIndex(fabric_index) == nullptr) {
      ESP_LOGW(TAG, "matter.remove_fabric: no fabric with index %u",
               (unsigned) fabric_index);
      return;
    }
    CHIP_ERROR err = table.Delete(fabric_index);
    if (err != CHIP_NO_ERROR)
      ESP_LOGW(TAG, "Failed to remove fabric %u: %" CHIP_ERROR_FORMAT,
               (unsigned) fabric_index, err.Format());
    else
      ESP_LOGI(TAG, "Removed fabric %u", (unsigned) fabric_index);
  });
}

} // namespace esphome::matter

#endif // USE_MATTER
