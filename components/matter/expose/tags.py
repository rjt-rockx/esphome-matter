"""Duplicate sets, automatic Common Number tags and labels for lowered endpoints (DIRECTION.md 9).

The Matter device library requires a Descriptor TagList on every endpoint that shares an
application device type with a sibling (same parent). The base engine numbers such endpoints in
endpoint-number order, which for hashed numbers is arbitrary. Lowering therefore writes the tags
itself, in the natural order of the endpoint keys (`relay_2` before `relay_10`), so that the
number follows the source and never the hash. Endpoints with explicit `tags:` keep them.

The entity name becomes the endpoint label (FixedLabel) only in a Duplicate set, where it tells
the siblings apart. Single endpoints get no label unless the item has `label:`.

Duplicate sets are computed on final endpoint numbers, so a `parent:` given as an id and one given
as a number, and the hand-written `endpoints:`, take part the same way.
"""

import logging
import re

import esphome.config_validation as cv

from ..conformance import (
    LABEL_MAX,
    NAMESPACE_COMMON_NUMBER,
    NUMBER_TAG_MAX,
    _duplicate_components,
    _tag_entry,
    default_label,
)
from .numbering import natural_key
from .plan import PlannedEndpoint

_LOGGER = logging.getLogger(__name__)

CONF_LABEL = "label"
CONF_TAGS = "tags"
CONF_PARENT = "parent"
_TRAILING_INT = re.compile(r"(\d+)$")


def duplicate_sets(
    planned: dict[str, PlannedEndpoint], numbers: dict[str, int], explicit: dict[int, dict]
) -> list[list[int]]:
    """Groups of endpoint numbers that share an application device type under the same parent.

    `explicit` are the hand-written endpoints; they count as siblings but are never changed.
    """
    shape: dict[int, dict] = {}
    for key, ep in planned.items():
        parent = ep.parent
        if isinstance(parent, str):
            parent = numbers[parent]
        shape[numbers[key]] = {CONF_PARENT: parent or 0, **dict.fromkeys(ep.device_types)}
    for number, config in explicit.items():
        shape[number] = {k: v for k, v in config.items() if k != CONF_LABEL}
    return _duplicate_components(shape)


def _number_tags(tags) -> set[int]:
    out = set()
    for tag in tags or ():
        tag = _tag_entry(tag)
        if tag["namespace"] == NAMESPACE_COMMON_NUMBER:
            out.add(tag["tag"])
    return out


def _trailing(key: str) -> int | None:
    match = _TRAILING_INT.search(key)
    return int(match.group(1)) if match else None


def _assign_numbers(keys: list[str], used: set[int]) -> tuple[dict[str, int], bool]:
    """Tag numbers for `keys` (natural order). Returns (numbers, any_by_position).

    An id ending in a number keeps that number (if it is free, in range and not taken by an
    earlier id), so editing other entities never moves it. The rest take the lowest free numbers.
    """
    taken = set(used)
    numbers: dict[str, int] = {}
    for key in keys:
        n = _trailing(key)
        if n is not None and n <= NUMBER_TAG_MAX and n not in taken:
            numbers[key] = n
            taken.add(n)
    by_position = False
    nxt = 1
    for key in keys:
        if key in numbers:
            continue
        while nxt in taken:
            nxt += 1
        numbers[key] = nxt
        taken.add(nxt)
        by_position = True
    return numbers, by_position


def _shorten(label: str, suffix: str) -> str:
    """`label` cut so that `label + ' ' + suffix` fits LABEL_MAX bytes, on a UTF-8 boundary."""
    room = LABEL_MAX - len(suffix.encode()) - 1
    cut = label.encode()[:room].decode("utf-8", errors="ignore").rstrip()
    return f"{cut} {suffix}".strip()


def _disambiguate(planned: dict[str, PlannedEndpoint], keys: list[str]) -> None:
    """Make derived labels of one Duplicate set distinct after truncation made some equal."""
    groups: dict[str, list[str]] = {}
    for key in keys:
        label = planned[key].extra.get(CONF_LABEL)
        if label:
            groups.setdefault(label, []).append(key)
    for label, same in groups.items():
        if len(same) < 2:
            continue
        for index, key in enumerate(same, 1):
            number = _trailing(key)
            suffix = str(number if number is not None else index)
            planned[key].extra[CONF_LABEL] = _shorten(label, suffix)
        _LOGGER.warning(
            "matter.expose: the labels of %s were identical after truncation to %d bytes; "
            "a number was appended. Set `label:` on each to choose them yourself.",
            ", ".join(same),
            LABEL_MAX,
        )


def assign_duplicate_tags(
    planned: dict[str, PlannedEndpoint],
    numbers: dict[str, int],
    explicit: dict[int, dict] | None = None,
) -> None:
    """Add Common Number tags and labels to the endpoints of every Duplicate set, in place."""
    explicit = explicit or {}
    by_number = {numbers[k]: k for k in planned}
    for component in duplicate_sets(planned, numbers, explicit):
        keys = sorted((by_number[n] for n in component if n in by_number), key=natural_key)
        if not keys:
            continue
        for key in keys:
            ep = planned[key]
            if CONF_LABEL not in ep.extra and ep.default_label:
                ep.extra[CONF_LABEL] = default_label(ep.default_label, f"entity '{key}'")
        _disambiguate(planned, [k for k in keys if planned[k].default_label])
        used: set[int] = set()
        for n in component:
            owner = planned[by_number[n]].extra if n in by_number else explicit[n]
            used |= _number_tags(owner.get(CONF_TAGS))
        untagged = [k for k in keys if CONF_TAGS not in planned[k].extra]
        if not untagged:
            continue
        assigned, by_position = _assign_numbers(untagged, used)
        if max(assigned.values()) > NUMBER_TAG_MAX:
            raise cv.Invalid(
                f"matter.expose: {len(component)} endpoints ({', '.join(keys)}) share a device "
                f"type and need more Common Number tags than the {NUMBER_TAG_MAX} that exist; "
                "give some of them `tags:`"
            )
        for key in untagged:
            ep = planned[key]
            tag = {"namespace": "common_number", "tag": assigned[key]}
            label = ep.extra.get(CONF_LABEL)
            if label and len(label.encode()) <= LABEL_MAX:
                tag[CONF_LABEL] = label
            ep.extra[CONF_TAGS] = [tag]
        if by_position:
            _LOGGER.info(
                "matter.expose: %s share a device type; ids that do not end in a number are "
                "tagged One, Two, ... in id order. Such a number follows the position, so "
                "adding an entity before them renumbers them. End the ids with a number "
                "(relay_1, relay_2) or give `tags:` to pin them",
                ", ".join(k for k in untagged if _trailing(k) is None) or ", ".join(untagged),
            )
    for ep in planned.values():
        ep.label = ep.extra.get(CONF_LABEL)


def warn_smoke_power(planned: dict[str, PlannedEndpoint]) -> None:
    """Warn when a Smoke CO Alarm is exposed and nothing in the plan is a Power Source.

    The esp-matter device type does not require a Power Source cluster, but detectors are almost
    always battery powered and controllers show the battery level of the node. Not verified
    against the Matter device library text.
    """
    if not any("smoke_co_alarm" in ep.device_types for ep in planned.values()):
        return
    if any("battery" in ep.extra or "power_source" in ep.device_types for ep in planned.values()):
        return
    _LOGGER.warning(
        "matter.expose: a Smoke CO Alarm is exposed without a Power Source. Expose its battery "
        "sensor (device_class: battery) next to it, with `attach_to:` naming the alarm endpoint, "
        "if the detector runs on a battery"
    )
