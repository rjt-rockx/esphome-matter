#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_MATTER_MODES)

#include "esphome/core/log.h"
#include "matter_attributes.h"
#include "matter_component.h"
#include "matter_modes.h"

#include <clusters/ModeSelect/Attributes.h>
#include <clusters/ModeSelect/ClusterId.h>
#include <esp_matter_cluster.h>
#include <esp_matter_command.h>
#include <esp_matter_data_model.h>
#include <esp_matter_delegate_callbacks.h>
#include <lib/support/Span.h>

#include <algorithm>

static const char *const TAG = "matter.mode";

namespace esphome::matter {

namespace {

namespace mode_base_ns = chip::app::Clusters::ModeBase;
namespace mode_select_ns = chip::app::Clusters::ModeSelect;
using InteractionStatus = chip::Protocols::InteractionModel::Status;

// Cluster ids of the Mode Base derived clusters that have an esp-matter delegate init callback.
constexpr uint32_t MODE_SELECT_ID = 0x0050;

struct ModeClusterInfo {
  uint32_t id;
  esp_matter::cluster::delegate_init_callback_t init_callback;
};

const ModeClusterInfo *mode_cluster_info(uint32_t cluster_id) {
  using namespace esp_matter::cluster::delegate_cb;
  static const ModeClusterInfo infos[] = {
      {0x0051, LaundryWasherModeDelegateInitCB},
      {0x0052, RefrigeratorAndTCCModeDelegateInitCB},
      {0x0054, RvcRunModeDelegateInitCB},
      {0x0055, RvcCleanModeDelegateInitCB},
      {0x0059, DishWasherModeDelegateInitCB},
      {0x005E, MicrowaveOvenModeDelegateInitCB},
      {0x009D, EnergyEvseModeDelegateInitCB},
      {0x009E, WaterHeaterModeDelegateInitCB},
      {0x009F, DeviceEnergyManagementModeDelegateInitCB},
  };
  for (const auto &info : infos) {
    if (info.id == cluster_id)
      return &info;
  }
  return nullptr;
}

// Mappings are registered during setup() and never removed, so the CHIP task
// can safely read these afterwards.
std::vector<MatterModeMapping *> &mode_mappings() {
  static std::vector<MatterModeMapping *> mappings;
  return mappings;
}

std::vector<MatterModeSelectMapping *> &mode_select_mappings() {
  static std::vector<MatterModeSelectMapping *> mappings;
  return mappings;
}

constexpr size_t MODE_STATUS_TEXT_MAX = 64;

#ifdef USE_MATTER_MODE_SELECT
class MatterModeSelectManager : public mode_select_ns::SupportedModesManager {
public:
  ModeOptionsProvider getModeOptionsProvider(chip::EndpointId endpoint_id) const override {
    auto *mapping = MatterModeSelectMapping::find(endpoint_id);
    return mapping != nullptr ? mapping->options() : ModeOptionsProvider();
  }

