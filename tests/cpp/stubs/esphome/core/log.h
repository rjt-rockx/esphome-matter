#pragma once
// Host test stub: ESP_LOGx routes to host_log(), which tests can inspect.
#include <string>
#include <vector>

namespace host_stub {
struct LogLine {
  char level;  // 'E' 'W' 'I' 'D' 'V' 'C'
  std::string text;
};
std::vector<LogLine> &log_lines();
void host_log(char level, const char *tag, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
}  // namespace host_stub

#define ESP_LOGE(tag, ...) ::host_stub::host_log('E', tag, __VA_ARGS__)
#define ESP_LOGW(tag, ...) ::host_stub::host_log('W', tag, __VA_ARGS__)
#define ESP_LOGI(tag, ...) ::host_stub::host_log('I', tag, __VA_ARGS__)
#define ESP_LOGD(tag, ...) ::host_stub::host_log('D', tag, __VA_ARGS__)
#define ESP_LOGV(tag, ...) ::host_stub::host_log('V', tag, __VA_ARGS__)
#define ESP_LOGCONFIG(tag, ...) ::host_stub::host_log('C', tag, __VA_ARGS__)
