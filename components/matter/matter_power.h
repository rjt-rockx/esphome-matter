#pragma once

// Runtime helpers for the Power Source and Power Topology clusters. See docs/power.md.
//
// Everything here is inline so nothing is emitted (or linked) unless the generated endpoint code uses it. This is
// required because the cluster implementations (PowerSourceServer, ...) are only compiled when the cluster is enabled
// in sdkconfig.

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "esphome/core/log.h"
#include "matter_attributes.h"
#include "matter_endpoints.h"

#ifdef USE_BINARY_SENSOR
#include "esphome/components/binary_sensor/binary_sensor.h"
#endif // USE_BINARY_SENSOR

#include <app/clusters/power-source-server/power-source-server.h>
#include <app/clusters/power-topology-server/PowerTopologyDelegate.h>
#include <esp_matter.h>

#include <cinttypes>
#include <cstdint>
#include <utility>
#include <vector>

namespace esphome::matter {

// Sets the EndpointList attribute of a Power Source cluster after Matter started.
// The attribute is served by PowerSourceServer, esp-matter only creates it (empty).
class MatterPowerSourceEndpointList : public MatterEndpointMappingBase {
public:
  MatterPowerSourceEndpointList(uint16_t endpoint_id,
                                std::vector<uint16_t> endpoints)
      : MatterEndpointMappingBase(endpoint_id),
        endpoints_(std::move(endpoints)) {}

  void initialize() override {
    uint16_t endpoint_id = this->endpoint_id_;
    run_on_matter_thread([endpoint_id, endpoints = this->endpoints_]() {
      std::vector<chip::EndpointId> ids(endpoints.begin(), endpoints.end());
      CHIP_ERROR err =
          chip::app::Clusters::PowerSourceServer::Instance().SetEndpointList(
              endpoint_id,
              chip::Span<chip::EndpointId>(ids.data(), ids.size()));
      if (err != CHIP_NO_ERROR) {
        ESP_LOGE("matter.power", "Failed to set the EndpointList of endpoint %u: %s",
                 endpoint_id, err.AsString());
      }
    });
  }

protected:
  std::vector<uint16_t> endpoints_;
};

#ifdef USE_BINARY_SENSOR
// Publishes an enum8 attribute that is derived from binary_sensors: the value
// of the first sensor (in the given order) that is ON, else the default value.
// As long as no sensor has a state, `unknown_value` is published (or nothing
// for -1, which keeps the initial value). Used for BatChargeLevel (low /
// critical), BatChargeState (charging / full) and the Power Source Status.
class MatterFlagEnumMapping : public MatterEndpointMappingBase {
public:
  struct Flag {
    binary_sensor::BinarySensor *sensor;
    uint8_t value;
  };

  MatterFlagEnumMapping(uint16_t endpoint_id, uint32_t cluster_id,
                        uint32_t attribute_id, uint8_t default_value,
                        int unknown_value, std::vector<Flag> flags)
      : MatterEndpointMappingBase(endpoint_id), cluster_id_(cluster_id),
        attribute_id_(attribute_id), default_value_(default_value),
        unknown_value_(unknown_value), flags_(std::move(flags)) {}

  void initialize() override {
    for (const auto &flag : this->flags_) {
      if (flag.sensor == nullptr)
        continue;
      flag.sensor->add_on_state_callback([this](bool) { this->publish_(); });
    }
    this->publish_();
  }

protected:
  void publish_() {
    uint8_t value = this->default_value_;
    bool any_state = false;
    bool matched = false;
    for (const auto &flag : this->flags_) {
      if (flag.sensor == nullptr || !flag.sensor->has_state())
        continue;
      any_state = true;
      if (!matched && flag.sensor->state) {
        value = flag.value;
        matched = true;
      }
    }
    if (!any_state) {
      if (this->unknown_value_ < 0)
        return;
      value = static_cast<uint8_t>(this->unknown_value_);
    }
    uint16_t endpoint_id = this->endpoint_id_;
    uint32_t cluster_id = this->cluster_id_;
    uint32_t attribute_id = this->attribute_id_;
    run_on_matter_thread([endpoint_id, cluster_id, attribute_id, value]() {
      esp_matter_attr_val_t val = esp_matter_enum8(value);
      esp_err_t err = esp_matter::attribute::update(endpoint_id, cluster_id,
                                                    attribute_id, &val);
      if (err != ESP_OK) {
        ESP_LOGE("matter.power",
                 "Failed to update attribute 0x%08" PRIX32
                 " on cluster 0x%08" PRIX32 ", endpoint %u: %s",
                 attribute_id, cluster_id, endpoint_id, esp_err_to_name(err));
      }
    });
  }

  uint32_t cluster_id_;
  uint32_t attribute_id_;
  uint8_t default_value_;
  int unknown_value_;
  std::vector<Flag> flags_;
};
#endif // USE_BINARY_SENSOR

// Power Topology delegate with fixed lists, used by the SetTopology feature.
class MatterPowerTopologyDelegate
    : public chip::app::Clusters::PowerTopology::Delegate {
public:
  MatterPowerTopologyDelegate(std::vector<uint16_t> available,
                              std::vector<uint16_t> active)
      : available_(std::move(available)), active_(std::move(active)) {}

  CHIP_ERROR GetAvailableEndpointAtIndex(size_t index,
                                         chip::EndpointId &endpoint_id) override {
    if (index >= this->available_.size())
      return CHIP_ERROR_PROVIDER_LIST_EXHAUSTED;
    endpoint_id = this->available_[index];
    return CHIP_NO_ERROR;
  }

  CHIP_ERROR GetActiveEndpointAtIndex(size_t index,
                                      chip::EndpointId &endpoint_id) override {
    if (index >= this->active_.size())
      return CHIP_ERROR_PROVIDER_LIST_EXHAUSTED;
    endpoint_id = this->active_[index];
    return CHIP_NO_ERROR;
  }

protected:
  std::vector<uint16_t> available_;
  std::vector<uint16_t> active_;
};

// Returns the value for power_topology::config_t::delegate. The delegate lives
// as long as the node.
inline void *power_topology_delegate(std::vector<uint16_t> available,
                                     std::vector<uint16_t> active) {
  return static_cast<chip::app::Clusters::PowerTopology::Delegate *>(
      new MatterPowerTopologyDelegate(std::move(available), std::move(active)));
}

} // namespace esphome::matter

#endif // USE_MATTER
