#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_MATTER_LAUNDRY_CONTROLS)

#include "matter_delegates.h"
#include "matter_endpoints.h"
#ifdef USE_SELECT
#include "esphome/components/select/select.h"
#endif

#include <app/clusters/laundry-washer-controls-server/laundry-washer-controls-delegate.h>
#include <esp_matter.h>

#include <cstdint>
#include <string>
#include <vector>

namespace esphome::matter {

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

  void on_spin_speed_selected_();
  void on_rinses_selected_();
  void on_matter_spin_speed_(uint8_t index);
  void on_matter_rinses_(uint8_t value);

  std::vector<std::string> spin_speeds_;
  std::vector<Rinse> rinses_;
#ifdef USE_SELECT
  select::Select *spin_speed_select_{nullptr};
  select::Select *rinses_select_{nullptr};
#endif
  // Last values seen on either side (main loop only); break select <-> Matter echo loops.
  int last_spin_speed_{-1};
  int last_rinses_{-1};
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_MATTER_LAUNDRY_CONTROLS
