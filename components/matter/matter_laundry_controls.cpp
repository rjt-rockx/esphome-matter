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
    this->spin_speed_select_->add_on_state_callback(
        [this](auto &&...) { this->on_spin_speed_selected_(); });
    global_matter_component->register_attribute_callback(
        this->endpoint_id(), LAUNDRY_WASHER_CONTROLS_ID,
        controls_ns::Attributes::SpinSpeedCurrent::Id,
        [this](const esp_matter_attr_val_t &value) {
          this->on_matter_spin_speed_(value.val.u8);
        });
  }
  if (this->rinses_select_ != nullptr) {
    this->rinses_select_->add_on_state_callback(
        [this](auto &&...) { this->on_rinses_selected_(); });
    global_matter_component->register_attribute_callback(
        this->endpoint_id(), LAUNDRY_WASHER_CONTROLS_ID,
        controls_ns::Attributes::NumberOfRinses::Id,
        [this](const esp_matter_attr_val_t &value) {
          this->on_matter_rinses_(value.val.u8);
        });
  }
  // The selects are the source of truth after a restart.
  global_matter_component->defer_to_main_loop([this]() {
    if (this->spin_speed_select_ != nullptr &&
        this->spin_speed_select_->has_state())
      this->on_spin_speed_selected_();
    if (this->rinses_select_ != nullptr && this->rinses_select_->has_state())
      this->on_rinses_selected_();
  });
#endif
}

void MatterLaundryControlsMapping::on_spin_speed_selected_() {
#ifdef USE_SELECT
  auto index = this->spin_speed_select_->active_index();
  if (!index.has_value())
    return;
  auto option = this->spin_speed_select_->at(index.value());
  if (!option.has_value())
    return;
  for (size_t i = 0; i < this->spin_speeds_.size(); i++) {
    if (this->spin_speeds_[i] != option.value())
      continue;
    if (static_cast<int>(i) == this->last_spin_speed_)
      return;
    this->last_spin_speed_ = static_cast<int>(i);
    set_attribute_value(this->endpoint_id(), LAUNDRY_WASHER_CONTROLS_ID,
                        controls_ns::Attributes::SpinSpeedCurrent::Id,
                        static_cast<uint64_t>(i));
    return;
  }
  ESP_LOGW(TAG, "Select option '%s' is not a spin speed of endpoint %u",
           option.value().c_str(), this->endpoint_id());
#endif
}

void MatterLaundryControlsMapping::on_rinses_selected_() {
#ifdef USE_SELECT
  auto index = this->rinses_select_->active_index();
  if (!index.has_value())
    return;
  auto option = this->rinses_select_->at(index.value());
  if (!option.has_value())
    return;
  for (const auto &rinse : this->rinses_) {
    if (rinse.name != option.value())
      continue;
    if (static_cast<int>(rinse.value) == this->last_rinses_)
      return;
    this->last_rinses_ = rinse.value;
    set_attribute_value(this->endpoint_id(), LAUNDRY_WASHER_CONTROLS_ID,
                        controls_ns::Attributes::NumberOfRinses::Id,
                        static_cast<uint64_t>(rinse.value));
    return;
  }
  ESP_LOGW(TAG, "Select option '%s' is not a rinse setting of endpoint %u",
           option.value().c_str(), this->endpoint_id());
#endif
}

// Called on the CHIP task after the SDK accepted a write of a controller.
void MatterLaundryControlsMapping::on_matter_spin_speed_(uint8_t index) {
  defer_to_main_loop(global_matter_component, [this, index]() {
#ifdef USE_SELECT
    if (static_cast<int>(index) == this->last_spin_speed_ ||
        index >= this->spin_speeds_.size() ||
        this->spin_speed_select_ == nullptr)
      return;
    this->last_spin_speed_ = index;
    this->spin_speed_select_->make_call()
        .set_option(this->spin_speeds_[index])
        .perform();
#endif
  });
}

void MatterLaundryControlsMapping::on_matter_rinses_(uint8_t value) {
  defer_to_main_loop(global_matter_component, [this, value]() {
#ifdef USE_SELECT
    if (static_cast<int>(value) == this->last_rinses_ ||
        this->rinses_select_ == nullptr)
      return;
    for (const auto &rinse : this->rinses_) {
      if (rinse.value != value)
        continue;
      this->last_rinses_ = value;
      this->rinses_select_->make_call().set_option(rinse.name).perform();
      return;
    }
#endif
  });
}

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
