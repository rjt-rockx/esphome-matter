#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_TEXT_SENSOR)
#include "esphome/components/matter/matter_commissioning.h"
#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/core/component.h"

namespace esphome::matter {

enum class MatterTextSensorType : uint8_t {
  PAIRING_CODE,
  QR_CODE,
  COMMISSIONING_STATE,
  FABRIC_COUNT,
};

// Publishes commissioning information. The pairing/QR code sensors are empty
// unless a commissioning window is open and the device knows its passcode.
class MatterTextSensor : public text_sensor::TextSensor, public Component {
public:
  void set_type(MatterTextSensorType type) { this->type_ = type; }
  void setup() override;
  void dump_config() override;

protected:
  void update_();
  MatterTextSensorType type_{MatterTextSensorType::COMMISSIONING_STATE};
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_TEXT_SENSOR
