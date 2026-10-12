"""Duplicate sets, automatic Common Number tags and labels for lowered endpoints (DIRECTION.md 9).

The Matter device library requires a Descriptor TagList on every endpoint that shares an
application device type with a sibling (same parent). The base engine numbers such endpoints in
endpoint-number order, which for hashed numbers is arbitrary. Lowering therefore writes the tags
itself, in the natural order of the endpoint keys (`relay_2` before `relay_10`), so that the
number follows the source and never the hash. Endpoints with explicit `tags:` keep them.

The entity name becomes the endpoint label (FixedLabel) only in a Duplicate set, where it tells
the siblings apart. Single endpoints get no label unless the item has `label:`.
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
)
from .numbering import natural_key
from .plan import PlannedEndpoint

_LOGGER = logging.getLogger(__name__)

CONF_LABEL = "label"
CONF_TAGS = "tags"
_TRAILING_INT = re.compile(r"(\d+)$")


def duplicate_sets(planned: dict[str, PlannedEndpoint]) -> list[list[str]]:
    """Groups of planned keys that share an application device type under the same parent."""
    shape = {
        key: {"parent": ep.parent if ep.parent is not None else 0, **dict.fromkeys(ep.device_types)}
        for key, ep in planned.items()
    }
    return [sorted(c, key=natural_key) for c in _duplicate_components(shape)]


def _used_numbers(planned: dict[str, PlannedEndpoint], keys: list[str]) -> set[int]:
    used = set()
    for key in keys:
        for tag in planned[key].extra.get(CONF_TAGS, ()):
            tag = _tag_entry(tag)
            if tag["namespace"] == NAMESPACE_COMMON_NUMBER:
                used.add(tag["tag"])
    return used


def _numbers_for(keys: list[str], used: set[int]) -> tuple[dict[str, int], bool]:
    """Tag numbers for `keys` (natural order). Returns (numbers, by_trailing_integer)."""
    trailing = {}
    for key in keys:
        match = _TRAILING_INT.search(key)
        if match:
            trailing[key] = int(match.group(1))
    values = list(trailing.values())
    if (
        len(trailing) == len(keys)
        and len(set(values)) == len(values)
        and all(v <= NUMBER_TAG_MAX for v in values)
        and not used & set(values)
    ):
        return trailing, True
    numbers, n = {}, 1
    for key in keys:
        while n in used:
            n += 1
        numbers[key] = n
        n += 1
    return numbers, False


def assign_duplicate_tags(planned: dict[str, PlannedEndpoint]) -> None:
    """Add Common Number tags and labels to the endpoints of every Duplicate set, in place."""
    for keys in duplicate_sets(planned):
        for key in keys:
            ep = planned[key]
            if CONF_LABEL not in ep.extra and ep.default_label:
                ep.extra[CONF_LABEL] = ep.default_label
        untagged = [k for k in keys if CONF_TAGS not in planned[k].extra]
        if not untagged:
            continue
        numbers, by_trailing = _numbers_for(untagged, _used_numbers(planned, keys))
        if max(numbers.values()) > NUMBER_TAG_MAX:
            raise cv.Invalid(
                f"matter.expose: {len(keys)} endpoints ({', '.join(keys)}) share a device type "
                f"and need more Common Number tags than the {NUMBER_TAG_MAX} that exist; give "
                "some of them `tags:`"
            )
        for key in untagged:
            ep = planned[key]
            tag = {"namespace": "common_number", "tag": numbers[key]}
            label = ep.extra.get(CONF_LABEL)
            if label and len(label.encode()) <= LABEL_MAX:
                tag[CONF_LABEL] = label
            ep.extra[CONF_TAGS] = [tag]
        if not by_trailing:
            _LOGGER.info(
                "matter.expose: %s share a device type; tagged One, Two, ... in id order. A "
                "number follows its position, so adding an entity before the others renumbers "
                "them. End the ids with a number (relay_1, relay_2) or give `tags:` to pin them",
                ", ".join(untagged),
            )
    for ep in planned.values():
        ep.label = ep.extra.get(CONF_LABEL)
