#pragma once
// Host test stub for the small part of esp-matter the pure logic touches.
#include <cstddef>
#include <cstdint>
#include <type_traits>

using esp_err_t = int;
constexpr esp_err_t ESP_OK = 0;
constexpr esp_err_t ESP_FAIL = -1;

namespace esp_matter {
struct endpoint_t;
struct cluster_t;
namespace attribute {
enum callback_type_t { PRE_UPDATE, POST_UPDATE, READ, WRITE };
}
}  // namespace esp_matter

enum esp_matter_val_type_t {
  ESP_MATTER_VAL_TYPE_INVALID,
  ESP_MATTER_VAL_TYPE_BOOLEAN,
  ESP_MATTER_VAL_TYPE_INTEGER,
  ESP_MATTER_VAL_TYPE_FLOAT,
  ESP_MATTER_VAL_TYPE_INT8,
  ESP_MATTER_VAL_TYPE_UINT8,
  ESP_MATTER_VAL_TYPE_INT16,
  ESP_MATTER_VAL_TYPE_UINT16,
  ESP_MATTER_VAL_TYPE_INT32,
  ESP_MATTER_VAL_TYPE_UINT32,
  ESP_MATTER_VAL_TYPE_INT64,
  ESP_MATTER_VAL_TYPE_UINT64,
  ESP_MATTER_VAL_TYPE_ENUM8,
  ESP_MATTER_VAL_TYPE_ENUM16,
  ESP_MATTER_VAL_TYPE_BITMAP8,
  ESP_MATTER_VAL_TYPE_BITMAP16,
  ESP_MATTER_VAL_TYPE_BITMAP32,
  ESP_MATTER_VAL_TYPE_CHAR_STRING,
  ESP_MATTER_VAL_TYPE_LONG_CHAR_STRING,
  ESP_MATTER_VAL_TYPE_OCTET_STRING,
  ESP_MATTER_VAL_TYPE_LONG_OCTET_STRING,
  ESP_MATTER_VAL_TYPE_ARRAY,
};

// Real esp-matter nullable<T> encodes null as a sentinel; the stub keeps a flag.
template<typename T> class nullable {
 public:
  nullable() = default;
  nullable(T v) : v_(v), has_(true) {}
  nullable &operator=(T v) {
    v_ = v;
    has_ = true;
    return *this;
  }
  bool has_value() const { return has_; }
  T value() const { return v_; }

 private:
  T v_{};
  bool has_{false};
};

struct esp_matter_attr_val_t {
  esp_matter_val_type_t type{ESP_MATTER_VAL_TYPE_INVALID};
  bool null{false};
  union {
    bool b;
    float f;
    int8_t i8;
    uint8_t u8;
    int16_t i16;
    uint16_t u16;
    int32_t i32;
    uint32_t u32;
    int64_t i64;
    uint64_t u64;
    struct {
      uint8_t *b;
      uint16_t s;
    } a;
  } val{};
  esp_matter_val_type_t get_base_type() const { return type; }
  bool is_null() const { return null; }
};

namespace host_stub {
template<typename T> esp_matter_attr_val_t make(esp_matter_val_type_t t, nullable<T> v) {
  esp_matter_attr_val_t r;
  r.type = t;
  r.null = !v.has_value();
  if (v.has_value()) {
    if constexpr (std::is_same_v<T, float>)
      r.val.f = v.value();
    else if constexpr (std::is_same_v<T, int16_t>)
      r.val.i16 = v.value();
    else if constexpr (std::is_same_v<T, uint16_t>)
      r.val.u16 = v.value();
    else if constexpr (std::is_same_v<T, uint8_t>)
      r.val.u8 = v.value();
    else if constexpr (std::is_same_v<T, uint32_t>)
      r.val.u32 = v.value();
    else if constexpr (std::is_same_v<T, int64_t>)
      r.val.i64 = v.value();
  }
  return r;
}
}  // namespace host_stub

inline esp_matter_attr_val_t esp_matter_bool(bool v) {
  esp_matter_attr_val_t r;
  r.type = ESP_MATTER_VAL_TYPE_BOOLEAN;
  r.val.b = v;
  return r;
}
inline esp_matter_attr_val_t esp_matter_bitmap8(uint8_t v) {
  esp_matter_attr_val_t r;
  r.type = ESP_MATTER_VAL_TYPE_BITMAP8;
  r.val.u8 = v;
  return r;
}
inline esp_matter_attr_val_t esp_matter_nullable_float(nullable<float> v) {
  return host_stub::make(ESP_MATTER_VAL_TYPE_FLOAT, v);
}
inline esp_matter_attr_val_t esp_matter_nullable_int16(nullable<int16_t> v) {
  return host_stub::make(ESP_MATTER_VAL_TYPE_INT16, v);
}
inline esp_matter_attr_val_t esp_matter_nullable_uint16(nullable<uint16_t> v) {
  return host_stub::make(ESP_MATTER_VAL_TYPE_UINT16, v);
}
inline esp_matter_attr_val_t esp_matter_nullable_uint8(nullable<uint8_t> v) {
  return host_stub::make(ESP_MATTER_VAL_TYPE_UINT8, v);
}
inline esp_matter_attr_val_t esp_matter_nullable_int64(nullable<int64_t> v) {
  return host_stub::make(ESP_MATTER_VAL_TYPE_INT64, v);
}
inline esp_matter_attr_val_t esp_matter_nullable_uint32(nullable<uint32_t> v) {
  return host_stub::make(ESP_MATTER_VAL_TYPE_UINT32, v);
}
inline esp_matter_attr_val_t esp_matter_enum8(uint8_t v) {
  esp_matter_attr_val_t r;
  r.type = ESP_MATTER_VAL_TYPE_ENUM8;
  r.val.u8 = v;
  return r;
}
inline esp_matter_attr_val_t esp_matter_invalid(void *) { return esp_matter_attr_val_t(); }
