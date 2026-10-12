"""The expose plan: what was lowered, printed at `esphome config` time (INFO)."""

import logging
from dataclasses import dataclass, field

from .inference import LoweredEndpoint, Overrides
from .inventory import Entity

_LOGGER = logging.getLogger(__name__)


# CORE.data[CONF_MATTER] key holding the ExposePlan for final_check.py and the plan table codegen.
KEY_EXPOSE_PLAN = "expose_plan"

# Warn above this many endpoints: each one costs heap.
ENDPOINT_WARN_LIMIT = 16
# Rough heap per exposed endpoint (endpoint, clusters, attributes), see KNOWLEDGE.md. Estimate only.
HEAP_PER_ENDPOINT_BYTES = 450
# Raising CONFIG_ESP_MATTER_MAX_DYNAMIC_ENDPOINT_COUNT sizes CHIP's per-endpoint tables.
HEAP_RAISED_LIMIT_KB = 6
TEST_VENDOR_IDS = range(0xFFF1, 0xFFF5)

# Device types some ecosystems reject: conf key -> reason. Printed in the plan.
ECOSYSTEM_WARNINGS = {
    "water_valve": "Apple Home does not show water valves",
    "soil_sensor": "Apple Home rejects soil sensors",
}


@dataclass
class Member:
    """One selected entity of a planned endpoint, kept for the FINAL_VALIDATE re-check."""

    entity: Entity
    overrides: Overrides
    lowered: LoweredEndpoint
    # Selected by an explicit id (True) or by a glob / `all` (False).
    explicit: bool = True


@dataclass
class PlannedEndpoint:
    key: str
    device_types: dict[str, dict] = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    entities: list[str] = field(default_factory=list)
    label: str | None = None
    pin: int | None = None
    # Entities whose clusters were merged in with `attach_to:`.
    attached: list[str] = field(default_factory=list)
    members: list[Member] = field(default_factory=list)
    number: int | None = None
    source: str = "hash"  # hash | pin | was


@dataclass
class ExposePlan:
    """The whole plan, kept in CORE.data for final_check.py and the codegen of the table."""

    endpoints: list[PlannedEndpoint]
    skipped: list[tuple[str, str]]
    pinned_by_hand: list[int] = field(default_factory=list)
    test_vendor: bool = True

    @property
    def endpoint_count(self) -> int:
        return len(self.endpoints) + len(self.pinned_by_hand)


def ecosystem_warnings(plan: ExposePlan) -> list[str]:
    found = []
    for ep in plan.endpoints:
        for name in ep.device_types:
            if name in ECOSYSTEM_WARNINGS:
                found.append(f"{ep.key}: {ECOSYSTEM_WARNINGS[name]}")
    if plan.endpoints and plan.test_vendor:
        found.append(
            "Google Home needs a Google Developer Console project for test vendor id 0xFFF1 "
            "(set `vendor_id:` once you have your own)"
        )
    return found


def heap_estimate(count: int) -> str:
    kb = count * HEAP_PER_ENDPOINT_BYTES / 1024
    return f"about {kb:.1f} KB for {count} endpoints (rough estimate, unmeasured)"


def endpoint_count_warning(count: int) -> str | None:
    if count <= ENDPOINT_WARN_LIMIT:
        return None
    return (
        f"matter.expose: {count} endpoints is above {ENDPOINT_WARN_LIMIT}. Every endpoint costs "
        f"heap ({heap_estimate(count)}), and CONFIG_ESP_MATTER_MAX_DYNAMIC_ENDPOINT_COUNT may "
        f"need raising (about {HEAP_RAISED_LIMIT_KB} KB more heap). "
        "Trim with `exclude:` or expose fewer entities."
    )


def format_plan(
    endpoints: list[PlannedEndpoint],
    skipped: list[tuple[str, str]],
    pinned_by_hand: list[int] | None = None,
    plan: ExposePlan | None = None,
) -> str:
    rows = [("endpoint", "key", "device types", "entities", "label", "number from")]
    for ep in sorted(endpoints, key=lambda e: e.number):
        rows.append(
            (
                str(ep.number),
                ep.key,
                ", ".join(ep.device_types),
                ", ".join([*ep.entities, *(f"+{a}" for a in ep.attached)]),
                ep.label or "",
                ep.source,
            )
        )
    for number in sorted(pinned_by_hand or ()):
        rows.append((str(number), "", "(endpoints:)", "", "", "pin"))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip() for r in rows]
    lines.extend(f"skipped {name}: {reason}" for name, reason in skipped)
    if plan is not None:
        lines.extend(f"ecosystem: {text}" for text in ecosystem_warnings(plan))
        lines.append(f"heap: {heap_estimate(plan.endpoint_count)}")
    return "\n".join(lines)


def log_plan(plan: ExposePlan) -> None:
    _LOGGER.info(
        "matter.expose plan:\n%s",
        format_plan(plan.endpoints, plan.skipped, plan.pinned_by_hand, plan),
    )
    if warning := endpoint_count_warning(plan.endpoint_count):
        _LOGGER.warning(warning)
