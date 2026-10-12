#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER_PLAN

#include <cstddef>
#include <cstdint>

namespace esphome::matter {

// One row of the `matter: expose:` plan. The table is emitted by codegen (expose/plan_table.py)
// as a constexpr array in flash and only when the logger level shows dump_config output.
struct PlanRow {
  uint16_t endpoint;
  const char *device_types; // comma separated device type names
  const char *entities;     // comma separated entity ids
  const char *label;        // "" if none
};

void set_plan(const PlanRow *rows, size_t count);
// Logs the plan with ESP_LOGCONFIG. Called from MatterComponent::dump_config.
void dump_plan();

} // namespace esphome::matter

#endif // USE_MATTER_PLAN
