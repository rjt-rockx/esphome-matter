#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include <atomic>
#include <vector>

#include "esphome/core/automation.h"
#include "esphome/core/component.h"

namespace esphome::matter {

class MatterEntity;

class MatterComponent : public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override;

  void set_vendor_id(uint16_t v) { this->vendor_id_ = v; }
  void set_product_id(uint16_t v) { this->product_id_ = v; }
  void set_discriminator(uint16_t v) { this->discriminator_ = v; }
  void set_passcode(uint32_t v) { this->passcode_ = v; }

  void register_entity(MatterEntity *entity) { this->entities_.push_back(entity); }

  void factory_reset();
  void open_commissioning_window(uint16_t seconds = 300);

  Trigger<bool> *get_commission_trigger() { return &this->commission_trigger_; }
  void fire_commission_trigger(bool joined) { this->commission_trigger_.trigger(joined); }

  bool is_commissioned() const { return this->commissioned_.load(); }

 protected:
  uint16_t vendor_id_{0xFFF2};
  uint16_t product_id_{0x8001};
  uint16_t discriminator_{3840};
  uint32_t passcode_{20202021};
  std::atomic<bool> commissioned_{false};
  std::vector<MatterEntity *> entities_;
  Trigger<bool> commission_trigger_;
};

template<typename... Ts> class FactoryResetAction : public Action<Ts...>, public Parented<MatterComponent> {
 public:
  void play(Ts... /*x*/) override { this->parent_->factory_reset(); }
};

template<typename... Ts> class OpenCommissioningWindowAction : public Action<Ts...>, public Parented<MatterComponent> {
 public:
  void play(Ts... /*x*/) override { this->parent_->open_commissioning_window(); }
};

}  // namespace esphome::matter

#endif
