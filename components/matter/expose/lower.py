"""Lowering of `matter: expose:` into `endpoints:` (DIRECTION.md sections 2, 5 and 7).

`lower_expose` runs at the tail of CONFIG_SCHEMA and reads the entity configs from
`CORE.raw_config`. The lowered dicts go through the unchanged ENDPOINT_SCHEMA, so `cv.use_id`
produces real ID objects that the stock ID pass resolves and type checks, and to_code() runs
unchanged. Lowering in FINAL_VALIDATE would be too late for that.
"""

import logging
import re
from dataclasses import replace

import esphome.config_validation as cv
from esphome.core import CORE, ID

from ..conformance import application_device_type_ids
from ..const import CONF_ENDPOINTS, CONF_MATTER, CONF_VENDOR_ID
from .inference import CONF_LABEL, LoweredEndpoint, NoMapping, Overrides, infer
from .inventory import Entity, build_inventory
from .numbering import group_key, natural_key, resolve_numbers
from .plan import (
    KEY_EXPOSE_PLAN,
    TEST_VENDOR_IDS,
    ExposePlan,
    Member,
    PlannedEndpoint,
    log_plan,
)
from .schema import CONF_CLUSTERS, CONF_EXCLUDE, CONF_EXPOSE, CONF_PARENT, CONF_TAGS
from .selectors import ALL, EXPLICIT, Selection, parse_items, select
from .tags import assign_duplicate_tags, warn_smoke_power

_LOGGER = logging.getLogger(__name__)


def bare_matter_hint(inventory: list[Entity]) -> str:
    """The ready-to-paste hint for `matter:` without `expose:` or `endpoints:` (a root node)."""
    inferred = []
    for entity in inventory:
        if not entity.exposable or entity.skipped_by_glob:
            continue
        try:
            infer(entity, Overrides(via_glob=True))
        except NoMapping:
            continue
        inferred.append(entity.id)
    listing = ", ".join(inferred) if inferred else "<entity id>"
    return (
        "matter: no entities are exposed (root node only). To expose some, add one of:\n"
        f"  expose: [{listing}]      # inferred from your config; edit as needed\n"
        "  expose: all\n"
        "  expose: []                       # root node only"
    )


def bound_entity_ids(endpoints: dict) -> frozenset[str]:
    """Ids of the entities that hand-written or `bridge:` endpoints already bind."""
    found: set[str] = set()

    def walk(value):
        if isinstance(value, ID):
            found.add(value.id)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(endpoints)
    return frozenset(found)


def _has_endpoints(config: dict) -> bool:
    if config.get(CONF_ENDPOINTS):
        return True
    raw = (CORE.raw_config or {}).get(CONF_MATTER)
    return isinstance(raw, dict) and CONF_ENDPOINTS in raw


def _merge_dicts(into: dict, new: dict, what: str) -> None:
    for key, value in new.items():
        if key in into and into[key] != value:
            hint = ""
            if "group:electrical" in what:
                hint = "; with several loads give each meter `meters: <switch id>`"
            raise cv.Invalid(
                f"matter.expose: {what}: '{key}' is bound to two entities "
                f"({into[key]} and {value}); use `endpoints:` or separate endpoints{hint}"
            )
        into[key] = value


# Entities whose endpoint a power or energy sensor can belong to.
LOAD_DOMAINS = frozenset({"switch", "light", "cover", "fan"})
_TRAILING_INT = re.compile(r"(\d+)$")


def _apply_item_keys(ep: PlannedEndpoint, o: Overrides) -> None:
    """`endpoint:`, `label:`, `tags:`, `clusters:` and `parent:` of an item onto its endpoint."""
    key = ep.key
    if o.endpoint is not None:
        if ep.pin is not None and ep.pin != o.endpoint:
            raise cv.Invalid(
                f"matter.expose: conflicting endpoint pins for {key}: {ep.pin} and {o.endpoint}"
            )
        ep.pin = o.endpoint
    if o.label:
        if ep.extra.get(CONF_LABEL, o.label) != o.label:
            raise cv.Invalid(
                f"matter.expose: conflicting `label:` for {key}: "
                f"'{ep.extra[CONF_LABEL]}' and '{o.label}'"
            )
        ep.extra[CONF_LABEL] = o.label
    if o.tags is not None:
        if CONF_TAGS in ep.extra and ep.extra[CONF_TAGS] != o.tags:
            raise cv.Invalid(
                f"matter.expose: conflicting `tags:` for {key}: "
                f"{ep.extra[CONF_TAGS]} and {o.tags}"
            )
        ep.extra[CONF_TAGS] = list(o.tags)
    if o.clusters is not None:
        _merge_dicts(ep.extra.setdefault(CONF_CLUSTERS, {}), o.clusters, f"{key} clusters")
    if o.parent is not None:
        if ep.parent is not None and ep.parent != o.parent:
            raise cv.Invalid(
                f"matter.expose: conflicting `parent:` for {key}: {ep.parent} and {o.parent}"
            )
        ep.parent = o.parent


