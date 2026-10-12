#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER_SMOKE_CO

#include "esphome/core/automation.h"
#include "matter_binding.h"
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
//
// A binding (binding framework): the requests are the inbound side (counted,
// never coalesced into fewer automation runs), the completion is the outbound
// state (idempotent, so coalescing is safe).
struct MatterSmokeCoSelfTestState {
  // Number of completed self tests.
  uint32_t completions{0};
};

class MatterSmokeCoAlarmMapping
    : public MatterBinding<MatterSmokeCoSelfTestState, uint32_t>,
      public chip::app::Clusters::SmokeCoAlarmDelegate {
public:
  MatterSmokeCoAlarmMapping(uint16_t endpoint_id, uint32_t auto_complete_ms)
      : MatterBinding<MatterSmokeCoSelfTestState, uint32_t>(
            endpoint_id, BindingPolicy{BootSync::NONE, 0, false}),
        auto_complete_ms_(auto_complete_ms) {}

  void add_self_test_trigger(MatterSmokeCoSelfTestTrigger *trigger) {
    this->triggers_.push_back(trigger);
  }

  // Matter thread.
  void OnSelfTestRequested() override;

protected:
  void setup_() override;
  MatterSmokeCoSelfTestState capture_() override;
  void write_matter_(const MatterSmokeCoSelfTestState &s) override;
  void merge_inbound_(uint32_t &acc, const uint32_t &in) override {
    acc += in;
  }
  void apply_inbound_(const uint32_t &requests) override;
  void complete_self_test_();

  // Main loop.
  uint32_t completions_{0};
  // Matter thread.
  uint32_t written_completions_{0};

  uint32_t auto_complete_ms_;
  std::vector<MatterSmokeCoSelfTestTrigger *> triggers_;
};

} // namespace esphome::matter

#endif // USE_MATTER_SMOKE_CO
