#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_SWITCH)

#include "matter_component.h"
#include "matter_switch.h"

#include <esp_matter_cluster.h>
#include <platform/CHIPDeviceLayer.h>

namespace esphome::matter {

void MatterComponent::register_switch(switch_::Switch *sw,
                                      uint16_t endpoint_id) {
  this->mappings_.push_back(new MatterSwitchMapping(sw, endpoint_id));
}

MatterSwitchMapping::MatterSwitchMapping(switch_::Switch *sw,
                                         uint16_t endpoint_id)
    : MatterEndpointMappingBase(endpoint_id), switch_(sw) {}

void MatterSwitchMapping::initialize() {
  if (this->switch_ == nullptr)
    return;

  using namespace chip::app::Clusters;

  // Matter -> ESPHome. Called from the CHIP task, so defer to the main loop.
  global_matter_component->register_attribute_callback(
      this->endpoint_id(), OnOff::Id, OnOff::Attributes::OnOff::Id,
      [this](const esp_matter_attr_val_t &value) {
        bool on = value.val.b;
        global_matter_component->defer_to_main_loop(
            [this, on]() { this->apply_on_off_(on); });
      });

  // ESPHome -> Matter. The state callback already provides the logical
  // (post-inversion) state of the switch.
  this->switch_->add_on_state_callback(
      [this](bool on) { this->push_state_to_matter(on); });

  // Apply the stored Matter OnOff value to the switch at boot.
  chip::DeviceLayer::SystemLayer().ScheduleLambda([this]() {
    using namespace chip::app::Clusters;
    global_matter_component->replay_attribute_callback(
        this->endpoint_id(), OnOff::Id, OnOff::Attributes::OnOff::Id);
  });
}

void MatterSwitchMapping::apply_on_off_(bool on) {
  // Loop prevention: nothing to do if the switch already has this state.
  if (this->switch_->state == on)
    return;
  this->switch_->control(on);
}

// Uses report() instead of update() so no attribute callbacks are triggered,
// which prevents an ESPHome -> Matter -> ESPHome loop. esp-matter skips the
// report when the value is unchanged.
void MatterSwitchMapping::push_state_to_matter(bool on) {
  uint16_t eid = this->endpoint_id();
  chip::DeviceLayer::SystemLayer().ScheduleLambda([eid, on]() {
    using namespace chip::app::Clusters;
    esp_matter_attr_val_t on_val = esp_matter_bool(on);
    esp_matter::attribute::report(eid, OnOff::Id, OnOff::Attributes::OnOff::Id,
                                  &on_val);
  });
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_SWITCH