def _collides(ep: PlannedEndpoint | None, device_types: dict[str, dict]) -> bool:
    """Whether adding `device_types` to `ep` would bind an attribute to a second entity."""
    if ep is None:
        return False
    return any(
        existing.get(attr, value) != value
        for name, block in device_types.items()
        for attr, value in block.items()
        for existing in [ep.device_types.get(name, {})]
    )


def _add_standalone(planned, key_of, entity, o, lowered, explicit) -> None:
    if lowered.merge_group and _collides(
        planned.get(group_key(lowered.merge_group)), lowered.device_types
    ):
        # Two sensors for the same attribute (two CO2 sensors): the later one gets an endpoint
        # of its own instead of an error.
        _LOGGER.info(
            "matter.expose: '%s' binds an attribute that %s already has; it gets its own "
            "endpoint",
            entity.id,
            group_key(lowered.merge_group),
        )
        lowered = replace(lowered, merge_group=None)
    if lowered.merge_group:
        if o.was:
            raise cv.Invalid(
                f"matter.expose: `was:` on '{entity.id}' cannot be used for a merged "
                f"endpoint ({group_key(lowered.merge_group)})"
            )
        key = group_key(lowered.merge_group)
    else:
        key = o.was or entity.id
        if key in planned:
            raise cv.Invalid(
                f"matter.expose: endpoint key '{key}' is used twice (check `was:` against ids)"
            )
    ep = planned.setdefault(key, PlannedEndpoint(key))
    for name, block in lowered.device_types.items():
        _merge_dicts(ep.device_types.setdefault(name, {}), block, f"{key} {name}")
    _merge_dicts(ep.extra, lowered.extra, f"{key}")
    ep.entities.append(entity.id)
    ep.members.append(Member(entity, o, lowered, explicit))
    key_of[entity.id] = key
    _apply_item_keys(ep, o)
    if not lowered.merge_group and entity.name and not ep.default_label:
        ep.default_label = entity.name


def _attach(planned, key_of, entity, o, lowered, target: str) -> None:
    for what, value in (
        ("endpoint", o.endpoint),
        ("was", o.was),
        ("tags", o.tags),
        ("parent", o.parent),
        ("clusters", o.clusters),
        ("label", o.label),
    ):
        if value is not None:
            raise cv.Invalid(
                f"matter.expose: '{entity.id}' is attached to '{target}', so `{what}:` has no "
                f"meaning on it; put it on the item of '{target}'"
            )
    ep = planned[target]
    if lowered.attach_extra is not None:
        _merge_dicts(ep.extra, lowered.attach_extra, target)
    else:
        for name, block in lowered.device_types.items():
            _merge_dicts(ep.device_types.setdefault(name, {}), block, f"{target} {name}")
        _merge_dicts(ep.extra, lowered.extra, target)
    ep.attached.append(entity.id)
    ep.members.append(Member(entity, o, lowered, True))
    key_of[entity.id] = target


def _trailing(text: str) -> int | None:
    match = _TRAILING_INT.search(text or "")
    return int(match.group(1)) if match else None


def _word_prefix(text: str, prefix: str) -> bool:
    """`text` equals `prefix` or continues it after a non-alphanumeric character.

    `relay_1_power` and `Relay 1 Power` start with `relay_1` / `Relay 1`, `relay_12_power` does not.
    """
    text, prefix = text.lower(), prefix.lower()
    if not prefix or not text.startswith(prefix):
        return False
    return len(text) == len(prefix) or not text[len(prefix)].isalnum()


def _unique_best(scores: dict[str, int]) -> str | None:
    """The key with the single highest positive score, else None."""
    top = max(scores.values(), default=0)
    if top and list(scores.values()).count(top) == 1:
        return next(k for k, v in scores.items() if v == top)
    return None


