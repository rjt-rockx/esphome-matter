#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include <esp_matter.h>

#include <cstdint>
#include <string>

namespace esphome::matter::conversion {

bool convert_attribute_value(const esp_matter_attr_val_t &value, bool &out);
bool convert_attribute_value(const esp_matter_attr_val_t &value, float &out);
bool convert_attribute_value(const esp_matter_attr_val_t &value, int64_t &out);
bool convert_attribute_value(const esp_matter_attr_val_t &value, uint64_t &out);
bool convert_attribute_value(const esp_matter_attr_val_t &value,
                             std::string &out);

esp_matter_attr_val_t ampere(float value);
// PowerSource::BatPercentRemaining. Input in percent, output in half percent.
esp_matter_attr_val_t battery_percent(float value);
esp_matter_attr_val_t boolean_state(bool value);
esp_matter_attr_val_t concentration(float value);
esp_matter_attr_val_t flow(float value);
esp_matter_attr_val_t frequency(float value);
esp_matter_attr_val_t illuminance(float value);
// Energy in kWh to EnergyMeasurementStruct::energy (mWh, nullable int64).
esp_matter_attr_val_t kilowatt_hours(float value);
esp_matter_attr_val_t occupancy(bool value);
esp_matter_attr_val_t percentage(float value);
// ElectricalPowerMeasurement::PowerFactor. Input is the ratio (-1.0 to 1.0),
// output is in 1/100 % as a nullable int64.
esp_matter_attr_val_t power_factor(float value);
// Input must be in hPa, which is numerically Matter's 0.1 kPa.
esp_matter_attr_val_t pressure(float value);
esp_matter_attr_val_t temperature(float value);
esp_matter_attr_val_t volts(float value);
esp_matter_attr_val_t watts(float value);

// New namespace structure. I'll move the other converters later.
namespace to_matter {

uint8_t brightness(float brightness);
bool color(float red, float green, float blue, uint16_t &x, uint16_t &y);
uint16_t color_temperature(float color_temperature);

} // namespace to_matter

namespace from_matter {

float brightness(uint8_t level);
bool color(uint16_t x, uint16_t y, float &red, float &green, float &blue);

} // namespace from_matter

} // namespace esphome::matter::conversion

#endif // USE_MATTER
