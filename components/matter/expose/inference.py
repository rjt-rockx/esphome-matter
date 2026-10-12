"""Device type inference for `matter: expose:` (DIRECTION.md section 6).

Inference is a registry: `domain -> [rule]`. A rule is `callable(entity, overrides) ->
LoweredEndpoint | None` and may raise `NoMapping(reason)`; `None` declines so the next rule for the
domain is tried. Register more rules with `@rule("binary_sensor")` (or `register_rule`) from any
module that is imported before `lower_expose` runs; nothing else has to change.

Rules must only use what the raw config offers (see inventory.py). Anything that depends on
validated values is re-checked in final_check.py.
"""

import difflib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import esphome.config_validation as cv

from ..const import CONF_WITH_FEATURES
from ..data_model.device_types import DEVICE_TYPES_BY_CONF_KEY
from .inventory import Entity

_LOGGER = logging.getLogger(__name__)

CONF_LABEL = "label"
FEATURES_KEY = CONF_WITH_FEATURES  # key of the extra features of a device type block


@dataclass
class Overrides:
    """The per-item keys of an expose map (everything but `id`, `endpoint`, `was`)."""

    as_: str | None = None
    features: list[str] | None = None
    label: str | None = None
    options: dict | None = None
    attach_to: str | None = None
    meters: str | None = None
    endpoint: int | None = None
    was: str | None = None

    def key(self) -> tuple:
        """Comparable form used to detect conflicting overrides on the same id."""
        return (
            self.as_,
            tuple(self.features) if self.features is not None else None,
            self.label,
            tuple(sorted(self.options.items())) if self.options is not None else None,
            self.attach_to,
            self.meters,
            self.endpoint,
            self.was,
        )

    @property
    def empty(self) -> bool:
        return self.key() == Overrides().key()


@dataclass
class LoweredEndpoint:
    """One planned endpoint contribution of one entity.

    device_types: conf_key -> block dict (always the dict form, never the `device_type: id`
      shorthand, so blocks can be merged and extended).
    extra: endpoint-level keys (`battery`, `extra_clusters`, ...).
    merge_group: entities returning the same group share one endpoint keyed `group:<name>`.
    """

    device_types: dict[str, dict] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    merge_group: str | None = None

    @property
    def primary(self) -> str:
        return next(iter(self.device_types))


class NoMapping(Exception):
    """Raised by a rule (or the registry) when an entity cannot be mapped; carries the reason."""


Rule = Callable[[Entity, Overrides], "LoweredEndpoint | None"]
_RULES: dict[str, list[Rule]] = {}


def register_rule(domain: str, fn: Rule) -> Rule:
    _RULES.setdefault(domain, []).append(fn)
    return fn


def rule(domain: str) -> Callable[[Rule], Rule]:
    def decorator(fn: Rule) -> Rule:
        return register_rule(domain, fn)

    return decorator


# Domains without a faithful Matter device type (section 6). Reason shown in errors and the plan.
UNMAPPABLE = {
    domain: f"a {domain.replace('_', ' ')} has no faithful Matter device type"
    for domain in (
        "button",
        "number",
        "select",
        "text",
        "text_sensor",
        "datetime",
        "alarm_control_panel",
        "media_player",
        "update",
    )
}

# Entity id key of the device types that bind one ESPHome entity of a domain.
DOMAIN_ID_KEYS = {
    "light": "light_id",
    "switch": "switch_id",
    "cover": "cover_id",
    "climate": "climate_id",
    "fan": "fan_id",
    "lock": "lock_id",
    "valve": "valve_id",
}
# Device types whose binding key differs from the domain's.
TYPE_ID_KEYS = {
    ("generic_switch", "binary_sensor"): "binary_sensor_id",
    ("generic_switch", "event"): "event",
}


def device_type_key(name: str) -> str:
    """Validate an `as:` value (any device-type key) and return it."""
    if name in DEVICE_TYPES_BY_CONF_KEY:
        return name
    close = difflib.get_close_matches(name, DEVICE_TYPES_BY_CONF_KEY, n=3)
    hint = f" Did you mean {', '.join(repr(c) for c in close)}?" if close else ""
    raise cv.Invalid(f"matter.expose: unknown device type '{name}' in `as:`.{hint}")


