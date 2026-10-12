#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include <cstdint>

// Thread discipline checks. The component runs on two threads: the ESPHome main
// loop and the CHIP task. See docs/dev/threading.md and
// docs/dev/binding-framework.md.
//
//   MATTER_ASSERT_CHIP_THREAD()  esp-matter / CHIP calls, store writes
//   MATTER_ASSERT_MAIN_LOOP()    ESPHome entity access
//
// Both compile to nothing unless USE_MATTER_THREAD_ASSERTS is defined (QEMU and
// test builds). A violation logs the call site and aborts, so it is visible in
// a runtime test instead of being a rare race.
namespace esphome::matter::thread {

// Records the calling task as the main loop. Idempotent; the first caller wins.
// MatterBindingBase::initialize() calls it. Until it ran, is_main_loop() is true.
void capture_main_loop();

// True while the calling task holds the CHIP stack lock, which is the case
// inside ScheduleWork() callbacks and esp-matter attribute callbacks.
bool is_chip_thread();
// True on the task that called capture_main_loop().
bool is_main_loop();

void violation(const char *expected, const char *file, int line);

#ifdef MATTER_HOST_TEST
// Host tests pretend to be one of the two threads.
enum class HostRole : uint8_t { ANY, MAIN, CHIP };
void host_set_role(HostRole role);
uint32_t host_violation_count();
#endif

} // namespace esphome::matter::thread

#ifdef USE_MATTER_THREAD_ASSERTS
#define MATTER_ASSERT_CHIP_THREAD()                                            \
  do {                                                                         \
    if (!::esphome::matter::thread::is_chip_thread())                          \
      ::esphome::matter::thread::violation("CHIP thread", __FILE__, __LINE__); \
  } while (0)
#define MATTER_ASSERT_MAIN_LOOP()                                              \
  do {                                                                         \
    if (!::esphome::matter::thread::is_main_loop())                            \
      ::esphome::matter::thread::violation("main loop", __FILE__, __LINE__);   \
  } while (0)
#else
#define MATTER_ASSERT_CHIP_THREAD() ((void) 0)
#define MATTER_ASSERT_MAIN_LOOP() ((void) 0)
#endif

#endif // USE_MATTER
