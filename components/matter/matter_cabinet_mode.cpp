#include "matter_cabinet_mode.h"

#if defined(USE_MATTER_CABINET_MODE) || defined(USE_MATTER_REFRIGERATOR_ALARM)

#include "esphome/core/log.h"
#include "matter_attributes.h"
#include "matter_component.h"

#include <esp_matter.h>


static const char *const TAG = "matter.cabinet";

namespace esphome::matter {

#ifdef USE_MATTER_CABINET_MODE

namespace {

// Mappings are registered during setup() and never removed, so the Matter
// thread can safely read this afterwards.
std::vector<MatterCabinetModeMapping *> &cabinet_mode_mappings() {
  static std::vector<MatterCabinetModeMapping *> mappings;
  return mappings;
}

} // namespace

void *get_cabinet_mode_delegate(uint16_t endpoint_id) {
  for (auto *mapping : cabinet_mode_mappings()) {
    if (mapping->endpoint_id() == endpoint_id)
      return static_cast<chip::app::Clusters::ModeBase::Delegate *>(
          mapping->delegate());
  }
  return nullptr;
}

CHIP_ERROR
MatterCabinetModeDelegate::GetModeLabelByIndex(uint8_t mode_index,
                                               chip::MutableCharSpan &label) {
  const auto &modes = this->mapping_->modes();
  if (mode_index >= modes.size())
    return CHIP_ERROR_PROVIDER_LIST_EXHAUSTED;
  return chip::CopyCharSpanToMutableCharSpan(
      chip::CharSpan::fromCharString(modes[mode_index].label.c_str()), label);
}

CHIP_ERROR MatterCabinetModeDelegate::GetModeValueByIndex(uint8_t mode_index,
                                                          uint8_t &value) {
  const auto &modes = this->mapping_->modes();
  if (mode_index >= modes.size())
    return CHIP_ERROR_PROVIDER_LIST_EXHAUSTED;
  value = modes[mode_index].mode;
  return CHIP_NO_ERROR;
}

CHIP_ERROR MatterCabinetModeDelegate::GetModeTagsByIndex(
    uint8_t mode_index,
    chip::app::DataModel::List<
        chip::app::Clusters::detail::Structs::ModeTagStruct::Type> &tags) {
  const auto &modes = this->mapping_->modes();
  if (mode_index >= modes.size())
    return CHIP_ERROR_PROVIDER_LIST_EXHAUSTED;
  const auto &option_tags = modes[mode_index].tags;
  if (tags.size() < option_tags.size())
    return CHIP_ERROR_INVALID_ARGUMENT;
  for (size_t i = 0; i < option_tags.size(); i++) {
    tags[i].value = option_tags[i];
    tags[i].mfgCode.ClearValue();
  }
  tags.reduce_size(option_tags.size());
  return CHIP_NO_ERROR;
}

// Matter thread. The mode is valid (the cluster checked it against the list).
void MatterCabinetModeDelegate::HandleChangeToMode(
    uint8_t new_mode,
    chip::app::Clusters::ModeBase::Commands::ChangeToModeResponse::Type
        &response) {
  response.status =
      chip::to_underlying(chip::app::Clusters::ModeBase::StatusCode::kSuccess);
  this->mapping_->on_change_to_mode(new_mode);
}

void MatterCabinetModeDelegate::update_current_mode(uint8_t mode) {
  auto *instance = this->GetInstance();
  if (instance == nullptr)
    return;
  auto status = instance->UpdateCurrentMode(mode);
  if (status != chip::Protocols::InteractionModel::Status::Success)
    ESP_LOGW(TAG, "Cannot change the cabinet mode to %u (status %u)", mode,
             static_cast<unsigned>(status));
}

uint8_t MatterCabinetModeDelegate::current_mode() {
  auto *instance = this->GetInstance();
  if (instance != nullptr)
    return instance->GetCurrentMode();
  return this->mapping_->modes().front().mode;
}

MatterCabinetModeMapping::MatterCabinetModeMapping(
    uint16_t endpoint_id, std::vector<MatterModeOption> modes)
    : MatterEndpointMappingBase(endpoint_id), modes_(std::move(modes)),
      delegate_(this) {
  cabinet_mode_mappings().push_back(this);
}

void MatterCabinetModeMapping::initialize() {
#ifdef USE_SELECT
  if (this->select_ == nullptr)
    return;
  this->select_->add_on_state_callback(
      [this](size_t index) { this->on_select_(index); });

  if (this->select_->has_state()) {
    const auto *mode = this->find_by_option_(this->select_->current_option().c_str());
    if (mode != nullptr)
      this->push_mode_to_matter_(mode->mode);
  } else {
    // Show the persisted mode.
    run_on_matter_thread([this]() {
      uint8_t mode = this->delegate_.current_mode();
      global_matter_component->defer_to_main_loop(
          [this, mode]() { this->publish_select_(mode); });
    });
  }
#endif
}

// Matter thread.
void MatterCabinetModeMapping::on_change_to_mode(uint8_t mode) {
#ifdef USE_SELECT
  if (this->select_ == nullptr)
    return;
  global_matter_component->defer_to_main_loop(
      [this, mode]() { this->apply_select_(mode); });
#endif
}

#ifdef USE_SELECT

const MatterModeOption *
MatterCabinetModeMapping::find_by_option_(const char *option) const {
  for (const auto &mode : this->modes_) {
    if (mode.option == option)
      return &mode;
  }
  return nullptr;
}

const MatterModeOption *MatterCabinetModeMapping::find_by_mode_(uint8_t mode) const {
  for (const auto &candidate : this->modes_) {
    if (candidate.mode == mode)
      return &candidate;
  }
  return nullptr;
}

// Main loop.
void MatterCabinetModeMapping::apply_select_(uint8_t mode) {
  const auto *option = this->find_by_mode_(mode);
  if (option == nullptr)
    return;
  auto current = this->select_->current_option();
  if (this->select_->has_state() && option->option == current.c_str())
    return;
  ESP_LOGD(TAG, "Matter selected cabinet mode '%s' on endpoint %u",
           option->label.c_str(), this->endpoint_id_);
  auto call = this->select_->make_call();
  call.set_option(option->option);
  call.perform();
}

// Main loop, boot only: reflect the mode without commanding the select.
void MatterCabinetModeMapping::publish_select_(uint8_t mode) {
  const auto *option = this->find_by_mode_(mode);
  if (option != nullptr && !this->select_->has_state())
    this->select_->publish_state(option->option);
}

// Main loop.
void MatterCabinetModeMapping::on_select_(size_t index) {
  auto option = this->select_->at(index);
  if (!option.has_value())
    return;
  const auto *mode = this->find_by_option_(option->c_str());
  if (mode == nullptr)
    return;
  this->push_mode_to_matter_(mode->mode);
}

// Main loop.
void MatterCabinetModeMapping::push_mode_to_matter_(uint8_t mode) {
  run_on_matter_thread(
      [this, mode]() { this->delegate_.update_current_mode(mode); });
}

#endif // USE_SELECT

#endif // USE_MATTER_CABINET_MODE

#ifdef USE_MATTER_REFRIGERATOR_ALARM

void MatterRefrigeratorAlarmMapping::initialize() {
#ifdef USE_BINARY_SENSOR
  if (this->door_open_ == nullptr)
    return;
  this->door_open_->add_on_state_callback(
      [this](bool open) { this->update_(open); });
  if (this->door_open_->has_state())
    this->update_(this->door_open_->state);
#endif
}

// Main loop; the attribute and the event are handled on the Matter thread.
void MatterRefrigeratorAlarmMapping::update_(bool open) {
  uint16_t endpoint_id = this->endpoint_id_;
  run_on_matter_thread([endpoint_id, open]() {
    using chip::app::Clusters::RefrigeratorAlarm::AlarmMap;
    auto &server = RefrigeratorAlarmServer::Instance();
    chip::BitMask<AlarmMap> state;
    if (server.GetStateValue(endpoint_id, &state) !=
        chip::Protocols::InteractionModel::Status::Success) {
      ESP_LOGW(TAG, "Refrigerator Alarm of endpoint %u not found",
               endpoint_id);
      return;
    }
    chip::BitMask<AlarmMap> new_state = state;
    if (open)
      new_state.Set(AlarmMap::kDoorOpen);
    else
      new_state.Clear(AlarmMap::kDoorOpen);
    if (new_state == state)
      return;
    // Writes State and generates the Notify event (Active, Inactive, State and
    // Mask). DoorOpen is the only alarm and always in the Mask.
    auto status = server.SetStateValue(endpoint_id, new_state);
    if (status != chip::Protocols::InteractionModel::Status::Success)
      ESP_LOGW(TAG, "Cannot update the Refrigerator Alarm State: %u",
               static_cast<unsigned>(status));
  });
}

#endif // USE_MATTER_REFRIGERATOR_ALARM

} // namespace esphome::matter

#endif // USE_MATTER_CABINET_MODE || USE_MATTER_REFRIGERATOR_ALARM
