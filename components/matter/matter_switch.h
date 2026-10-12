#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_SWITCH)

#include "esphome/components/switch/switch.h"
#include "matter_push_bindings.h"

#include <esp_matter.h>

#include <cstdint>

namespace esphome::matter {

// Binds an ESPHome switch to the OnOff cluster of an endpoint, through the
// binding framework (MatterOnOffBinding): coalesced pushes, PushScope echo
// suppression, reconcile after refused commands, stored value replayed at boot.
class MatterSwitchMapping : public MatterOnOffBinding {
public:
  MatterSwitchMapping(switch_::Switch *sw, uint16_t endpoint_id);

protected:
  void setup_() override;
  bool entity_on_() override;
  void command_on_(bool on) override;

  switch_::Switch *switch_;
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_SWITCH
