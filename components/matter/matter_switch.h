#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_SWITCH)

#include "esphome/components/switch/switch.h"
#include "matter_endpoints.h"

#include <esp_matter.h>

#include <cstdint>

namespace esphome::matter {

// Binds an ESPHome switch to the OnOff cluster of an endpoint.
class MatterSwitchMapping : public MatterEndpointMappingBase {
public:
  MatterSwitchMapping(switch_::Switch *sw, uint16_t endpoint_id);

  void initialize() override;

  void push_state_to_matter(bool on);

protected:
  // Called on the ESPHome main loop.
  void apply_on_off_(bool on);

  switch_::Switch *switch_;
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_SWITCH