def bind(device_type: str, entity: Entity) -> dict:
    """Binding block of `device_type` for `entity`.

    A device type with a single sensor attribute binds through that attribute; others through the
    entity-id key of the domain. A wrong pairing is left for the stock `cv.use_id` check, which
    names the offending id and type at the lowered path.
    """
    dt = DEVICE_TYPES_BY_CONF_KEY[device_type]
    if (device_type, entity.domain) in TYPE_ID_KEYS:
        return {TYPE_ID_KEYS[(device_type, entity.domain)]: entity.id}
    if len(dt.sensor_attributes) == 1:
        return {dt.sensor_attributes[0].conf_key: entity.id}
    if entity.domain in DOMAIN_ID_KEYS:
        return {DOMAIN_ID_KEYS[entity.domain]: entity.id}
    raise NoMapping(
        f"`as: {device_type}` cannot bind a {entity.domain} (it has "
        f"{len(dt.sensor_attributes)} sensor attributes); use `endpoints:` for this one"
    )


def infer(entity: Entity, overrides: Overrides) -> LoweredEndpoint:
    """Lower one entity. Raises NoMapping with the reason when nothing applies."""
    if overrides.as_:
        name = device_type_key(overrides.as_)
        lowered = LoweredEndpoint({name: bind(name, entity)})
    else:
        lowered = None
        for fn in _RULES.get(entity.domain, ()):
            lowered = fn(entity, overrides)
            if lowered is not None:
                break
        if lowered is None:
            if entity.domain in UNMAPPABLE:
                raise NoMapping(UNMAPPABLE[entity.domain])
            raise NoMapping(
                f"no device type is inferred for this {entity.domain}"
                f"{f' ({entity.hint})' if entity.hint else ''}; use `as:`"
            )
    return apply_overrides(lowered, entity, overrides)


def apply_overrides(lowered: LoweredEndpoint, entity: Entity, o: Overrides) -> LoweredEndpoint:
    """`features:`, `options:` on the primary device type block, `label:` on the endpoint."""
    block = lowered.device_types[lowered.primary]
    if o.features:
        # Names are validated by the device type schema when the block is lowered.
        block[FEATURES_KEY] = list(dict.fromkeys([*block.get(FEATURES_KEY, ()), *o.features]))
    if o.options:
        clash = sorted(set(o.options) & set(block))
        if clash:
            raise cv.Invalid(
                f"matter.expose: options {clash} of '{entity.id}' collide with the binding "
                f"of {lowered.primary}"
            )
        block.update(o.options)
    return lowered


# --- the spike's rules (T09 registers the rest of the section 6 table) -------------------------

_LIGHT_PLATFORM_TO_TYPE = {
    "binary": "on_off_light",
    "monochromatic": "dimmable_light",
    "cwww": "color_temperature_light",
    "color_temperature": "color_temperature_light",
    "rgb": "extended_color_light",
    "rgbw": "extended_color_light",
    "rgbww": "extended_color_light",
    "rgbct": "extended_color_light",
}
_TEMPERATURE_PLATFORMS = {"internal_temperature", "dallas_temp", "ntc"}
_TEMPERATURE_UNITS = {"°C", "°F", "K"}


@rule("switch")
def _switch(entity: Entity, overrides: Overrides) -> LoweredEndpoint:
    return LoweredEndpoint({"on_off_plug_in_unit": bind("on_off_plug_in_unit", entity)})


@rule("light")
def _light(entity: Entity, overrides: Overrides) -> LoweredEndpoint:
    device_type = _LIGHT_PLATFORM_TO_TYPE.get(entity.platform)
    if device_type is None:
        raise NoMapping(
            f"cannot infer the light type for platform '{entity.platform}'; use `as:`"
        )
    return LoweredEndpoint({device_type: bind(device_type, entity)})


@rule("sensor")
def _sensor(entity: Entity, overrides: Overrides) -> LoweredEndpoint | None:
    hint = entity.hint
    if (
        hint == "temperature"
        or entity.platform in _TEMPERATURE_PLATFORMS
        or (hint is None and entity.unit in _TEMPERATURE_UNITS)
    ):
        return LoweredEndpoint({"temperature_sensor": bind("temperature_sensor", entity)})
    if hint == "humidity":
        return LoweredEndpoint({"humidity_sensor": bind("humidity_sensor", entity)})
    return None


__all__ = [
    "FEATURES_KEY",
    "CONF_LABEL",
    "LoweredEndpoint",
    "NoMapping",
    "Overrides",
    "apply_overrides",
    "bind",
    "infer",
    "register_rule",
    "rule",
]
