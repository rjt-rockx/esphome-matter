#include "matter_plan.h"
#ifdef USE_MATTER_PLAN

#include "esphome/core/log.h"

namespace esphome::matter {

static const char *const TAG = "matter";

static const PlanRow *plan_rows =
    nullptr; // NOLINT(cppcoreguidelines-avoid-non-const-global-variables)
static size_t plan_count =
    0; // NOLINT(cppcoreguidelines-avoid-non-const-global-variables)

void set_plan(const PlanRow *rows, size_t count) {
  plan_rows = rows;
  plan_count = count;
}

void dump_plan() {
  if (plan_rows == nullptr) {
    return;
  }
  ESP_LOGCONFIG(TAG, "  Exposed endpoints:");
  for (size_t i = 0; i < plan_count; i++) {
    const PlanRow &row = plan_rows[i];
    ESP_LOGCONFIG(TAG, "    [%u] %s: %s%s%s", row.endpoint, row.device_types,
                  row.entities, row.label[0] ? ", label: " : "", row.label);
  }
}

} // namespace esphome::matter

#endif // USE_MATTER_PLAN
