// Host tests for the pure functions in components/matter/matter_conversions.cpp.
#include "matter_conversions.h"
#include "test_framework.h"

#include <cmath>

using namespace esphome::matter::conversion;

TEST(conv_temperature) {
  auto v = temperature(21.5f);
  CHECK(!v.is_null());
  CHECK_EQ(v.val.i16, (int16_t) 2150);
  CHECK(temperature(-300.0f).is_null());  // below absolute zero
  CHECK(temperature(400.0f).is_null());   // does not fit int16
  CHECK(temperature(NAN).is_null());
}

TEST(conv_percent_and_percentage) {
  CHECK_EQ(percent(50.4f).val.u8, (uint8_t) 50);
  CHECK(percent(101.0f).is_null());
  CHECK_EQ(percentage(12.34f).val.u16, (uint16_t) 1234);
  CHECK(percentage(-1.0f).is_null());
}

TEST(conv_battery_percent_is_half_percent) {
  CHECK_EQ(battery_percent(100.0f).val.u8, (uint8_t) 200);
  CHECK_EQ(battery_percent(50.0f).val.u8, (uint8_t) 100);
  CHECK_EQ(battery_percent(150.0f).val.u8, (uint8_t) 200);  // clamped
  CHECK(battery_percent(NAN).is_null());
}

TEST(conv_scaled_int64) {
  CHECK_EQ(watts(1.5f).val.i64, (int64_t) 1500);
  CHECK_EQ(volts(230.0f).val.i64, (int64_t) 230000);
  CHECK_EQ(kilowatt_hours(1.0f).val.i64, (int64_t) 1000000);
  CHECK(watts(INFINITY).is_null());
  CHECK_EQ(power_factor(0.5f).val.i64, (int64_t) 5000);
  CHECK(power_factor(1.5f).is_null());
}

TEST(conv_illuminance_log_scale) {
  CHECK_EQ(illuminance(0.0f).val.u16, (uint16_t) 0);
  CHECK_EQ(illuminance(1.0f).val.u16, (uint16_t) 1);  // 10000*log10(1)+1
  CHECK_EQ(illuminance(10.0f).val.u16, (uint16_t) 10001);
  CHECK(illuminance(-1.0f).is_null());
}

TEST(conv_flow_pressure_occupancy) {
  CHECK_EQ(flow(2.5f).val.u16, (uint16_t) 25);
  CHECK(flow(-1.0f).is_null());
  CHECK_EQ(pressure(1013.0f).val.i16, (int16_t) 1013);
  CHECK_EQ(occupancy(true).val.u8, (uint8_t) 1);
  CHECK_EQ(boolean_state(true).val.b, true);
}

TEST(conv_attribute_value_extraction) {
  bool b = false;
  CHECK(convert_attribute_value(esp_matter_bool(true), b) && b);
  float f = 0;
  CHECK(!convert_attribute_value(esp_matter_bool(true), f));
  uint64_t u = 0;
  CHECK(convert_attribute_value(esp_matter_bitmap8(7), u) && u == 7);
  int64_t i = 0;
  CHECK(!convert_attribute_value(esp_matter_bitmap8(7), i));
}

TEST(conv_brightness) {
  using namespace to_matter;
  CHECK_EQ(brightness(0.0f), (uint8_t) 1);  // never 0 (that would mean off)
  CHECK_EQ(brightness(1.0f), (uint8_t) 254);
  CHECK_EQ(brightness(0.5f), (uint8_t) 127);
  CHECK_NEAR(from_matter::brightness(127), 0.5, 0.005);
}

TEST(conv_color_roundtrip) {
  uint16_t x = 0, y = 0;
  CHECK(!to_matter::color(0.0f, 0.0f, 0.0f, x, y));  // black has no chromaticity
  CHECK(to_matter::color(1.0f, 0.0f, 0.0f, x, y));
  float r, g, b;
  CHECK(from_matter::color(x, y, r, g, b));
  CHECK_NEAR(r, 1.0, 0.02);
  CHECK(g < 0.05f);
  CHECK(!from_matter::color(100, 0, r, g, b));  // y == 0
}

TEST(conv_hue_saturation) {
  uint8_t hue = 0, sat = 0;
  uint16_t ehue = 0;
  CHECK(!to_matter::hue_saturation(0.0f, 0.0f, 0.0f, hue, sat, ehue));
  CHECK(to_matter::hue_saturation(0.0f, 1.0f, 0.0f, hue, sat, ehue));  // green = 120 deg
  CHECK_NEAR(hue, 120.0 / 360.0 * 254.0, 1.5);
  CHECK_EQ(sat, (uint8_t) 254);
  float r, g, b;
  CHECK(from_matter::hue_saturation(hue, sat, r, g, b));
  CHECK_NEAR(g, 1.0, 0.01);
  CHECK(r < 0.05f && b < 0.05f);
}

TEST(conv_color_temperature) {
  CHECK_EQ(to_matter::color_temperature(250.0f), (uint16_t) 250);
}
