#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_MATTER_MODES)

#include "esphome/core/automation.h"
#include "matter_delegates.h"
#include "matter_endpoints.h"
#include "matter_sync_bindings.h"
#ifdef USE_SELECT
#include "esphome/components/select/select.h"
#endif

#include <app/clusters/mode-base-server/mode-base-server.h>
#include <app/clusters/mode-select-server/supported-modes-manager.h>
#include <esp_matter.h>

#include <cstdint>
#include <string>
#include <vector>

namespace esphome::matter {

// A semantic / mode tag. `has_mfg_code` selects the manufacturer-specific form (ModeTagStruct.MfgCode present).
struct MatterModeTag {
  uint16_t value{0};
  uint16_t mfg_code{0};
  bool has_mfg_code{false};
};

struct MatterModeEntry {
  std::string label;
  uint8_t mode{0};
  // Option of the bound ESPHome select that stands for this mode (defaults to the label).
  std::string option;
  std::vector<MatterModeTag> tags;
};

// Trigger for `on_change_to_mode`: fired on the main loop with the requested mode number.
class MatterChangeToModeTrigger : public Trigger<uint8_t> {};

// Mode list, ESPHome select binding and change trigger shared by the Mode Base derived clusters and Mode Select.
// A MatterModeBinding (binding framework): pushes of CurrentMode are coalesced and echo-suppressed by the framework.
//
// Matter -> ESPHome: an accepted ChangeToMode sets the bound select and runs `on_change_to_mode` on the main loop.
// ESPHome -> Matter: the bound select (and `matter.mode.set`) updates CurrentMode.
// Boot: the select wins when it has a (restored) state; otherwise the stored CurrentMode is replayed into the select.
class MatterModeList : public MatterModeBinding {
public:
  MatterModeList(uint16_t endpoint_id, uint32_t cluster_id);

  uint32_t cluster_id() const { return this->cluster_id_; }

  // Codegen: add_mode() starts a new mode, add_tag() appends to the last one.
  void add_mode(const char *label, uint8_t mode, const char *option);
  void add_tag(uint16_t value, uint16_t mfg_code, bool has_mfg_code);
  void add_change_trigger(MatterChangeToModeTrigger *trigger) {
    this->triggers_.push_back(trigger);
  }
  // decide_in_automation: the CHIP task waits up to `timeout_ms` for the automation and honours matter.mode.reject.
  void set_decision(bool decide_in_automation, uint32_t timeout_ms) {
    this->decide_in_automation_ = decide_in_automation;
    this->decide_timeout_ms_ = timeout_ms;
  }
  // accept: when_idle: ChangeToMode is refused while the operational state of the endpoint is not idle.
  void set_accept_when_idle(bool value) { this->accept_when_idle_ = value; }
#ifdef USE_SELECT
  void set_select(select::Select *select) { this->select_ = select; }
#endif

  const std::vector<MatterModeEntry> &modes() const { return this->modes_; }
  const MatterModeEntry *find_mode(uint8_t mode) const;

  // Main loop: sets CurrentMode in the Matter data model. The mode must be one of the configured modes.
  void set_current_mode(uint8_t mode);

protected:
  void setup_() override;
  // Main loop: select update and user automation for a mode change (silent: boot replay, no automation).
  void deliver_(uint8_t mode, bool silent) override;
  // Main loop: the mode the bound select shows (-1: no select or no state).
  int entity_mode_() override;
  // Main loop: select update and user automation for a change accepted in the blocking decide_in_automation path.
  void apply_change_(uint8_t mode, MatterCommandOutcome &outcome);
  // Main loop: the bound select changed.
  void on_select_state_();

