#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_MATTER_LAUNDRY_CONTROLS)

#include "esphome/core/log.h"
#include "matter_attributes.h"
#include "matter_component.h"
#include "matter_laundry_controls.h"

#include <clusters/LaundryWasherControls/Attributes.h>
#include <clusters/LaundryWasherControls/ClusterId.h>
#include <esp_matter_cluster.h>
#include <esp_matter_data_model.h>
#include <esp_matter_delegate_callbacks.h>
#include <lib/support/Span.h>

static const char *const TAG = "matter.laundry";

namespace esphome::matter {

namespace {

namespace controls_ns = chip::app::Clusters::LaundryWasherControls;

constexpr uint32_t LAUNDRY_WASHER_CONTROLS_ID = 0x0053;

std::vector<MatterLaundryControlsMapping *> &laundry_mappings() {
  static std::vector<MatterLaundryControlsMapping *> mappings;
  return mappings;
}

} // namespace

MatterLaundryControlsMapping::MatterLaundryControlsMapping(uint16_t endpoint_id)
    : MatterEndpointMappingBase(endpoint_id) {
  laundry_mappings().push_back(this);
}

MatterLaundryControlsMapping *
MatterLaundryControlsMapping::find(uint16_t endpoint_id) {
  for (auto *mapping : laundry_mappings()) {
    if (mapping->endpoint_id() == endpoint_id)
      return mapping;
  }
  return nullptr;
}

void MatterLaundryControlsMapping::add_spin_speed(const char *spin_speed) {
  this->spin_speeds_.push_back(spin_speed);
}

void MatterLaundryControlsMapping::add_rinse(uint8_t value, const char *name) {
  this->rinses_.push_back({value, name});
}

CHIP_ERROR MatterLaundryControlsMapping::GetSpinSpeedAtIndex(
    size_t index, chip::MutableCharSpan &spin_speed) {
  if (index >= this->spin_speeds_.size())
    return CHIP_ERROR_PROVIDER_LIST_EXHAUSTED;
  return chip::CopyCharSpanToMutableCharSpan(
      chip::CharSpan::fromCharString(this->spin_speeds_[index].c_str()),
      spin_speed);
}

CHIP_ERROR MatterLaundryControlsMapping::GetSupportedRinseAtIndex(
    size_t index, controls_ns::NumberOfRinsesEnum &supported_rinse) {
  if (index >= this->rinses_.size())
    return CHIP_ERROR_PROVIDER_LIST_EXHAUSTED;
  supported_rinse =
      static_cast<controls_ns::NumberOfRinsesEnum>(this->rinses_[index].value);
  return CHIP_NO_ERROR;
}

void MatterLaundryControlsMapping::initialize() {
#ifdef USE_SELECT
  if (this->spin_speed_select_ != nullptr) {
    this->spin_speed_binding_ = new MatterLaundrySelectBinding(
        this, MatterLaundrySelectBinding::Kind::SPIN_SPEED,
        this->spin_speed_select_);
    this->spin_speed_binding_->initialize();
  }
  if (this->rinses_select_ != nullptr) {
    this->rinses_binding_ = new MatterLaundrySelectBinding(
        this, MatterLaundrySelectBinding::Kind::RINSES, this->rinses_select_);
    this->rinses_binding_->initialize();
  }
#endif
}

#ifdef USE_SELECT
bool MatterLaundryControlsMapping::option_to_value(
    MatterLaundrySelectBinding::Kind kind, const std::string &option,
    int32_t &value) const {
  if (kind == MatterLaundrySelectBinding::Kind::SPIN_SPEED) {
    for (size_t i = 0; i < this->spin_speeds_.size(); i++) {
      if (this->spin_speeds_[i] == option) {
        value = static_cast<int32_t>(i);
        return true;
      }
    }
    return false;
  }
  for (const auto &rinse : this->rinses_) {
    if (rinse.name == option) {
      value = rinse.value;
      return true;
    }
  }
  return false;
}

const std::string *MatterLaundryControlsMapping::value_to_option(
    MatterLaundrySelectBinding::Kind kind, int32_t value) const {
  if (kind == MatterLaundrySelectBinding::Kind::SPIN_SPEED) {
    if (value < 0 || static_cast<size_t>(value) >= this->spin_speeds_.size())
      return nullptr;
    return &this->spin_speeds_[value];
  }
  for (const auto &rinse : this->rinses_) {
    if (rinse.value == value)
      return &rinse.name;
  }
  return nullptr;
}

// ------------------------------------------------- MatterLaundrySelectBinding

MatterLaundrySelectBinding::MatterLaundrySelectBinding(
    MatterLaundryControlsMapping *parent, Kind kind, select::Select *select)
    : MatterSettingBinding(parent->endpoint_id()), parent_(parent), kind_(kind),
      select_(select) {}

uint32_t MatterLaundrySelectBinding::attribute_id_() const {
  return this->kind_ == Kind::SPIN_SPEED
             ? controls_ns::Attributes::SpinSpeedCurrent::Id
             : controls_ns::Attributes::NumberOfRinses::Id;
}

void MatterLaundrySelectBinding::setup_() {
  this->select_->add_on_state_callback(
      [this](auto &&...) { this->request_push(); });
  // Accepted writes of a controller (the SDK validated them against the lists).
  this->watch_attribute(LAUNDRY_WASHER_CONTROLS_ID, this->attribute_id_());
}

// Main loop.
bool MatterLaundrySelectBinding::entity_value_(int32_t &out) {
  if (!this->select_->has_state())
    return false;
  auto index = this->select_->active_index();
  if (!index.has_value())
    return false;
  auto option = this->select_->at(index.value());
  if (!option.has_value())
    return false;
  if (this->parent_->option_to_value(this->kind_, option.value(), out))
    return true;
  ESP_LOGW(TAG, "Select option '%s' is not a %s of endpoint %u",
           option.value().c_str(),
           this->kind_ == Kind::SPIN_SPEED ? "spin speed" : "rinse setting",
           this->endpoint_id());
  return false;
}

// Main loop.
void MatterLaundrySelectBinding::command_value_(int32_t value) {
  const std::string *option = this->parent_->value_to_option(this->kind_, value);
  if (option == nullptr)
    return;
  if (this->select_->has_state()) {
    auto current = this->select_->active_index();
    if (current.has_value() && this->select_->at(current.value()) == *option)
      return;
  }
  this->select_->make_call().set_option(*option).perform();
}

// CHIP task, inside the PushScope.
void MatterLaundrySelectBinding::write_value_(int32_t value) {
  esp_matter_attr_val_t attribute_value =
      this->kind_ == Kind::SPIN_SPEED
          ? esp_matter_uint8(static_cast<uint8_t>(value))
          : esp_matter_enum8(static_cast<uint8_t>(value));
  esp_err_t err = binding_platform()->write_attribute(
      this->endpoint_id(), LAUNDRY_WASHER_CONTROLS_ID, this->attribute_id_(),
      attribute_value, false);
  if (err != ESP_OK)
    ESP_LOGW(TAG, "Cannot set %s %d on endpoint %u: %d",
             this->kind_ == Kind::SPIN_SPEED ? "spin speed" : "rinses",
             static_cast<int>(value), this->endpoint_id(),
             static_cast<int>(err));
}

// CHIP task.
void MatterLaundrySelectBinding::on_attribute_(uint32_t cluster,
                                               uint32_t attribute,
                                               const esp_matter_attr_val_t &v) {
  if (cluster == LAUNDRY_WASHER_CONTROLS_ID && attribute == this->attribute_id_())
    this->matter_changed_(v.val.u8);
}
#endif // USE_SELECT

DelegateAttach attach_laundry_controls_delegate(esp_matter::endpoint_t *endpoint,
                                                uint32_t cluster_id) {
  if (cluster_id != LAUNDRY_WASHER_CONTROLS_ID)
    return DelegateAttach::NOT_HANDLED;
  const uint16_t endpoint_id = esp_matter::endpoint::get_id(endpoint);
  auto *mapping = MatterLaundryControlsMapping::find(endpoint_id);
  if (mapping == nullptr)
    return DelegateAttach::NOT_HANDLED;
  esp_matter::cluster_t *cluster = esp_matter::cluster::get(endpoint, cluster_id);
  if (cluster == nullptr) {
    ESP_LOGE(TAG, "Laundry Washer Controls missing on endpoint %u", endpoint_id);
    return DelegateAttach::FAILED;
  }
  if (esp_matter::cluster::set_delegate_and_init_callback(
          cluster,
          esp_matter::cluster::delegate_cb::LaundryWasherControlsDelegateInitCB,
          static_cast<controls_ns::Delegate *>(mapping)) != ESP_OK)
    return DelegateAttach::FAILED;
  return DelegateAttach::OK;
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_MATTER_LAUNDRY_CONTROLS
