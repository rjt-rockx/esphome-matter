"""The expose plan: what was lowered, printed at `esphome config` time (INFO)."""

import logging
from dataclasses import dataclass, field

_LOGGER = logging.getLogger(__name__)


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
    number: int | None = None
    source: str = "hash"  # hash | pin | was


def format_plan(
    endpoints: list[PlannedEndpoint],
    skipped: list[tuple[str, str]],
    pinned_by_hand: list[int] | None = None,
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
    return "\n".join(lines)


def log_plan(
    endpoints: list[PlannedEndpoint],
    skipped: list[tuple[str, str]],
    pinned_by_hand: list[int] | None = None,
) -> None:
    _LOGGER.info("matter.expose plan:\n%s", format_plan(endpoints, skipped, pinned_by_hand))
