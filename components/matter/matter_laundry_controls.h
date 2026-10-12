#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_MATTER_LAUNDRY_CONTROLS)

#include "matter_delegates.h"
#include "matter_endpoints.h"
#include "matter_sync_bindings.h"
#ifdef USE_SELECT
#include "esphome/components/select/select.h"
#endif

#include <app/clusters/laundry-washer-controls-server/laundry-washer-controls-delegate.h>
#include <esp_matter.h>

#include <cstdint>
#include <string>
#include <vector>

namespace esphome::matter {

class MatterLaundryControlsMapping;

#ifdef USE_SELECT
// A select bound to SpinSpeedCurrent or NumberOfRinses, through the binding framework (MatterSettingBinding):
// the select is pushed with coalescing and echo suppression, controller writes reach the select on the main loop
// and a push captured before such a write cannot overwrite it. The selects win at boot (when they have a state).
class MatterLaundrySelectBinding : public MatterSettingBinding {
public:
  enum class Kind : uint8_t { SPIN_SPEED, RINSES };

  MatterLaundrySelectBinding(MatterLaundryControlsMapping *parent, Kind kind,
                             select::Select *select);

protected:
  void setup_() override;
  bool entity_value_(int32_t &out) override;
  void command_value_(int32_t value) override;
  void write_value_(int32_t value) override;
  void on_attribute_(uint32_t cluster, uint32_t attribute,
                     const esp_matter_attr_val_t &v) override;

  uint32_t attribute_id_() const;

  MatterLaundryControlsMapping *parent_;
  Kind kind_;
  select::Select *select_;
};
#endif

// Delegate of the Laundry Washer Controls cluster (0x0053): the lists behind SpinSpeeds and SupportedRinses, and a
// two-way binding of SpinSpeedCurrent / NumberOfRinses to ESPHome selects.
//
// The SDK validates writes of a controller against these lists (PreAttributeChanged). Accepted values arrive through
// the attribute update callback and are applied to the select on the main loop.
class MatterLaundryControlsMapping
    : public MatterEndpointMappingBase,
      public chip::app::Clusters::LaundryWasherControls::Delegate {
public:
  explicit MatterLaundryControlsMapping(uint16_t endpoint_id);

  static MatterLaundryControlsMapping *find(uint16_t endpoint_id);

  // Codegen
  void add_spin_speed(const char *spin_speed);
  // `value` is a NumberOfRinsesEnum (0 none, 1 normal, 2 extra, 3 max), `name` the select option text.
  void add_rinse(uint8_t value, const char *name);
#ifdef USE_SELECT
  void set_spin_speed_select(select::Select *select) {
    this->spin_speed_select_ = select;
  }
  void set_rinses_select(select::Select *select) {
    this->rinses_select_ = select;
  }
#endif

  void initialize() override;

#ifdef USE_SELECT
  // Main loop. Name <-> value of the select options. False when the option is not one of the configured entries.
  bool option_to_value(MatterLaundrySelectBinding::Kind kind,
                       const std::string &option, int32_t &value) const;
  const std::string *value_to_option(MatterLaundrySelectBinding::Kind kind,
                                     int32_t value) const;
#endif

  // Delegate (CHIP task)
  CHIP_ERROR GetSpinSpeedAtIndex(size_t index,
                                 chip::MutableCharSpan &spin_speed) override;
  CHIP_ERROR GetSupportedRinseAtIndex(
      size_t index,
      chip::app::Clusters::LaundryWasherControls::NumberOfRinsesEnum
          &supported_rinse) override;

protected:
  struct Rinse {
    uint8_t value;
    std::string name;
  };

  std::vector<std::string> spin_speeds_;
  std::vector<Rinse> rinses_;
#ifdef USE_SELECT
  select::Select *spin_speed_select_{nullptr};
  select::Select *rinses_select_{nullptr};
  MatterLaundrySelectBinding *spin_speed_binding_{nullptr};
  MatterLaundrySelectBinding *rinses_binding_{nullptr};
#endif
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_MATTER_LAUNDRY_CONTROLS
