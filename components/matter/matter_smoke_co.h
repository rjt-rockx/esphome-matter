#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER_SMOKE_CO

#include "esphome/core/automation.h"
#include "matter_endpoints.h"

#include <app/clusters/smoke-co-alarm-server/SmokeCoAlarmCluster.h>

#include <cstdint>
#include <vector>

namespace esphome::matter {

// on_self_test: the Smoke CO Alarm accepted a SelfTestRequest (TestInProgress
// is TRUE and ExpressedState is Testing by now).
class MatterSmokeCoSelfTestTrigger : public Trigger<> {};

// Self test handling of the Smoke CO Alarm cluster of an endpoint.
//
// The cluster itself accepts SelfTestRequest, refuses it with Busy while an
// alarm or a test is active, sets TestInProgress and ExpressedState=Testing and
// then calls OnSelfTestRequested(). Finishing the test is up to the
// application, so without this mapping a requested test would never end.
//
//  * on_self_test triggers run on the ESPHome main loop (blink, beep, ...).
//  * auto_complete_ms > 0: the test ends by itself after that time
//    (TestInProgress=false, SelfTestComplete event, ExpressedState recomputed).
//  * auto_complete_ms == 0: the application ends the test through the bound
//    test_in_progress binary_sensor (OFF).
class MatterSmokeCoAlarmMapping : public MatterEndpointMappingBase,
                                  public chip::app::Clusters::SmokeCoAlarmDelegate {
public:
  MatterSmokeCoAlarmMapping(uint16_t endpoint_id, uint32_t auto_complete_ms)
      : MatterEndpointMappingBase(endpoint_id),
        auto_complete_ms_(auto_complete_ms) {}

  void add_self_test_trigger(MatterSmokeCoSelfTestTrigger *trigger) {
    this->triggers_.push_back(trigger);
  }

  void initialize() override;

  // Matter thread.
  void OnSelfTestRequested() override;

protected:
  void complete_self_test_();

  uint32_t auto_complete_ms_;
  std::vector<MatterSmokeCoSelfTestTrigger *> triggers_;
};

} // namespace esphome::matter

#endif // USE_MATTER_SMOKE_CO
