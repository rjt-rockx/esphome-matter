#pragma once

#include "esphome/core/automation.h"
#include "esphome/core/defines.h"
#include "esphome/core/helpers.h"
#ifdef USE_MATTER

#include "matter_conversions.h"

#include <esp_matter.h>

#include <cstdint>
#include <functional>
#include <string>
#include <utility>
#include <variant>

namespace esphome::matter {

class MatterComponent;

void defer_to_main_loop(MatterComponent *component, std::function<void()> &&f);

// Runs f on the CHIP task, in order with SystemLayer().ScheduleLambda() work.
// Unlike ScheduleLambda the capture size is not limited to
// CHIP_CONFIG_LAMBDA_EVENT_SIZE (24 bytes), at the price of one heap allocation.
// Safe to call from the main loop; never blocks.
void run_on_matter_thread(std::function<void()> &&f);

using MatterAttributeValue =
    std::variant<bool, float, int64_t, uint64_t, std::string>;

void set_attribute_value(uint16_t endpoint_id, uint32_t cluster_id,
                         uint32_t attribute_id, MatterAttributeValue value);
void replay_attribute_triggers(MatterComponent *component);

using MatterAttributeCallback =
    std::function<void(const esp_matter_attr_val_t &)>;

struct MatterAttributeCallbackRegistration {
  uint16_t endpoint_id;
  uint32_t cluster_id;
  uint32_t attribute_id;
  MatterAttributeCallback callback;

  bool matches(uint16_t endpoint, uint32_t cluster, uint32_t attribute) const {
    return this->endpoint_id == endpoint && this->cluster_id == cluster &&
           this->attribute_id == attribute;
  }
};

// Marks esp-matter attribute writes that ESPHome does itself, so that the
// resulting POST_UPDATE callbacks are not treated as controller changes.
//
// esp_matter::attribute::report() skips the callbacks only for attributes that
// are NOT writable (OnOff, CurrentLevel, the current colour, ...). For writable
// attributes (Thermostat SystemMode and setpoints, OnOff StartUpOnOff,
// LevelControl OnLevel, WindowCovering Mode, ...) esp-matter routes the write
// through the data model provider, which always runs the PRE_UPDATE and
// POST_UPDATE callbacks. endpoint_attribute_update_cb() drops every callback
// that arrives while a guard is alive, so an ESPHome -> Matter push can never
// loop back into ESPHome. Use it around every attribute::report()/update() that
// mirrors ESPHome state. Only meaningful on the Matter thread, where esp-matter
// calls the callbacks synchronously from within the write.
//
// Code driven clusters must not be written through the attribute store at all,
// use the cluster object setters of the registry instead.
class MatterPushGuard {
public:
  MatterPushGuard();
  ~MatterPushGuard();
  MatterPushGuard(const MatterPushGuard &) = delete;
  MatterPushGuard &operator=(const MatterPushGuard &) = delete;

  static bool active();
};

class MatterAttributeDispatchGuard {
public:
  MatterAttributeDispatchGuard(uint16_t endpoint_id, uint32_t cluster_id,
                               uint32_t attribute_id);
  ~MatterAttributeDispatchGuard();

protected:
  bool previous_active_;
  uint16_t previous_endpoint_id_;
  uint32_t previous_cluster_id_;
  uint32_t previous_attribute_id_;
};

class MatterAttributeTriggerBase {
public:
  MatterAttributeTriggerBase(uint16_t endpoint_id, uint32_t cluster_id,
                             uint32_t attribute_id)
      : endpoint_id_(endpoint_id), cluster_id_(cluster_id),
        attribute_id_(attribute_id) {}
  virtual ~MatterAttributeTriggerBase() = default;

  bool matches(uint16_t endpoint_id, uint32_t cluster_id,
               uint32_t attribute_id) const {
    return this->endpoint_id_ == endpoint_id &&
           this->cluster_id_ == cluster_id &&
           this->attribute_id_ == attribute_id;
  }
  uint16_t endpoint_id() const { return this->endpoint_id_; }
  uint32_t cluster_id() const { return this->cluster_id_; }
  uint32_t attribute_id() const { return this->attribute_id_; }
  virtual void dispatch(const esp_matter_attr_val_t &value) = 0;

protected:
  uint16_t endpoint_id_;
  uint32_t cluster_id_;
  uint32_t attribute_id_;
};

template <typename T>
class MatterAttributeTrigger : public Trigger<T>,
                               public MatterAttributeTriggerBase {
public:
  MatterAttributeTrigger(MatterComponent *parent, uint16_t endpoint_id,
                         uint32_t cluster_id, uint32_t attribute_id)
      : MatterAttributeTriggerBase(endpoint_id, cluster_id, attribute_id),
        parent_(parent) {}

  void dispatch(const esp_matter_attr_val_t &value) override {
    if (value.is_null())
      return;
    T converted{};
    if (!conversion::convert_attribute_value(value, converted))
      return;

    {
      LockGuard guard(this->pending_mutex_);
      this->pending_value_ = std::move(converted);
      if (this->dispatch_pending_)
        return;
      this->dispatch_pending_ = true;
    }

    defer_to_main_loop(this->parent_, [this]() {
      T pending_value;
      {
        LockGuard guard(this->pending_mutex_);
        pending_value = this->pending_value_;
        this->dispatch_pending_ = false;
      }
      MatterAttributeDispatchGuard dispatch_guard(
          this->endpoint_id_, this->cluster_id_, this->attribute_id_);
      this->trigger(pending_value);
    });
  }

protected:
  MatterComponent *parent_;
  Mutex pending_mutex_;
  T pending_value_{};
  bool dispatch_pending_{false};
};

esp_err_t
endpoint_attribute_update_cb(esp_matter::attribute::callback_type_t type,
                             uint16_t endpoint_id, uint32_t cluster_id,
                             uint32_t attribute_id, esp_matter_attr_val_t *val,
                             void *priv_data);

} // namespace esphome::matter

#endif // USE_MATTER