  uint32_t cluster_id_;
  std::vector<MatterModeEntry> modes_;
  std::vector<MatterChangeToModeTrigger *> triggers_;
  bool decide_in_automation_{false};
  uint32_t decide_timeout_ms_{MATTER_DELEGATE_TIMEOUT_MS};
  bool accept_when_idle_{false};
#ifdef USE_SELECT
  select::Select *select_{nullptr};
#endif
};

// Delegate of a Mode Base derived cluster (Laundry Washer Mode, RVC Run Mode, ...).
//
// ChangeToMode runs on the CHIP task. Without trigger and select the change is accepted. With them, the answer
// depends on `matter.mode.reject` in the trigger (see matter_delegates.h).
class MatterModeMapping : public MatterModeList,
                          public chip::app::Clusters::ModeBase::Delegate {
public:
  MatterModeMapping(uint16_t endpoint_id, uint32_t cluster_id);

  // The pointer to hand to esp-matter (as the type its init callback casts to).
  chip::app::Clusters::ModeBase::Delegate *delegate() { return this; }

  static MatterModeMapping *find(uint16_t endpoint_id, uint32_t cluster_id);

  // ModeBase::Delegate
  CHIP_ERROR Init() override { return CHIP_NO_ERROR; }
  CHIP_ERROR GetModeLabelByIndex(uint8_t mode_index,
                                 chip::MutableCharSpan &label) override;
  CHIP_ERROR GetModeValueByIndex(uint8_t mode_index, uint8_t &value) override;
  CHIP_ERROR GetModeTagsByIndex(
      uint8_t mode_index,
      chip::app::DataModel::List<
          chip::app::Clusters::detail::Structs::ModeTagStruct::Type> &tags)
      override;
  void HandleChangeToMode(
      uint8_t new_mode,
      chip::app::Clusters::ModeBase::Commands::ChangeToModeResponse::Type
          &response) override;

protected:
  // CHIP task, inside the PushScope.
  void write_mode_(uint8_t mode) override;
  bool read_matter_mode_(uint8_t &mode) override;
  chip::app::Clusters::ModeBase::Instance *instance_();

  // Kept alive until the CHIP task has encoded the response.
  std::string status_text_;
};

// Mode Select cluster. All Mode Select endpoints share one SupportedModesManager (a connectedhomeip singleton).
// The SDK handles ChangeToMode itself (only UnsupportedMode can be answered); accepted changes (and scene recalls)
// arrive through the CurrentMode attribute update, so `matter.mode.reject` has no effect here.
class MatterModeSelectMapping : public MatterModeList {
public:
  MatterModeSelectMapping(uint16_t endpoint_id);

  static MatterModeSelectMapping *find(uint16_t endpoint_id);

  // Builds the SDK structures for SupportedModes (once, before esp_matter::start).
  void finalize();
  chip::app::Clusters::ModeSelect::SupportedModesManager::ModeOptionsProvider
  options() const;

protected:
  void setup_() override;
  // CHIP task: the store update of a controller or a scene recall. The framework drops the updates caused by our
  // own writes (PushScope), so no echo bookkeeping is needed.
  void on_attribute_(uint32_t cluster, uint32_t attribute,
                     const esp_matter_attr_val_t &v) override;
  void write_mode_(uint8_t mode) override;
  bool read_matter_mode_(uint8_t &mode) override;
  // A change that only repeats what the select shows has no command to run.
  bool drop_unchanged_inbound_() const override { return true; }

  std::vector<chip::app::Clusters::ModeSelect::Structs::ModeOptionStruct::Type>
      options_;
  std::vector<
      std::vector<chip::app::Clusters::ModeSelect::Structs::SemanticTagStruct::Type>>
      semantic_tags_;
};

// Main loop: sets CurrentMode of the mode cluster configured in YAML on an endpoint (logs when unknown).
void matter_mode_set(uint16_t endpoint_id, uint32_t cluster_id, uint8_t mode);
// Main loop: vetoes the ChangeToMode that is being handled (no effect outside `on_change_to_mode`).
void matter_mode_reject(uint8_t status, const std::string &text);

// Action `matter.mode.set`: sets CurrentMode of a mode cluster configured in YAML.
template <typename... Ts> class MatterModeSetAction : public Action<Ts...> {
public:
  void set_endpoint_id(uint16_t endpoint_id) {
    this->endpoint_id_ = endpoint_id;
  }
  void set_cluster_id(uint32_t cluster_id) { this->cluster_id_ = cluster_id; }
  TEMPLATABLE_VALUE(uint8_t, mode)

  void play(const Ts &...x) override {
    matter_mode_set(this->endpoint_id_, this->cluster_id_, this->mode_.value(x...));
  }

protected:
  uint16_t endpoint_id_{0};
  uint32_t cluster_id_{0};
};

// Action `matter.mode.reject`: vetoes the ChangeToMode that is being handled by the surrounding
// `on_change_to_mode` automation.
template <typename... Ts> class MatterModeRejectAction : public Action<Ts...> {
public:
  TEMPLATABLE_VALUE(uint8_t, status)
  TEMPLATABLE_VALUE(std::string, text)

  void play(const Ts &...x) override {
    matter_mode_reject(this->status_.value(x...),
                       this->text_.has_value() ? this->text_.value(x...)
                                               : std::string());
  }
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_MATTER_MODES
