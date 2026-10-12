"""Selecting entities for `matter: expose:` (DIRECTION.md section 5).

Precedence: explicit id > glob > `all`. `exclude:` beats globs and `all`; excluding an explicitly
listed id is an error. Globs and `all` skip internal and config/diagnostic entities and entities
without a mapping; an explicit id may expose them (with a warning) or errors with the reason.
"""

import difflib
import fnmatch
import logging
from dataclasses import dataclass, field, replace

import esphome.config_validation as cv

from .inference import NoMapping, Overrides, infer
from .inventory import Entity, by_id
from .numbering import natural_key

_LOGGER = logging.getLogger(__name__)

ALL = "all"
EXPLICIT, GLOB, ALL_LEVEL = 0, 1, 2  # precedence, lowest number wins
LEVEL_NAMES = {EXPLICIT: "explicit id", GLOB: "glob", ALL_LEVEL: "all"}
_GLOB_CHARS = frozenset("*?[")


@dataclass
class Item:
    pattern: str
    overrides: Overrides = field(default_factory=Overrides)

    @property
    def level(self) -> int:
        if self.pattern == ALL:
            return ALL_LEVEL
        return GLOB if is_glob(self.pattern) else EXPLICIT


@dataclass
class Selected:
    entity: Entity
    overrides: Overrides
    level: int


@dataclass
class Selection:
    selected: list[Selected] = field(default_factory=list)
    # (entity label, reason) for everything a glob or `all` passed over.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    # Ids of entities a glob or `all` matched but passed over because they have no `name:`.
    unnamed: list[str] = field(default_factory=list)


def is_glob(pattern: str) -> bool:
    return any(c in _GLOB_CHARS for c in pattern)


def parse_items(expose) -> list[Item]:
    """Normalise the validated `expose:` list (strings and maps) into Items."""
    items = []
    for entry in expose or []:
        if isinstance(entry, str):
            items.append(Item(entry))
            continue
        items.append(
            Item(
                entry["id"],
                Overrides(
                    as_=entry.get("as"),
                    features=entry.get("features"),
                    label=entry.get("label"),
                    options=entry.get("options"),
                    attach_to=entry.get("attach_to"),
                    meters=entry.get("meters"),
                    endpoint=entry.get("endpoint"),
                    was=entry.get("was"),
                    tags=entry.get("tags"),
                    parent=entry.get("parent"),
                    clusters=entry.get("clusters"),
                ),
            )
        )
    return items


def suggest(unknown: str, ids) -> str:
    close = difflib.get_close_matches(unknown, list(ids), n=3, cutoff=0.5)
    if not close:
        return ""
    return f" Did you mean {', '.join(repr(c) for c in close)}?"


def expand_glob(pattern: str, ids) -> list[str]:
    """Ids matching an fnmatch glob (case sensitive), in natural-sort order."""
    return sorted((i for i in ids if fnmatch.fnmatchcase(i, pattern)), key=natural_key)


def _merge(level: int, eid: str, candidates: list[Overrides]) -> Overrides:
    """Overrides for one id at one level. Equal or empty duplicates merge; others conflict."""
    chosen = Overrides()
    for cand in candidates:
        if cand.empty or cand.key() == chosen.key():
            continue
        if chosen.empty:
            chosen = cand
            continue
        raise cv.Invalid(
            f"matter.expose: '{eid}' is matched twice by {LEVEL_NAMES[level]} entries with "
            f"conflicting overrides ({_describe(chosen)} vs {_describe(cand)})"
        )
    return chosen


def _describe(o: Overrides) -> str:
    base = Overrides().key()
    names = (
        "as", "features", "label", "options", "attach_to", "meters", "endpoint", "was",
        "tags", "parent", "clusters",
    )  # fmt: skip
    parts = [n for n, v, b in zip(names, o.key(), base, strict=True) if v != b]
    return "{" + ", ".join(parts) + "}"


def select(inventory: list[Entity], items: list[Item], excludes: list[str]) -> Selection:
    entities = by_id(inventory)
    ids = list(entities)
    # id -> level -> [Overrides]
    matches: dict[str, dict[int, list[Overrides]]] = {}
    order: list[str] = []
    skipped: list[tuple[str, str]] = []

    def add(eid: str, level: int, overrides: Overrides) -> None:
        if eid not in matches:
            matches[eid] = {}
            order.append(eid)
        matches[eid].setdefault(level, []).append(overrides)

    for item in items:
        if item.level == EXPLICIT:
            if item.pattern not in entities:
                raise cv.Invalid(
                    f"matter.expose: no entity with id '{item.pattern}'."
                    f"{suggest(item.pattern, ids)}"
                )
            add(item.pattern, EXPLICIT, item.overrides)
        elif item.level == GLOB:
            found = expand_glob(item.pattern, ids)
            if not found:
                raise cv.Invalid(
                    f"matter.expose: pattern '{item.pattern}' matches no entity with an `id:`"
                )
            for eid in found:
                add(eid, GLOB, item.overrides)
        else:
            for eid in ids:
                add(eid, ALL_LEVEL, item.overrides)
            skipped.extend(
                (e.label, "no explicit `id:`")
                for e in inventory
                if not e.exposable and not e.skipped_by_glob
            )

    # exclude: ids or globs; an explicitly listed id may not be excluded.
    excluded: set[str] = set()
    for pattern in excludes:
        if is_glob(pattern):
            found = expand_glob(pattern, ids)
        elif pattern in entities:
            found = [pattern]
        else:
            raise cv.Invalid(
                f"matter.exclude: no entity with id '{pattern}'.{suggest(pattern, ids)}"
            )
        for eid in found:
            if EXPLICIT in matches.get(eid, {}):
                raise cv.Invalid(
                    f"matter.exclude: '{eid}' (from '{pattern}') is also listed explicitly in "
                    "`expose:`; remove it from one of them"
                )
        excluded.update(found)

    result = Selection(skipped=skipped)
    for eid in order:
        levels = matches[eid]
        level = min(levels)
        entity = entities[eid]
        overrides = _merge(level, eid, levels[level])
        if level != EXPLICIT:
            if eid in excluded:
                skipped.append((eid, "excluded"))
                continue
            if reason := entity.skipped_by_glob:
                skipped.append((eid, reason))
                if reason.startswith("no name"):
                    result.unnamed.append(eid)
                continue
            try:
                # Probe only; lowering re-runs it with the real selection.
                infer(entity, replace(overrides, via_glob=True))
            except NoMapping as exc:
                skipped.append((eid, str(exc)))
                continue
        else:
            if reason := entity.skipped_by_glob:
                _LOGGER.warning(
                    "matter.expose: '%s' is exposed explicitly although it is %s", eid, reason
                )
        result.selected.append(Selected(entity, overrides, level))
    return result
