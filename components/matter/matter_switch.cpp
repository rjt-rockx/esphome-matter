#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_SWITCH)

#include "matter_component.h"
#include "matter_switch.h"

#include <clusters/OnOff/AttributeIds.h>
#include <clusters/OnOff/ClusterId.h>

namespace esphome::matter {

static_assert(MatterOnOffBinding::ON_OFF_CLUSTER ==
                  chip::app::Clusters::OnOff::Id,
              "OnOff cluster id");
static_assert(MatterOnOffBinding::ON_OFF_ATTRIBUTE ==
                  chip::app::Clusters::OnOff::Attributes::OnOff::Id,
              "OnOff attribute id");

void MatterComponent::register_switch(switch_::Switch *sw,
                                      uint16_t endpoint_id) {
  this->mappings_.push_back(new MatterSwitchMapping(sw, endpoint_id));
}

MatterSwitchMapping::MatterSwitchMapping(switch_::Switch *sw,
                                         uint16_t endpoint_id)
    : MatterOnOffBinding(endpoint_id), switch_(sw) {}

void MatterSwitchMapping::setup_() {
  if (this->switch_ == nullptr)
    return;
  MatterOnOffBinding::setup_();
  // The state callback already provides the logical (post-inversion) state.
  this->switch_->add_on_state_callback([this](bool) { this->request_push(); });
}

bool MatterSwitchMapping::entity_on_() { return this->switch_->state; }

void MatterSwitchMapping::command_on_(bool on) {
  // Nothing to do if the switch already has this state.
  if (this->switch_->state == on)
    return;
  this->switch_->control(on);
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_SWITCH
