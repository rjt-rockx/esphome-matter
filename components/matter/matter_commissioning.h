#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER
#include "esphome/core/automation.h"

#include <cstddef>
#include <cstdint>
#include <atomic>
#include <functional>
#include <string>
#include <vector>

#include <platform/CHIPDeviceEvent.h>

namespace esphome::matter {

// Snapshot of the commissioning state. Only read and written on the ESPHome
// main loop: the CHIP task builds a new snapshot and defers it.
struct CommissioningState {
  // False until the CHIP task published the first snapshot, so listeners do not
  // report "uncommissioned" for a few seconds after every reboot.
  bool valid{false};
  bool window_open{false};
  uint8_t fabric_count{0};
  // Both are empty unless a commissioning window is open AND the passcode is
  // currently known to the device (see docs/commissioning.md).
  std::string qr_code;
  std::string manual_code;
};

class MatterCommissioning {
public:
  static MatterCommissioning &instance();

  // Main loop, before esp_matter::start(): loads or creates the commissioning
  // identity, migrates legacy NVS data and installs the custom
  // CommissionableDataProvider. Returns false on fatal errors.
  bool init(bool persist_passcode);

  // CHIP task: called from the Matter device event callback.
  void handle_event(const ChipDeviceEvent *event);

  // Main loop: logs the QR/manual code (only while a window is open and the
  // code is known) and the window/fabric summary.
  void dump_config();

  // Any thread. Opens a commissioning window protected by a fresh random
  // one-time passcode. If the device is not commissioned yet, the basic window
  // is re-opened instead (with a fresh passcode if the current one is unknown).
  void open_window(uint32_t timeout_s);

  // Main loop. Removes all fabrics plus esp-matter and CHIP persistent data,
  // then reboots. The commissioning identity (salt, verifier, discriminator) is
  // kept; without a known passcode the next boot derives a fresh one.
  void factory_reset();
  // Like factory_reset() but also erases the commissioning identity, so a new
  // passcode and discriminator are generated on the next boot.
  void reset_identity();

  const CommissioningState &state() const { return this->state_; }
  void add_state_callback(std::function<void()> &&cb) {
    this->state_callbacks_.push_back(std::move(cb));
  }
  void add_commissioned_callback(std::function<void()> &&cb) {
    this->commissioned_callbacks_.push_back(std::move(cb));
  }
  void add_decommissioned_callback(std::function<void()> &&cb) {
    this->decommissioned_callbacks_.push_back(std::move(cb));
  }

protected:
  friend class MatterCommissionableDataProvider;

  static constexpr size_t MAX_SALT_LEN = 32;
  static constexpr size_t VERIFIER_LEN = 97; // kSpake2p_VerifierSerialized_Length

  struct Identity {
    uint16_t discriminator{0};
    uint32_t iterations{0};
    uint8_t salt[MAX_SALT_LEN]{};
    size_t salt_len{0};
    uint8_t verifier[VERIFIER_LEN]{};
    size_t verifier_len{0};
    // Normally only kept in RAM (see persist_passcode).
    bool passcode_known{false};
    uint32_t passcode{0};
  };

  bool generate_passcode_(Identity &id);
  bool store_identity_(const Identity &id);
  void on_server_ready_();
  void on_fabric_removed_();
  void refresh_(bool log_codes, bool publish = true);
  bool build_codes_(uint32_t passcode, uint16_t discriminator, bool ecm,
                    std::string &qr, std::string &manual) const;
  void erase_identity_keys_();

  Identity identity_;
  bool persist_passcode_{false};
  bool fixed_passcode_{false};
  // Set (main loop) once a factory reset was requested. The CHIP task then
  // leaves the identity alone: the fabric removal events of the reset must not
  // regenerate or re-store what reset_identity() just erased.
  std::atomic<bool> resetting_{false};

  // One-time passcode of a window opened by open_window().
  bool ephemeral_active_{false};
  uint32_t ephemeral_passcode_{0};
  uint16_t ephemeral_discriminator_{0};

  CommissioningState state_;
  std::vector<std::function<void()>> state_callbacks_;
  std::vector<std::function<void()>> commissioned_callbacks_;
  std::vector<std::function<void()>> decommissioned_callbacks_;
};

class MatterCommissionedTrigger : public Trigger<> {
public:
  MatterCommissionedTrigger() {
    MatterCommissioning::instance().add_commissioned_callback(
        [this]() { this->trigger(); });
  }
};

class MatterDecommissionedTrigger : public Trigger<> {
public:
  MatterDecommissionedTrigger() {
    MatterCommissioning::instance().add_decommissioned_callback(
        [this]() { this->trigger(); });
  }
};

template <typename... Ts>
class MatterOpenCommissioningWindowAction : public Action<Ts...> {
public:
  TEMPLATABLE_VALUE(uint32_t, timeout)
  void play(const Ts &...x) override {
    // Matter spec 11.19.7: window length between 3 and 15 minutes.
    uint32_t t = this->timeout_.value(x...);
    t = t < 180 ? 180 : (t > 900 ? 900 : t);
    MatterCommissioning::instance().open_window(t);
  }
};

template <typename... Ts>
class MatterResetIdentityAction : public Action<Ts...> {
public:
  void play(const Ts &...x) override {
    MatterCommissioning::instance().reset_identity();
  }
};

} // namespace esphome::matter

#endif // USE_MATTER
