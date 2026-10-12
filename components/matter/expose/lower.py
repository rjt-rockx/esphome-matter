"""Lowering of `matter: expose:` into `endpoints:` (DIRECTION.md sections 2, 5 and 7).

`lower_expose` runs at the tail of CONFIG_SCHEMA and reads the entity configs from
`CORE.raw_config`. The lowered dicts go through the unchanged ENDPOINT_SCHEMA, so `cv.use_id`
produces real ID objects that the stock ID pass resolves and type checks, and to_code() runs
unchanged. Lowering in FINAL_VALIDATE would be too late for that.
"""

import logging

import esphome.config_validation as cv
from esphome.core import CORE

from ..const import CONF_ENDPOINTS, CONF_MATTER
from .inference import CONF_LABEL, NoMapping, Overrides, infer
from .inventory import Entity, build_inventory
from .numbering import group_key, natural_key, resolve_numbers
from .plan import PlannedEndpoint, log_plan
from .schema import CONF_EXCLUDE, CONF_EXPOSE
from .selectors import ALL, Selection, parse_items, select

_LOGGER = logging.getLogger(__name__)


def bare_matter_error(inventory: list[Entity]) -> str:
    """The ready-to-paste message for `matter:` without `expose:` or `endpoints:`."""
    inferred = []
    for entity in inventory:
        if not entity.exposable or entity.skipped_by_glob:
            continue
        try:
            infer(entity, Overrides())
        except NoMapping:
            continue
        inferred.append(entity.id)
    listing = ", ".join(inferred) if inferred else "<entity id>"
    return (
        "matter: nothing to expose. Add one of:\n"
        f"  expose: [{listing}]      # inferred from your config; edit as needed\n"
        "  expose: all\n"
        "  expose: []                       # root node only"
    )


def _has_endpoints(config: dict) -> bool:
    if config.get(CONF_ENDPOINTS):
        return True
    raw = (CORE.raw_config or {}).get(CONF_MATTER)
    return isinstance(raw, dict) and CONF_ENDPOINTS in raw


def _merge_dicts(into: dict, new: dict, what: str) -> None:
    for key, value in new.items():
        if key in into and into[key] != value:
            raise cv.Invalid(
                f"matter.expose: {what}: '{key}' is bound to two entities "
                f"({into[key]} and {value}); use `endpoints:` or separate endpoints"
            )
        into[key] = value


def _supports_label() -> bool:
    """Whether the base ENDPOINT_SCHEMA has an endpoint `label:` key (M0 adds it)."""
    from ..endpoints import ENDPOINT_SCHEMA

    validators = getattr(ENDPOINT_SCHEMA, "validators", (ENDPOINT_SCHEMA,))
    return any(
        str(k) == CONF_LABEL
        for v in validators
        for k in getattr(getattr(v, "schema", None), "keys", lambda: ())()
    )


def _plan(selection: Selection) -> tuple[dict[str, PlannedEndpoint], dict[str, str]]:
    """Group the selected entities into planned endpoints. Returns (planned, entity -> key)."""
    planned: dict[str, PlannedEndpoint] = {}
    key_of: dict[str, str] = {}
    attaches = []
    supports_label = _supports_label()

    for sel in selection.selected:
        entity, o = sel.entity, sel.overrides
        try:
            lowered = infer(entity, o)
        except NoMapping as exc:
            raise cv.Invalid(
                f"matter.expose: '{entity.id}' cannot be exposed: {exc}"
            ) from None
        if o.attach_to:
            attaches.append((entity, o, lowered))
            continue
        if lowered.merge_group:
            if o.was:
                raise cv.Invalid(
                    f"matter.expose: `was:` on '{entity.id}' cannot be used for a merged "
                    f"endpoint ({group_key(lowered.merge_group)})"
                )
            key = group_key(lowered.merge_group)
        else:
            key = o.was or entity.id
        if key in planned and not lowered.merge_group:
            raise cv.Invalid(
                f"matter.expose: endpoint key '{key}' is used twice (check `was:` against ids)"
            )
        ep = planned.setdefault(key, PlannedEndpoint(key))
        for name, block in lowered.device_types.items():
            _merge_dicts(ep.device_types.setdefault(name, {}), block, f"{key} {name}")
        _merge_dicts(ep.extra, lowered.extra, f"{key}")
        ep.entities.append(entity.id)
        key_of[entity.id] = key
        if o.endpoint is not None:
            if ep.pin is not None and ep.pin != o.endpoint:
                raise cv.Invalid(
                    f"matter.expose: conflicting endpoint pins for {key}: "
                    f"{ep.pin} and {o.endpoint}"
                )
            ep.pin = o.endpoint
        label = o.label or (entity.name if not lowered.merge_group else None)
        if label and not ep.label:
            ep.label = label
            if supports_label:
                ep.extra[CONF_LABEL] = label[:64]
            else:
                _LOGGER.debug("matter.expose: endpoint label '%s' dropped (not supported)", label)

    for entity, o, lowered in attaches:
        target = key_of.get(o.attach_to) or (o.attach_to if o.attach_to in planned else None)
        if target is None:
            raise cv.Invalid(
                f"matter.expose: '{entity.id}' has attach_to: '{o.attach_to}', which is not "
                "an exposed entity or endpoint key"
            )
        ep = planned[target]
        for name, block in lowered.device_types.items():
            _merge_dicts(ep.device_types.setdefault(name, {}), block, f"{target} {name}")
        _merge_dicts(ep.extra, lowered.extra, target)
        ep.attached.append(entity.id)
        key_of[entity.id] = target
    return planned, key_of


def lower_expose(config: dict) -> dict:
    inventory = build_inventory(CORE.raw_config)
    if CONF_EXPOSE not in config:
        if not _has_endpoints(config):
            raise cv.Invalid(bare_matter_error(inventory))
        return config

    items = parse_items(config[CONF_EXPOSE])
    selection = select(inventory, items, config.get(CONF_EXCLUDE, []))
    planned, _ = _plan(selection)

    from ..endpoints import ENDPOINT_SCHEMA  # avoid an import cycle at module import

    explicit = dict(config.get(CONF_ENDPOINTS) or {})
    numbers = resolve_numbers(
        sorted(planned, key=natural_key),
        {k: ep.pin for k, ep in planned.items() if ep.pin is not None},
        set(explicit),
    )
    endpoints = dict(explicit)
    for key, ep in planned.items():
        ep.number = numbers[key]
        ep.source = "pin" if ep.pin is not None else "hash"
        value = {**ep.device_types, **ep.extra}
        try:
            endpoints[ep.number] = ENDPOINT_SCHEMA(value)
        except cv.Invalid as exc:
            raise cv.Invalid(
                f"matter.expose: endpoint {ep.number} ({key}): {exc.msg}",
                path=[CONF_EXPOSE, *exc.path],
            ) from None

    log_plan(list(planned.values()), selection.skipped, sorted(explicit))
    config = dict(config)
    config[CONF_ENDPOINTS] = dict(sorted(endpoints.items()))
    return config


__all__ = ["ALL", "bare_matter_error", "lower_expose"]
