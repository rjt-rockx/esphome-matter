#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "matter_binding.h"

#include <cstdint>

// Reusable framework bindings for push-mostly entities. Everything here is free
// of ESPHome entity and CHIP cluster headers so the state machines can be
// host-tested (tests/cpp/test_push_bindings.cpp).
namespace esphome::matter {

// Controller writes are not applied by these bindings.
struct NoInbound {};

// One converted Matter value. `valid` is false while the entity has no state
// (or the converter rejected the reading); nothing is written then.
struct ValueState {
  bool valid{false};
  esp_matter_attr_val_t value{};
};

// ESPHome -> Matter only: mirrors one attribute value. The newest value wins,
// a burst of readings costs one or two CHIP work items and no heap allocation.
// Subclasses implement capture_() (main loop) and call request_push() from the
// entity callback; they override write_attribute_() for code-driven clusters.
class MatterAttributePushBinding
    : public MatterBinding<ValueState, NoInbound> {
public:
  MatterAttributePushBinding(uint16_t endpoint_id, uint32_t cluster_id,
                             uint32_t attribute_id, bool report = false)
      : MatterBinding<ValueState, NoInbound>(
            endpoint_id, BindingPolicy{BootSync::ESPHOME_WINS, 0, false}),
        cluster_id_(cluster_id), attribute_id_(attribute_id), report_(report) {}

protected:
  void setup_() override {}

  // CHIP, inside the PushScope. Default: write the attribute store.
  virtual void write_attribute_(const ValueState &s) {
    esp_matter_attr_val_t value = s.value;
    esp_err_t err = binding_platform()->write_attribute(
        this->endpoint_id_, this->cluster_id_, this->attribute_id_, value,
        this->report_);
    if (err != ESP_OK) {
      ESP_LOGE("matter.sensor",
               "Failed to update attribute 0x%08X on cluster 0x%08X, "
               "endpoint %u: %d",
               static_cast<unsigned>(this->attribute_id_),
               static_cast<unsigned>(this->cluster_id_),
               static_cast<unsigned>(this->endpoint_id_), static_cast<int>(err));
    }
  }

  void write_matter_(const ValueState &s) final {
    if (s.valid)
      this->write_attribute_(s);
  }
  void apply_inbound_(const NoInbound &) final {}

  uint32_t cluster_id_;
  uint32_t attribute_id_;
  bool report_;
};

// OnOff cluster (0x0006) OnOff attribute (0x0000) bound to an on/off entity.
//
// Matter -> ESPHome: a controller write is submitted, coalesced and applied on
// the main loop. ESPHome -> Matter: request_push() mirrors the entity with
// attribute::report() (no callbacks). After every applied write the state is
// pushed back, so a refused command ends with Matter equal to ESPHome.
// Boot: the stored Matter value is replayed into the entity (MATTER_WINS).
class MatterOnOffBinding : public MatterBinding<bool, bool> {
public:
  static constexpr uint32_t ON_OFF_CLUSTER = 0x0006;
  static constexpr uint32_t ON_OFF_ATTRIBUTE = 0x0000;

  explicit MatterOnOffBinding(uint16_t endpoint_id)
      : MatterBinding<bool, bool>(
            endpoint_id, BindingPolicy{BootSync::MATTER_WINS, 0, true}) {}

protected:
  // Main loop.
  virtual bool entity_on_() = 0;
  virtual void command_on_(bool on) = 0;

  void setup_() override {
    this->watch_attribute(ON_OFF_CLUSTER, ON_OFF_ATTRIBUTE);
  }
  bool capture_() override { return this->entity_on_(); }
  void write_matter_(const bool &on) override {
    esp_matter_attr_val_t value = esp_matter_bool(on);
    binding_platform()->write_attribute(this->endpoint_id_, ON_OFF_CLUSTER,
                                        ON_OFF_ATTRIBUTE, value, true);
  }
  void on_attribute_(uint32_t cluster, uint32_t attribute,
                     const esp_matter_attr_val_t &v) override {
    if (cluster == ON_OFF_CLUSTER && attribute == ON_OFF_ATTRIBUTE)
      this->submit_inbound(v.val.b);
  }
  void apply_inbound_(const bool &on) override { this->command_on_(on); }
};

} // namespace esphome::matter

#endif // USE_MATTER
