#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_COVER)

#include "esphome/components/cover/cover.h"
#include "esphome/core/optional.h"
#include "matter_endpoints.h"

#include <app/clusters/window-covering-server/WindowCoveringDelegate.h>
#include <esp_matter.h>
#include <esp_matter_cluster.h>

#include <cstdint>

namespace esphome::matter {

namespace conversion {

// Matter Percent100ths: 0 = fully open, 10000 = fully closed.
// ESPHome cover position/tilt: 1.0 = fully open, 0.0 = fully closed.
constexpr uint16_t kCoverMatterOpen = 0;
constexpr uint16_t kCoverMatterClosed = 10000;

// Returns false when the ESPHome value is not a finite number (unknown).
bool cover_to_matter(float esphome_value, uint16_t &matter_value);
float cover_from_matter(uint16_t matter_value);

} // namespace conversion

// Binds an ESPHome cover to a Matter Window Covering endpoint.
//
// The mapping is also the cluster's Delegate: connectedhomeip invokes
// HandleMovement/HandleStopMotion on the CHIP task after it has set the
// TargetPosition attributes, and the mapping defers the work to the ESPHome
// main loop. Cover state is pushed back with attribute::report.
//
// Using the Window Covering delegate and batching lift and tilt into a single
// cover call is aligned with the approach of mrflo97/esphome-matter.
class MatterCoverMapping : public MatterEndpointMappingBase,
                           public chip::app::Clusters::WindowCovering::Delegate {
public:
  MatterCoverMapping(cover::Cover *cover, uint16_t endpoint_id);

  // Fills the esp-matter cluster config used to create the endpoint: feature
  // flags, Type, EndProductType, ConfigStatus, delegate and initial positions.
  // Called from the generated endpoint build callback.
  // `end_product_type` < 0 selects it from the device class.
  void configure(esp_matter::cluster::window_covering::config_t &config,
                 const char *device_class, int end_product_type);

  void initialize() override;

  // chip::app::Clusters::WindowCovering::Delegate (CHIP task)
  CHIP_ERROR HandleMovement(
      chip::app::Clusters::WindowCovering::WindowCoveringType type) override;
  CHIP_ERROR HandleStopMotion() override;

protected:
  void request_lift_(uint16_t target, bool from_delegate);
  void request_tilt_(uint16_t target, bool from_delegate);
  void schedule_drain_();
  void drain_();
  void stop_();
  void on_cover_state_();
  void push_state_to_matter_();

  cover::Cover *cover_;
  bool has_tilt_{false};
  bool has_position_{false};
  bool has_stop_{false};

  // Main-loop state
  optional<uint16_t> pending_lift_;
  optional<uint16_t> pending_tilt_;
  bool drain_scheduled_{false};
  optional<uint16_t> target_lift_;
  optional<uint16_t> target_tilt_;
  uint16_t last_lift_request_{0};
  uint32_t last_lift_request_ms_{0};
  bool have_lift_request_{false};
  uint16_t last_tilt_request_{0};
  uint32_t last_tilt_request_ms_{0};
  bool have_tilt_request_{false};
  uint32_t last_stop_ms_{0};
  bool stop_seen_{false};
};

// Used by the generated endpoint code. Looks up the mapping registered for the
// endpoint by MatterComponent::register_cover.
void configure_window_covering(
    esp_matter::cluster::window_covering::config_t &config,
    uint16_t endpoint_id, const char *device_class, int end_product_type);

} // namespace esphome::matter

#endif // USE_MATTER && USE_COVER
