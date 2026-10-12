#pragma once

#include "esphome/core/automation.h"
#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include <cstdint>

#include <esp_matter.h>

namespace esphome::matter {

class MatterComponent;

// Matter Identify cluster (0x0003) events delivered to the ESPHome main loop.
//
// Trigger arguments: (uint16_t endpoint_id, bool active, uint16_t identify_time, uint8_t effect)
//   active        true on Identify start and on an effect request, false on stop.
//   identify_time seconds requested by the controller (IdentifyTime attribute) at start, 0 otherwise.
//   effect        EffectIdentifierEnum for effect requests, IdentifyTrigger::NO_EFFECT otherwise.
class IdentifyTrigger : public Trigger<uint16_t, bool, uint16_t, uint8_t> {
public:
  static constexpr uint16_t ANY_ENDPOINT = 0xFFFF;
  static constexpr uint8_t NO_EFFECT = 0xFF;

  IdentifyTrigger(MatterComponent *parent, uint16_t endpoint_id)
      : parent_(parent), endpoint_id_(endpoint_id) {}

  // Called from the CHIP task. Defers to the ESPHome main loop.
  void dispatch(uint16_t endpoint_id, bool active, uint16_t identify_time,
                uint8_t effect);

protected:
  MatterComponent *parent_;
  uint16_t endpoint_id_;  // ANY_ENDPOINT = all endpoints
};

void register_identify_trigger(IdentifyTrigger *trigger);

// esp_matter::identification::callback_t implementation. Runs on the CHIP task.
esp_err_t matter_identification_cb(
    esp_matter::identification::callback_type_t type, uint16_t endpoint_id,
    uint8_t effect_id, uint8_t effect_variant, void *priv_data);

} // namespace esphome::matter

#endif // USE_MATTER
