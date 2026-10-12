#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER_CABINET_MODE) || defined(USE_MATTER_REFRIGERATOR_ALARM)

#include "matter_endpoints.h"

#ifdef USE_BINARY_SENSOR
#include "esphome/components/binary_sensor/binary_sensor.h"
#endif
#ifdef USE_SELECT
#include "esphome/components/select/select.h"
#endif

#include <cstdint>
#include <string>
#include <vector>

#ifdef USE_MATTER_CABINET_MODE
#include <app/clusters/mode-base-server/Delegate.h>
#include <app/clusters/mode-base-server/mode-base-server.h>
#endif
#ifdef USE_MATTER_REFRIGERATOR_ALARM
#include <app/clusters/refrigerator-alarm-server/refrigerator-alarm-server.h>
#endif

namespace esphome::matter {

#ifdef USE_MATTER_CABINET_MODE

// One entry of the `modes:` list: the Matter label and value, the select
// option that stands for it and the mode tags (Mode Base and cluster specific
// ModeTag values).
struct MatterModeOption {
  std::string label;
  std::string option;
  uint8_t mode;
  std::vector<uint16_t> tags;
};

class MatterCabinetModeMapping;

// ModeBase delegate of the Refrigerator And Temperature Controlled Cabinet Mode
// cluster. The mode list is static (from the YAML); ChangeToMode is always
// accepted and forwarded to the ESPHome select.
class MatterCabinetModeDelegate
    : public chip::app::Clusters::ModeBase::Delegate {
public:
  explicit MatterCabinetModeDelegate(MatterCabinetModeMapping *mapping)
      : mapping_(mapping) {}

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

  // Matter thread. Updates CurrentMode (a no-op for an unknown value).
  void update_current_mode(uint8_t mode);
  // Matter thread. Returns the current mode, or the first mode before the
  // cluster is initialized.
  uint8_t current_mode();

protected:
  MatterCabinetModeMapping *mapping_;
};

// Binds a select to the mode cluster of an endpoint.
//
// Matter -> ESPHome: ChangeToMode selects the option that stands for the mode.
// ESPHome -> Matter: a change of the select updates CurrentMode. The state of
// the select (typically restored) wins at boot; without one the select shows
// the mode that Matter persisted.
class MatterCabinetModeMapping : public MatterEndpointMappingBase {
public:
  MatterCabinetModeMapping(uint16_t endpoint_id,
                           std::vector<MatterModeOption> modes);

#ifdef USE_SELECT
  void set_select(select::Select *select) { this->select_ = select; }
#endif

  void initialize() override;

  MatterCabinetModeDelegate *delegate() { return &this->delegate_; }
  const std::vector<MatterModeOption> &modes() const { return this->modes_; }

  // Matter thread (from the delegate).
  void on_change_to_mode(uint8_t mode);

protected:
#ifdef USE_SELECT
  void on_select_(size_t index);
  void apply_select_(uint8_t mode);
  void publish_select_(uint8_t mode);
  void push_mode_to_matter_(uint8_t mode);
  const MatterModeOption *find_by_option_(const char *option) const;
  const MatterModeOption *find_by_mode_(uint8_t mode) const;

  select::Select *select_{nullptr};
#endif
  std::vector<MatterModeOption> modes_;
  MatterCabinetModeDelegate delegate_;
};

// The delegate for the `delegate` field of the cluster config of an endpoint
// (a ModeBase::Delegate*), or nullptr.
void *get_cabinet_mode_delegate(uint16_t endpoint_id);

#endif // USE_MATTER_CABINET_MODE

#ifdef USE_MATTER_REFRIGERATOR_ALARM

// Refrigerator Alarm: DoorOpen (bit 0 of State) follows a binary_sensor
// (ON = door open). A change updates the State attribute and generates the
// Notify event with the Active, Inactive, State and Mask bitmaps.
class MatterRefrigeratorAlarmMapping : public MatterEndpointMappingBase {
public:
#ifdef USE_BINARY_SENSOR
  MatterRefrigeratorAlarmMapping(uint16_t endpoint_id,
                                 binary_sensor::BinarySensor *door_open)
      : MatterEndpointMappingBase(endpoint_id), door_open_(door_open) {}
#endif

  void initialize() override;

protected:
  void update_(bool open);

#ifdef USE_BINARY_SENSOR
  binary_sensor::BinarySensor *door_open_;
#endif
};

#endif // USE_MATTER_REFRIGERATOR_ALARM

} // namespace esphome::matter

#endif // USE_MATTER_CABINET_MODE || USE_MATTER_REFRIGERATOR_ALARM
