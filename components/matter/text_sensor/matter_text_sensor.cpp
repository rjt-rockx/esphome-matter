#include "matter_text_sensor.h"

#if defined(USE_MATTER) && defined(USE_TEXT_SENSOR)
#include "esphome/core/log.h"

#include <string>

namespace esphome::matter {

static const char *const TAG = "matter.text_sensor";

void MatterTextSensor::setup() {
  MatterCommissioning::instance().add_state_callback(
      [this]() { this->update_(); });
  this->update_();
}

void MatterTextSensor::update_() {
  const CommissioningState &s = MatterCommissioning::instance().state();
  if (!s.valid)
    return;
  std::string value;
  switch (this->type_) {
  case MatterTextSensorType::PAIRING_CODE:
    value = s.manual_code;
    break;
  case MatterTextSensorType::QR_CODE:
    value = s.qr_code;
    break;
  case MatterTextSensorType::FABRIC_COUNT:
    value = std::to_string(s.fabric_count);
    break;
  case MatterTextSensorType::COMMISSIONING_STATE:
    if (s.window_open)
      value = s.fabric_count == 0 ? "commissioning" : "window_open";
    else
      value = s.fabric_count == 0 ? "uncommissioned" : "commissioned";
    break;
  }
  if (!this->has_state() || this->state != value)
    this->publish_state(value);
}

void MatterTextSensor::dump_config() {
  LOG_TEXT_SENSOR("", "Matter Text Sensor", this);
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_TEXT_SENSOR