  InteractionStatus getModeOptionByMode(
      chip::EndpointId endpoint_id, uint8_t mode,
      const mode_select_ns::Structs::ModeOptionStruct::Type **data) const override {
    auto *mapping = MatterModeSelectMapping::find(endpoint_id);
    if (mapping == nullptr)
      return InteractionStatus::UnsupportedCluster;
    auto provider = mapping->options();
    for (auto *it = provider.begin(); it != provider.end(); ++it) {
      if (it->mode == mode) {
        *data = it;
        return InteractionStatus::Success;
      }
    }
    return InteractionStatus::ConstraintError;
  }
};

MatterModeSelectManager &mode_select_manager() {
  static MatterModeSelectManager manager;
  return manager;
}
#endif // USE_MATTER_MODE_SELECT

} // namespace

// ---------------------------------------------------------------- MatterModeList

MatterModeList::MatterModeList(uint16_t endpoint_id, uint32_t cluster_id)
    : MatterModeBinding(endpoint_id), cluster_id_(cluster_id) {}

void MatterModeList::add_mode(const char *label, uint8_t mode,
                              const char *option) {
  MatterModeEntry entry;
  entry.label = label;
  entry.mode = mode;
  entry.option = option;
  this->modes_.push_back(std::move(entry));
}

void MatterModeList::add_tag(uint16_t value, uint16_t mfg_code,
                             bool has_mfg_code) {
  if (this->modes_.empty())
    return;
  this->modes_.back().tags.push_back({value, mfg_code, has_mfg_code});
}

const MatterModeEntry *MatterModeList::find_mode(uint8_t mode) const {
  for (const auto &entry : this->modes_) {
    if (entry.mode == mode)
      return &entry;
  }
  return nullptr;
}

void MatterModeList::setup_() {
#ifdef USE_SELECT
  if (this->select_ != nullptr)
    this->select_->add_on_state_callback(
        [this](auto &&...) { this->on_select_state_(); });
#endif
  // Boot rule: the select wins when it has a (restored) state and is pushed to Matter; otherwise the stored
  // CurrentMode is shown in the select. Decided on the main loop so the select can be read safely.
  this->start_boot_sync_();
}

int MatterModeList::entity_mode_() {
#ifdef USE_SELECT
  if (this->select_ == nullptr || !this->select_->has_state())
    return -1;
  auto index = this->select_->active_index();
  if (!index.has_value())
    return -1;
  auto option = this->select_->at(index.value());
  if (!option.has_value())
    return -1;
  for (const auto &entry : this->modes_) {
    if (entry.option == option.value())
      return entry.mode;
  }
#endif
  return -1;
}

// Main loop. Shows `mode` in the select and, unless this replays what Matter already holds, runs the automation.
void MatterModeList::deliver_(uint8_t mode, bool silent) {
  if (!silent) {
    for (auto *trigger : this->triggers_)
      trigger->trigger(mode);
  }
#ifdef USE_SELECT
  if (this->select_ != nullptr) {
    const MatterModeEntry *entry = this->find_mode(mode);
    if (entry != nullptr)
      this->select_->make_call().set_option(entry->option).perform();
  }
#endif
}

// Main loop, blocking decide_in_automation path: the automation may veto the mode.
void MatterModeList::apply_change_(uint8_t mode, MatterCommandOutcome &outcome) {
  for (auto *trigger : this->triggers_)
    trigger->trigger(mode);
  if (outcome.rejected)
    return;
#ifdef USE_SELECT
  if (this->select_ != nullptr) {
    const MatterModeEntry *entry = this->find_mode(mode);
    if (entry != nullptr)
      this->select_->make_call().set_option(entry->option).perform();
  }
#endif
  // The SDK writes the accepted mode itself; refresh the outbound slot so an older snapshot cannot overwrite it.
  this->set_mode_(mode);
}

void MatterModeList::on_select_state_() {
#ifdef USE_SELECT
  auto index = this->select_->active_index();
  if (!index.has_value())
    return;
  auto option = this->select_->at(index.value());
  if (!option.has_value())
    return;
  for (const auto &entry : this->modes_) {
    if (entry.option != option.value())
      continue;
    if (static_cast<int>(entry.mode) == this->last_mode_)
      return;
    ESP_LOGD(TAG, "Select '%s' -> mode %u on endpoint %u cluster 0x%04x",
             option.value().c_str(), entry.mode, this->endpoint_id(),
             static_cast<unsigned>(this->cluster_id_));
    this->entity_mode_changed_(entry.mode);
    return;
  }
  ESP_LOGW(TAG, "Select option '%s' is not one of the modes of endpoint %u "
                "cluster 0x%04x",
           option.value().c_str(), this->endpoint_id(),
           static_cast<unsigned>(this->cluster_id_));
#endif
}

void MatterModeList::set_current_mode(uint8_t mode) {
  const MatterModeEntry *entry = this->find_mode(mode);
  if (entry == nullptr) {
    ESP_LOGW(TAG, "Mode %u is not a supported mode of endpoint %u cluster 0x%04x",
             mode, this->endpoint_id(), static_cast<unsigned>(this->cluster_id_));
    return;
  }
  // Always pushed, also when the mode did not change: matter.mode.set is an explicit write.
  this->set_mode_(mode);
#ifdef USE_SELECT
  if (this->select_ != nullptr)
    this->select_->make_call().set_option(entry->option).perform();
#endif
}

// ------------------------------------------------------------ MatterModeMapping

MatterModeMapping::MatterModeMapping(uint16_t endpoint_id, uint32_t cluster_id)
    : MatterModeList(endpoint_id, cluster_id) {
  mode_mappings().push_back(this);
}

MatterModeMapping *MatterModeMapping::find(uint16_t endpoint_id,
                                           uint32_t cluster_id) {
  for (auto *mapping : mode_mappings()) {
    if (mapping->endpoint_id() == endpoint_id &&
        mapping->cluster_id() == cluster_id)
      return mapping;
  }
  return nullptr;
}

CHIP_ERROR MatterModeMapping::GetModeLabelByIndex(uint8_t mode_index,
                                                  chip::MutableCharSpan &label) {
  if (mode_index >= this->modes_.size())
    return CHIP_ERROR_PROVIDER_LIST_EXHAUSTED;
  return chip::CopyCharSpanToMutableCharSpan(
      chip::CharSpan::fromCharString(this->modes_[mode_index].label.c_str()),
      label);
}

CHIP_ERROR MatterModeMapping::GetModeValueByIndex(uint8_t mode_index,
                                                  uint8_t &value) {
  if (mode_index >= this->modes_.size())
    return CHIP_ERROR_PROVIDER_LIST_EXHAUSTED;
  value = this->modes_[mode_index].mode;
  return CHIP_NO_ERROR;
}

CHIP_ERROR MatterModeMapping::GetModeTagsByIndex(
    uint8_t mode_index,
    chip::app::DataModel::List<
        chip::app::Clusters::detail::Structs::ModeTagStruct::Type> &tags) {
  if (mode_index >= this->modes_.size())
    return CHIP_ERROR_PROVIDER_LIST_EXHAUSTED;
  const auto &source = this->modes_[mode_index].tags;
  if (tags.size() < source.size())
    return CHIP_ERROR_INVALID_ARGUMENT;
  for (size_t i = 0; i < source.size(); i++) {
    tags[i].value = source[i].value;
    if (source[i].has_mfg_code)
      tags[i].mfgCode.SetValue(static_cast<chip::VendorId>(source[i].mfg_code));
    else
      tags[i].mfgCode.ClearValue();
  }
  tags.reduce_size(source.size());
  return CHIP_NO_ERROR;
}

// Called on the CHIP task.
void MatterModeMapping::HandleChangeToMode(
    uint8_t new_mode, mode_base_ns::Commands::ChangeToModeResponse::Type &response) {
  this->status_text_.clear();
  response.status = chip::to_underlying(mode_base_ns::StatusCode::kSuccess);

  bool has_handler = !this->triggers_.empty();
#ifdef USE_SELECT
  has_handler = has_handler || this->select_ != nullptr;
#endif

  // Declarative rule, decided here without waiting for the main loop.
#ifdef USE_MATTER_OPERATIONAL_STATE
  if (this->accept_when_idle_ && !endpoint_is_idle(this->endpoint_id())) {
    response.status =
        chip::to_underlying(mode_base_ns::StatusCode::kInvalidInMode);
    this->status_text_ = "Busy";
  } else
#endif
      if (has_handler && this->decide_in_automation_) {
    // Opt-in: the automation decides (matter.mode.reject), the CHIP task waits for it.
    MatterCommandOutcome outcome;
    bool answered = run_on_main_loop_and_wait(
        [this, new_mode](MatterCommandOutcome &result) {
          this->apply_change_(new_mode, result);
        },
        outcome, this->decide_timeout_ms_,
        // Finished after the controller got a failure: if nothing vetoed it, bring Matter in line with the select.
        [this, new_mode](const MatterCommandOutcome &late) {
          if (!late.rejected) {
            ESP_LOGW(TAG, "ChangeToMode(%u) finished late; applying it to Matter", new_mode);
            this->set_mode_(new_mode);
          }
        });
    if (!answered) {
      response.status =
          chip::to_underlying(mode_base_ns::StatusCode::kGenericFailure);
      this->status_text_ = "Timed out";
    } else if (outcome.rejected) {
      response.status = outcome.status;
      this->status_text_ = outcome.text;
    }
  } else if (has_handler) {
    // Default: accept and let the automation/select follow asynchronously on the main loop. The framework queues
    // the change (every accepted command runs the automation) and drops a stale push of the select.
    this->matter_mode_changed_(new_mode);
  }

  ESP_LOGD(TAG, "ChangeToMode(%u) on endpoint %u cluster 0x%04x: status %u",
           new_mode, this->endpoint_id(), static_cast<unsigned>(this->cluster_id_),
           response.status);
  if (response.status !=
      chip::to_underlying(mode_base_ns::StatusCode::kSuccess)) {
    // StatusText is always present on a failure.
    if (this->status_text_.empty())
      this->status_text_ = "Rejected";
    this->status_text_.resize(
        utf8_prefix_length(this->status_text_, MODE_STATUS_TEXT_MAX));
    response.statusText.SetValue(
        chip::CharSpan::fromCharString(this->status_text_.c_str()));
  }
}

mode_base_ns::Instance *MatterModeMapping::instance_() {
  auto *cluster =
      esp_matter::cluster::get(this->endpoint_id(), this->cluster_id_);
  if (cluster == nullptr)
    return nullptr;
  return static_cast<mode_base_ns::Instance *>(
      esp_matter::cluster::get_delegate_managed_instance(cluster));
}

// CHIP task, inside the PushScope.
void MatterModeMapping::write_mode_(uint8_t mode) {
  auto *instance = this->instance_();
  if (instance == nullptr) {
    ESP_LOGW(TAG, "Mode cluster 0x%04x on endpoint %u is not running",
             static_cast<unsigned>(this->cluster_id_), this->endpoint_id());
    return;
  }
  auto status = instance->UpdateCurrentMode(mode);
  if (status != InteractionStatus::Success)
    ESP_LOGW(TAG, "Cannot set mode %u on endpoint %u cluster 0x%04x", mode,
             this->endpoint_id(), static_cast<unsigned>(this->cluster_id_));
}

// CHIP task.
bool MatterModeMapping::read_matter_mode_(uint8_t &mode) {
  auto *instance = this->instance_();
  if (instance == nullptr)
    return false;
  mode = instance->GetCurrentMode();
  return true;
}

// ------------------------------------------------------- MatterModeSelectMapping

MatterModeSelectMapping::MatterModeSelectMapping(uint16_t endpoint_id)
    : MatterModeList(endpoint_id, MODE_SELECT_ID) {
  mode_select_mappings().push_back(this);
}

MatterModeSelectMapping *MatterModeSelectMapping::find(uint16_t endpoint_id) {
  for (auto *mapping : mode_select_mappings()) {
    if (mapping->endpoint_id() == endpoint_id)
      return mapping;
  }
  return nullptr;
}

void MatterModeSelectMapping::finalize() {
  if (!this->options_.empty())
    return;
  this->semantic_tags_.reserve(this->modes_.size());
  for (const auto &entry : this->modes_) {
    std::vector<mode_select_ns::Structs::SemanticTagStruct::Type> tags;
    for (const auto &tag : entry.tags) {
      mode_select_ns::Structs::SemanticTagStruct::Type semantic_tag;
      semantic_tag.mfgCode = static_cast<chip::VendorId>(tag.mfg_code);
      semantic_tag.value = tag.value;
      tags.push_back(semantic_tag);
    }
    this->semantic_tags_.push_back(std::move(tags));
  }
  this->options_.reserve(this->modes_.size());
  for (size_t i = 0; i < this->modes_.size(); i++) {
    mode_select_ns::Structs::ModeOptionStruct::Type option;
    option.label = chip::CharSpan::fromCharString(this->modes_[i].label.c_str());
    option.mode = this->modes_[i].mode;
    option.semanticTags =
        chip::app::DataModel::List<const mode_select_ns::Structs::SemanticTagStruct::Type>(
            this->semantic_tags_[i].data(), this->semantic_tags_[i].size());
    this->options_.push_back(option);
  }
}

mode_select_ns::SupportedModesManager::ModeOptionsProvider
MatterModeSelectMapping::options() const {
  return mode_select_ns::SupportedModesManager::ModeOptionsProvider(
      this->options_.data(), this->options_.data() + this->options_.size());
}

void MatterModeSelectMapping::setup_() {
  MatterModeList::setup_();
  // CurrentMode is written by the SDK when a controller changes it (ChangeToMode) and by scene recalls. The updates
  // caused by our own writes never reach on_attribute_().
  this->watch_attribute(MODE_SELECT_ID, mode_select_ns::Attributes::CurrentMode::Id);
}

// CHIP task.
void MatterModeSelectMapping::on_attribute_(uint32_t cluster, uint32_t attribute,
                                            const esp_matter_attr_val_t &v) {
  if (cluster == MODE_SELECT_ID &&
      attribute == mode_select_ns::Attributes::CurrentMode::Id)
    this->matter_mode_changed_(v.val.u8);
}

// CHIP task, inside the PushScope.
void MatterModeSelectMapping::write_mode_(uint8_t mode) {
  esp_matter_attr_val_t value = esp_matter_uint8(mode);
  esp_err_t err = binding_platform()->write_attribute(
      this->endpoint_id(), MODE_SELECT_ID,
      mode_select_ns::Attributes::CurrentMode::Id, value, false);
  if (err != ESP_OK)
    ESP_LOGW(TAG, "Cannot set mode %u on endpoint %u: %d", mode,
             this->endpoint_id(), static_cast<int>(err));
}

// CHIP task.
bool MatterModeSelectMapping::read_matter_mode_(uint8_t &mode) {
  esp_matter_attr_val_t value;
  if (esp_matter::attribute::get_val(this->endpoint_id(), MODE_SELECT_ID,
                                     mode_select_ns::Attributes::CurrentMode::Id,
                                     &value) != ESP_OK)
    return false;
  mode = value.val.u8;
  return true;
}

// --------------------------------------------------------------------- actions

void matter_mode_set(uint16_t endpoint_id, uint32_t cluster_id, uint8_t mode) {
  MatterModeList *list = nullptr;
  if (cluster_id == MODE_SELECT_ID)
    list = MatterModeSelectMapping::find(endpoint_id);
  else
    list = MatterModeMapping::find(endpoint_id, cluster_id);
  if (list == nullptr) {
    ESP_LOGW(TAG, "No mode cluster 0x%04x configured on endpoint %u",
             static_cast<unsigned>(cluster_id), endpoint_id);
    return;
  }
  list->set_current_mode(mode);
}

void matter_mode_reject(uint8_t status, const std::string &text) {
  MatterCommandOutcome *outcome = active_command_outcome();
  if (outcome == nullptr) {
    ESP_LOGW(TAG, "matter.mode.reject ignored: it only works in "
                  "on_change_to_mode of a cluster with decide_in_automation: "
                  "true, before any delay");
    return;
  }
  outcome->rejected = true;
  outcome->status = status;
  outcome->text = text;
}

// ---------------------------------------------------------------------- attach

DelegateAttach attach_mode_delegate(esp_matter::endpoint_t *endpoint,
                                    uint32_t cluster_id) {
  const uint16_t endpoint_id = esp_matter::endpoint::get_id(endpoint);
  const bool is_select = cluster_id == MODE_SELECT_ID;
  const ModeClusterInfo *info = mode_cluster_info(cluster_id);
  if (!is_select && info == nullptr)
    return DelegateAttach::NOT_HANDLED;

  esp_matter::cluster_t *cluster = esp_matter::cluster::get(endpoint, cluster_id);
  if (cluster == nullptr) {
    ESP_LOGE(TAG, "Mode cluster 0x%04x missing on endpoint %u",
             static_cast<unsigned>(cluster_id), endpoint_id);
    return DelegateAttach::FAILED;
  }

  if (is_select) {
#ifdef USE_MATTER_MODE_SELECT
    auto *mapping = MatterModeSelectMapping::find(endpoint_id);
    if (mapping == nullptr)
      return DelegateAttach::NOT_HANDLED;
    mapping->finalize();
    // Only referenced when a Mode Select is configured: ModeSelectDelegateInitCB needs the mode-select-server sources,
    // which esp-matter builds only for CONFIG_SUPPORT_MODE_SELECT_CLUSTER.
    if (esp_matter::cluster::set_delegate_and_init_callback(
            cluster, esp_matter::cluster::delegate_cb::ModeSelectDelegateInitCB,
            static_cast<mode_select_ns::SupportedModesManager *>(
                &mode_select_manager())) != ESP_OK)
      return DelegateAttach::FAILED;
    return DelegateAttach::OK;
#else
    return DelegateAttach::NOT_HANDLED;
#endif
  }

  auto *mapping = MatterModeMapping::find(endpoint_id, cluster_id);
  if (mapping == nullptr)
    return DelegateAttach::NOT_HANDLED;

  // esp-matter creates ChangeToMode (and, for some clusters, SupportedModes) only in some mode cluster creators; the
  // commands are served by the ModeBase::Instance once the delegate is installed, but they must be in the data model to
  // be listed in AcceptedCommandList / GeneratedCommandList.
  using namespace esp_matter;
  if (attribute::get(cluster, mode_base_ns::Attributes::SupportedModes::Id) ==
      nullptr)
    cluster::mode_base::attribute::create_supported_modes(cluster, nullptr, 0, 0);
  // Microwave Oven Mode disallows ChangeToMode and ChangeToModeResponse (Mode_MicrowaveOven.xml): its mode is changed
  // through Microwave Oven Control (SetCookingParameters), so the commands are not added there.
  if (cluster_id != 0x005E) {
    if (command::get(cluster, mode_base_ns::Commands::ChangeToMode::Id,
                     COMMAND_FLAG_ACCEPTED) == nullptr)
      cluster::mode_base::command::create_change_to_mode(cluster);
    if (command::get(cluster, mode_base_ns::Commands::ChangeToModeResponse::Id,
                     COMMAND_FLAG_GENERATED) == nullptr)
      cluster::mode_base::command::create_change_to_mode_response(cluster);
  }

  // On a microwave oven the Microwave Oven Control cluster creates (and Init()s) the mode instance itself once it
  // has a delegate; installing our own init callback as well would Init the instance twice. Only hand over the
  // delegate pointer then.
  esp_matter::cluster::delegate_init_callback_t init_callback = info->init_callback;
  if (cluster_id == 0x005E) {
    esp_matter::cluster_t *control = esp_matter::cluster::get(endpoint, 0x005F);
    if (control != nullptr &&
        esp_matter::cluster::get_delegate_impl(control) != nullptr)
      init_callback = nullptr;
  }
  if (cluster::set_delegate_and_init_callback(
          cluster, init_callback,
          static_cast<mode_base_ns::Delegate *>(mapping)) != ESP_OK)
    return DelegateAttach::FAILED;
  return DelegateAttach::OK;
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_MATTER_MODES