def _auto_target(
    entity: Entity,
    kind: str,
    planned: dict[str, PlannedEndpoint],
    lowered: LoweredEndpoint,
    explicit: bool,
) -> str | None:
    """The endpoint a meter or battery belongs to, or None when that is not clear.

    A relation is needed: the id or the name starts with the candidate's (on a word boundary:
    `relay_1_power` and `Relay 1 Power` belong to `relay_1`, `relay_12_power` does not), or the id
    ends with the same number (`power_2` for `relay_2`). Ids are scored first, then names, then
    numbers; each must single out one candidate. Without a relation a meter that was listed by
    its own id attaches to the only load (an unrelated ADC voltage under `all` does not); a
    battery never does. A target that cannot take the entity (it already has a battery, or the
    attribute is bound to another sensor) is not a candidate.
    """
    if kind == "meter":
        cands = [
            ep for ep in planned.values() if any(m.entity.domain in LOAD_DOMAINS for m in ep.members)
        ]
    else:
        cands = [
            ep
            for ep in planned.values()
            if application_device_type_ids(ep.device_types) and "battery" not in ep.extra
        ]
    family = cands
    if kind == "meter":
        cands = [ep for ep in cands if not _collides(ep, lowered.device_types)]
    if not cands:
        return None

    def best(attr: str) -> str | None:
        if not getattr(entity, attr):
            return None
        scores = {}
        for ep in cands:
            hits = [
                len(getattr(m.entity, attr))
                for m in ep.members
                if getattr(m.entity, attr) and _word_prefix(getattr(entity, attr), getattr(m.entity, attr))
            ]
            scores[ep.key] = max(hits, default=0)
        return _unique_best(scores)

    for attr in ("id", "name"):
        if found := best(attr):
            return found
    # A trailing number only counts inside a numbered family: at least two candidates with
    # distinct numbers, one of which is the sensor's (relay_1/relay_2 with power_1/power_2).
    # Batteries never match on the number alone.
    number = _trailing(entity.id)
    if kind == "meter" and number is not None:
        numbers = [_trailing(ep.key) for ep in family]
        if (
            len(family) >= 2
            and None not in numbers
            and len(set(numbers)) == len(numbers)
            and numbers.count(number) == 1
        ):
            owner = next(ep.key for ep in family if _trailing(ep.key) == number)
            return owner if any(ep.key == owner for ep in cands) else None
    if kind == "meter" and explicit and len(cands) == 1:
        return cands[0].key
    return None


def _plan(selection: Selection) -> tuple[dict[str, PlannedEndpoint], dict[str, str]]:
    """Group the selected entities into planned endpoints. Returns (planned, entity -> key)."""
    planned: dict[str, PlannedEndpoint] = {}
    key_of: dict[str, str] = {}
    attaches, autos = [], []

    for sel in selection.selected:
        entity, o = sel.entity, sel.overrides
        explicit = sel.level == EXPLICIT
        try:
            lowered = infer(entity, replace(o, via_glob=not explicit))
        except NoMapping as exc:
            raise cv.Invalid(f"matter.expose: '{entity.id}' cannot be exposed: {exc}") from None
        if o.meters and lowered.auto_attach != "meter":
            raise cv.Invalid(
                f"matter.expose: `meters:` on '{entity.id}', which is not a power, energy, "
                "voltage, current, frequency or power factor sensor; use `attach_to:`"
            )
        if o.meters and o.attach_to and o.meters != o.attach_to:
            raise cv.Invalid(
                f"matter.expose: '{entity.id}' has `meters: {o.meters}` and "
                f"`attach_to: {o.attach_to}`; keep one"
            )
        target = o.attach_to or o.meters
        if target:
            attaches.append((entity, o, lowered, target))
        elif lowered.auto_attach:
            autos.append((entity, o, lowered, explicit))
        else:
            _add_standalone(planned, key_of, entity, o, lowered, explicit)

    def resolve(entity, name):
        target = key_of.get(name) or (name if name in planned else None)
        if target is None:
            raise cv.Invalid(
                f"matter.expose: '{entity.id}' has attach_to: '{name}', which is not an exposed "
                "entity or endpoint key"
            )
        return target

    for entity, o, lowered, name in attaches:
        _attach(planned, key_of, entity, o, lowered, resolve(entity, name))
    # Meters before batteries: a standalone electrical endpoint is no host for a Power Source.
    for kind in ("meter", "battery"):
        for entity, o, lowered, explicit in (a for a in autos if a[2].auto_attach == kind):
            target = _auto_target(entity, kind, planned, lowered, explicit)
            if target is not None:
                _attach(planned, key_of, entity, o, lowered, target)
            else:
                _add_standalone(planned, key_of, entity, o, lowered, explicit)

    for ep in planned.values():
        if isinstance(ep.parent, str):
            ep.parent = key_of.get(ep.parent) or (ep.parent if ep.parent in planned else None)
            if ep.parent is None:
                raise cv.Invalid(
                    f"matter.expose: `parent:` of '{ep.key}' is not an exposed entity or "
                    "endpoint key"
                )
            if ep.parent == ep.key:
                raise cv.Invalid(f"matter.expose: '{ep.key}' cannot be its own parent")
    return planned, key_of


