#pragma once
// Tiny test runner: TEST(name) { CHECK(cond); CHECK_EQ(a, b); }
#include <cstdint>
#include <cstdio>
#include <functional>
#include <sstream>
#include <string>
#include <vector>

namespace tf {
struct Case {
  const char *name;
  std::function<void()> fn;
};
inline std::vector<Case> &cases() {
  static std::vector<Case> c;
  return c;
}
inline int &failures() {
  static int f = 0;
  return f;
}
struct Register {
  Register(const char *n, std::function<void()> f) { cases().push_back({n, std::move(f)}); }
};
template<typename T> std::string show(const T &v) {
  std::ostringstream o;
  o << v;
  return o.str();
}
inline std::string show(bool v) { return v ? "true" : "false"; }
inline std::string show(uint8_t v) { return std::to_string(v); }
inline std::string show(const std::vector<int> &v) {
  std::string s = "[";
  for (size_t i = 0; i < v.size(); i++)
    s += (i ? "," : "") + std::to_string(v[i]);
  return s + "]";
}
}  // namespace tf

#define TEST(name) \
  static void test_##name(); \
  static tf::Register reg_##name(#name, test_##name); \
  static void test_##name()

#define CHECK(cond) \
  do { \
    if (!(cond)) { \
      std::printf("    FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
      tf::failures()++; \
    } \
  } while (0)

#define CHECK_EQ(a, b) \
  do { \
    auto va_ = (a); \
    auto vb_ = (b); \
    if (!(va_ == vb_)) { \
      std::printf("    FAIL %s:%d: %s == %s (%s vs %s)\n", __FILE__, __LINE__, #a, #b, tf::show(va_).c_str(), \
                  tf::show(vb_).c_str()); \
      tf::failures()++; \
    } \
  } while (0)

#define CHECK_NEAR(a, b, eps) \
  do { \
    double va_ = (a), vb_ = (b); \
    if (!(va_ - vb_ <= (eps) && vb_ - va_ <= (eps))) { \
      std::printf("    FAIL %s:%d: %s ~ %s (%g vs %g)\n", __FILE__, __LINE__, #a, #b, va_, vb_); \
      tf::failures()++; \
    } \
  } while (0)
