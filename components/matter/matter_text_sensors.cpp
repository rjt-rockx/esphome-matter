#include "matter_text_sensors.h"

#ifdef USE_MATTER

#ifdef USE_TEXT_SENSOR
#include "matter_attributes.h"
#include "matter_component.h"
#include "matter_thread.h"

#include <app/server/Server.h>
#include <platform/CHIPDeviceLayer.h>

#include <cinttypes>
#include <cstdio>
#include <vector>
#endif

namespace esphome::matter {

std::string format_manual_pairing_code(const std::string &code) {
  if (code.length() != 11)
    return code;
  return code.substr(0, 4) + "-" + code.substr(4, 3) + "-" + code.substr(7, 4);
}

#ifdef USE_TEXT_SENSOR
void MatterComponent::schedule_fabric_sensor_update() {
  // Thread agnostic: publish_fabric_sensors_() only schedules work.
  this->publish_fabric_sensors_();
}

MatterFabricSensorRegistration &
MatterComponent::get_fabric_sensors_(size_t slot) {
  for (auto &sensors : this->fabric_sensors_) {
    if (sensors.slot == slot)
      return sensors;
  }
  this->fabric_sensors_.emplace_back();
  this->fabric_sensors_.back().slot = slot;
  return this->fabric_sensors_.back();
}

void MatterComponent::set_fabric_compressed_id_sensor(
    size_t slot, text_sensor::TextSensor *sensor) {
  this->get_fabric_sensors_(slot).compressed_id = sensor;
}

void MatterComponent::set_fabric_id_sensor(size_t slot,
                                           text_sensor::TextSensor *sensor) {
  this->get_fabric_sensors_(slot).fabric_id = sensor;
}

void MatterComponent::set_fabric_label_sensor(size_t slot,
                                              text_sensor::TextSensor *sensor) {
  this->get_fabric_sensors_(slot).label = sensor;
}

void MatterComponent::set_fabric_node_id_sensor(
    size_t slot, text_sensor::TextSensor *sensor) {
  this->get_fabric_sensors_(slot).node_id = sensor;
}

void MatterComponent::set_fabric_vendor_id_sensor(
    size_t slot, text_sensor::TextSensor *sensor) {
  this->get_fabric_sensors_(slot).vendor_id = sensor;
}

namespace {
struct FabricSensorValues {
  size_t slot;
  bool present{false};
  std::string compressed_id;
  std::string fabric_id;
  std::string label;
  std::string node_id;
  std::string vendor_id;
};

void publish_values(const std::vector<MatterFabricSensorRegistration> &sensors,
                    const std::vector<FabricSensorValues> &values) {
  for (size_t i = 0; i < values.size() && i < sensors.size(); i++) {
    const auto &s = sensors[i];
    const auto &value = values[i];
    if (s.compressed_id != nullptr &&
        (value.present || s.compressed_id->has_state()))
      s.compressed_id->publish_state(value.compressed_id);
    if (s.fabric_id != nullptr && (value.present || s.fabric_id->has_state()))
      s.fabric_id->publish_state(value.fabric_id);
    if (s.label != nullptr && (value.present || s.label->has_state()))
      s.label->publish_state(value.label);
    if (s.node_id != nullptr && (value.present || s.node_id->has_state()))
      s.node_id->publish_state(value.node_id);
    if (s.vendor_id != nullptr && (value.present || s.vendor_id->has_state()))
      s.vendor_id->publish_state(value.vendor_id);
  }
}
} // namespace

// Callable from any thread; never takes the CHIP lock. The fabric table is read
// on the CHIP task (run_on_matter_thread) and the sensors are published on the
// main loop (defer), so neither thread ever waits for the other.
void MatterComponent::publish_fabric_sensors_() {
  std::vector<FabricSensorValues> values;
  values.reserve(this->fabric_sensors_.size());
  for (const auto &sensors : this->fabric_sensors_) {
    values.emplace_back();
    values.back().slot = sensors.slot;
  }

  run_on_matter_thread([this, values = std::move(values)]() mutable {
    MATTER_ASSERT_CHIP_THREAD();
    size_t slot = 0;
    for (const auto &fabric : chip::Server::GetInstance().GetFabricTable()) {
      for (auto &value : values) {
        if (value.slot != slot)
          continue;
        value.present = true;
        char compressed_id[19];
        snprintf(compressed_id, sizeof(compressed_id), "0x%016" PRIX64,
                 fabric.GetCompressedFabricId());
        value.compressed_id = compressed_id;
        char fabric_id[19];
        snprintf(fabric_id, sizeof(fabric_id), "0x%016" PRIX64,
                 fabric.GetFabricId());
        value.fabric_id = fabric_id;
        const chip::CharSpan label = fabric.GetFabricLabel();
        value.label.assign(label.data(), label.size());
        char node_id[19];
        snprintf(node_id, sizeof(node_id), "0x%016" PRIX64, fabric.GetNodeId());
        value.node_id = node_id;
        char vendor_id[7];
        snprintf(vendor_id, sizeof(vendor_id), "0x%04" PRIX16,
                 static_cast<uint16_t>(fabric.GetVendorId()));
        value.vendor_id = vendor_id;
        break;
      }
      slot++;
    }
    this->defer([this, values = std::move(values)]() {
      MATTER_ASSERT_MAIN_LOOP();
      publish_values(this->fabric_sensors_, values);
    });
  });
}
#endif

} // namespace esphome::matter

#endif // USE_MATTER
