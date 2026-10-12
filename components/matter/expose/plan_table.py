"""Codegen of the plan table printed by MatterComponent::dump_config (matter_plan.cpp).

The table is a constexpr array in flash. It is skipped when the logger level hides dump_config
output (below CONFIG) or there is no logger, so such builds pay nothing.
"""

import esphome.codegen as cg
from esphome.core import CORE
from esphome.cpp_generator import RawExpression, RawStatement

from ..const import CONF_MATTER
from .plan import KEY_EXPOSE_PLAN, ExposePlan

# Logger levels that print dump_config (ESP_LOGCONFIG) output.
LEVELS_WITH_CONFIG = ("CONFIG", "DEBUG", "VERBOSE", "VERY_VERBOSE")
ROW_BYTES = 16  # uint16_t + three 32-bit pointers on the Xtensa/RISC-V targets
CODE_BYTES = 150  # dump_plan() and set_plan(), once (estimate)

Row = tuple[int, str, str, str]


def logs_config() -> bool:
    logger = (CORE.config or {}).get("logger")
    return logger is not None and logger.get("level", "DEBUG") in LEVELS_WITH_CONFIG


def plan_rows(plan: ExposePlan) -> list[Row]:
    """(endpoint, device types, entity ids, label) for every lowered endpoint, ascending."""
    return [
        (
            ep.number,
            ",".join(ep.device_types),
            ",".join([*ep.entities, *(f"+{a}" for a in ep.attached)]),
            ep.label or "",
        )
        for ep in sorted(plan.endpoints, key=lambda e: e.number)
    ]


def flash_estimate(rows: list[Row]) -> int:
    """Bytes of flash the table costs: the rows, their strings, and the dump code once."""
    if not rows:
        return 0
    strings = sum(len(s.encode()) + 1 for _, a, b, c in rows for s in (a, b, c) if s)
    return CODE_BYTES + len(rows) * ROW_BYTES + strings + 1  # +1: the shared "" literal


def _literal(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def plan_table_code(rows: list[Row]) -> str:
    body = ",\n    ".join(
        f"{{{n}, {_literal(d)}, {_literal(e)}, {_literal(label)}}}" for n, d, e, label in rows
    )
    return "static constexpr esphome::matter::PlanRow MATTER_PLAN[] = {\n    " + body + "};"


def plan_to_code() -> None:
    plan: ExposePlan | None = CORE.data.get(CONF_MATTER, {}).get(KEY_EXPOSE_PLAN)
    if plan is None or not plan.endpoints or not logs_config():
        return
    cg.add_define("USE_MATTER_PLAN")
    cg.add_global(RawStatement('#include "esphome/components/matter/matter_plan.h"'))
    cg.add_global(RawStatement(plan_table_code(plan_rows(plan))))
    cg.add(
        RawExpression(
            "esphome::matter::set_plan(MATTER_PLAN, sizeof(MATTER_PLAN) / sizeof(MATTER_PLAN[0]))"
        )
    )