def unnamed_warning(selection: Selection) -> str | None:
    if not selection.unnamed:
        return None
    ids = ", ".join(selection.unnamed)
    return (
        f"matter.expose: {len(selection.unnamed)} entities matched by a glob or `all` were "
        f"skipped because they have no `name:` (ESPHome makes a nameless entity internal): "
        f"{ids}. To expose one, give it a `name:`; to expose it as it is, list its id "
        "explicitly in `expose:`; to silence this, add the id to `exclude:`."
    )


def lower_expose(config: dict) -> dict:
    inventory = build_inventory(CORE.raw_config)
    CORE.data.get(CONF_MATTER, {}).pop(KEY_EXPOSE_PLAN, None)  # no stale plan across validations
    if CONF_EXPOSE not in config:
        # A bare `matter:` (or one with only `endpoints:`) stays valid: a root node, or the
        # hand-written endpoints. The hint shows what `expose:` would add.
        if not _has_endpoints(config):
            _LOGGER.info(bare_matter_hint(inventory))
        return config

    items = parse_items(config[CONF_EXPOSE])
    selection = select(
        inventory,
        items,
        config.get(CONF_EXCLUDE, []),
        bound_entity_ids(config.get(CONF_ENDPOINTS) or {}),
    )
    if warning := unnamed_warning(selection):
        _LOGGER.warning(warning)
    planned, _ = _plan(selection)

    from ..endpoints import ENDPOINT_SCHEMA  # avoid an import cycle at module import

    explicit = dict(config.get(CONF_ENDPOINTS) or {})
    numbers = resolve_numbers(
        sorted(planned, key=natural_key),
        {k: ep.pin for k, ep in planned.items() if ep.pin is not None},
        set(explicit),
    )
    # Tags and labels depend on who shares a parent, so they need the final numbers.
    assign_duplicate_tags(planned, numbers, explicit)
    warn_smoke_power(planned)
    endpoints = dict(explicit)
    for key, ep in planned.items():
        ep.number = numbers[key]
        ep.source = "pin" if ep.pin is not None else "hash"
        value = {**ep.device_types, **ep.extra}
        if ep.parent is not None:
            value[CONF_PARENT] = numbers[ep.parent] if isinstance(ep.parent, str) else ep.parent
        try:
            endpoints[ep.number] = ENDPOINT_SCHEMA(value)
        except cv.Invalid as exc:
            where = ".".join(str(p) for p in exc.path)
            what = exc.msg.replace("extra keys not allowed", "is not an option of this device type")
            raise cv.Invalid(
                f"matter.expose: '{key}' ({', '.join([*ep.entities, *ep.attached])}) lowered to "
                f"endpoint {ep.number}, which failed validation"
                f"{f' at {where}' if where else ''}: {what}",
                path=[CONF_EXPOSE],
            ) from None

    plan = ExposePlan(
        list(planned.values()),
        selection.skipped,
        sorted(explicit),
        config.get(CONF_VENDOR_ID, 0xFFF1) in TEST_VENDOR_IDS,
    )
    CORE.data.setdefault(CONF_MATTER, {})[KEY_EXPOSE_PLAN] = plan
    log_plan(plan)
    config = dict(config)
    config[CONF_ENDPOINTS] = dict(sorted(endpoints.items()))
    return config


bare_matter_error = bare_matter_hint  # old name

__all__ = ["ALL", "bare_matter_hint", "bare_matter_error", "lower_expose"]
