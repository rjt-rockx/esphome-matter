#pragma once

// Included into every connectedhomeip translation unit through
// CONFIG_CHIP_PROJECT_CONFIG (see network_config.py). Plain #defines only.

// Subscription resumption retry schedule.
//
// After a reboot CHIP resumes persisted subscriptions once, after the largest
// persisted MinInterval (not configurable, chosen by the controller). If that
// first CASE attempt fails (controller briefly unreachable, DNS-SD not yet
// converged, Thread still attaching) the retry waits
//   MIN_RETRY_INTERVAL + Fibonacci(n) * WAIT_TIME_MULTIPLIER
// seconds. The defaults (300 s + 300 s * Fibonacci(n), max 6 h) leave a device
// silent for at least 5 minutes after one failed attempt.
//
// With these values the retries come after 5, 15, 15, 25, 35, 55, 85, 135, 215,
// 345 and 555 s, then CHIP drops the persisted subscription (the controller
// re-subscribes by itself when it notices the device again).
//
// CHIP requires:
//   MIN + Fib(MAX_STEP) * MULT < MAX < MIN + Fib(MAX_STEP + 1) * MULT
//   5 + 55 * 10 = 555 < 600 < 5 + 89 * 10 = 895
#define CHIP_CONFIG_SUBSCRIPTION_TIMEOUT_RESUMPTION_MIN_RETRY_INTERVAL_SECS 5
#define CHIP_CONFIG_SUBSCRIPTION_TIMEOUT_RESUMPTION_WAIT_TIME_MULTIPLIER_SECS 10
#define CHIP_CONFIG_SUBSCRIPTION_TIMEOUT_RESUMPTION_MAX_FIBONACCI_STEP_INDEX 10
#define CHIP_CONFIG_SUBSCRIPTION_TIMEOUT_RESUMPTION_MAX_RETRY_INTERVAL_SECS 600
