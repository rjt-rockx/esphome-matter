// Host stub implementations + the test runner main().
#include "esphome/core/log.h"
#include "test_framework.h"

#include <cstdarg>
#include <cstdio>
#include <cstring>

namespace host_stub {

std::vector<LogLine> &log_lines() {
  static std::vector<LogLine> lines;
  return lines;
}

void host_log(char level, const char *tag, const char *fmt, ...) {
  char buf[512];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  log_lines().push_back({level, std::string(tag) + ": " + buf});
}

}  // namespace host_stub

int main(int argc, char **argv) {
  const char *filter = argc > 1 ? argv[1] : nullptr;
  int ran = 0;
  for (auto &c : tf::cases()) {
    if (filter && !std::strstr(c.name, filter))
      continue;
    int before = tf::failures();
    host_stub::log_lines().clear();
    c.fn();
    std::printf("[%s] %s\n", tf::failures() == before ? " ok " : "FAIL", c.name);
    ran++;
  }
  std::printf("%d tests, %d failed checks\n", ran, tf::failures());
  return tf::failures() == 0 && ran > 0 ? 0 : 1;
}
