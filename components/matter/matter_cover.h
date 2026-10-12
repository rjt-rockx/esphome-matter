#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_COVER)

#include "esphome/components/cover/cover.h"
#include "esphome/core/optional.h"
#include "matter_cover_binding.h"

#include <app/clusters/window-covering-server/WindowCoveringDelegate.h>
#include <esp_matter.h>
#include <esp_matter_cluster.h>

#include <cstdint>

namespace esphome::matter {

// Binds an ESPHome cover to a Matter Window Covering endpoint, through the
// binding framework (see matter_cover_binding.h for the state machine).
//
// The mapping is also the cluster's Delegate: connectedhomeip invokes
// HandleMovement/HandleStopMotion on the CHIP task after it has set the
// TargetPosition attributes, and the mapping submits the request to the ESPHome
// main loop. Cover state is pushed back with attribute::report, which does not
// run the attribute callbacks.
//
// Using the Window Covering delegate and batching lift and tilt into a single
// cover call is aligned with the approach of mrflo97/esphome-matter.
class MatterCoverMapping : public MatterCoverBinding,
                           public chip::app::Clusters::WindowCovering::Delegate {
public:
  MatterCoverMapping(cover::Cover *cover, uint16_t endpoint_id);

  // EndProductType for the endpoint config constructor (the attribute is const
  // in esp-matter). `end_product_type` < 0 selects it from the device class.
  uint8_t end_product_type(const char *device_class, int end_product_type) const;

  // Fills the esp-matter cluster config used to create the endpoint: feature
  // flags, Type, ConfigStatus, delegate and initial positions. Called from the
  // generated endpoint build callback.
  void configure(esp_matter::cluster::window_covering::config_t &config,
                 const char *device_class);

  // chip::app::Clusters::WindowCovering::Delegate (CHIP task)
  CHIP_ERROR HandleMovement(
      chip::app::Clusters::WindowCovering::WindowCoveringType type) override;
  CHIP_ERROR HandleStopMotion() override;

protected:
  void setup_() override;
  CoverEntityView entity_() override;
  void command_stop_() override;
  void command_move_(bool has_lift, uint16_t lift, bool has_tilt, uint16_t tilt) override;

  cover::Cover *cover_;
};

// Used by the generated endpoint code. Both look up the mapping registered for
// the endpoint by MatterComponent::register_cover.
uint8_t window_covering_end_product_type(uint16_t endpoint_id, const char *device_class,
                                         int end_product_type);
void configure_window_covering(esp_matter::cluster::window_covering::config_t &config,
                               uint16_t endpoint_id, const char *device_class);

} // namespace esphome::matter

#endif // USE_MATTER && USE_COVER
