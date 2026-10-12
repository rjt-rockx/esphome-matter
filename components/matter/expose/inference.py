"""Device type inference for `matter: expose:` (DIRECTION.md section 6).

Inference is a registry: `domain -> [rule]`. A rule is `callable(entity, overrides) ->
LoweredEndpoint | None` and may raise `NoMapping(reason)`; `None` declines so the next rule for the
domain is tried. Register more rules with `@rule("binary_sensor")` (or `register_rule`) from any
module that is imported before `lower_expose` runs; nothing else has to change.

Rules must only use what the raw config offers (see inventory.py). Anything that depends on
validated values is re-checked in final_check.py.
"""

import difflib
import json
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
    tags: list | None = None
    parent: str | int | None = None
    clusters: dict | None = None
    # Set by the selector when the entity was picked by a glob or `all`, not by its id. Rules may
    # decline entities that only make sense when asked for by id. Not part of key().
    via_glob: bool = False

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
            json.dumps(self.tags, sort_keys=True, default=str) if self.tags is not None else None,
            self.parent,
            json.dumps(self.clusters, sort_keys=True, default=str)
            if self.clusters is not None
            else None,
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
    auto_attach: "meter" or "battery". Without `attach_to:`/`meters:` the planner attaches the
      entity to the endpoint of the load or device it belongs to when that is unambiguous, and
      otherwise gives it the standalone endpoint described by device_types.
    attach_extra: what an attached entity adds to the target endpoint instead of device_types
      (e.g. `{"battery": id}`: the Power Source rides on the target endpoint).
    """

    device_types: dict[str, dict] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    merge_group: str | None = None
    auto_attach: str | None = None
    attach_extra: dict[str, Any] | None = None

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
}
# Domains that become a Generic Switch without a binding: the user sends the events with
# `matter.send_switch_event` (event: on_event, button: on_press).
UNBOUND_SWITCH_DOMAINS = frozenset({"event", "button"})

# What to try when no rule applies, per domain. Replaces the plain "use `as:`".
NO_RULE_HINTS = {
    "binary_sensor": (
        "set a `device_class:` on it (door, window, opening, garage_door, motion, occupancy, "
        "presence, moisture, cold, smoke, carbon_monoxide), or use `as: contact_sensor`, "
        "`as: occupancy_sensor`, `as: generic_switch`, ..."
    ),
    "sensor": (
        "supported device_class values: temperature, humidity, pressure, illuminance, "
        "volume_flow_rate, carbon_dioxide, carbon_monoxide, pm1, pm25, pm10, "
        "volatile_organic_compounds, nitrogen_dioxide, ozone, power, energy, voltage, current, "
        "apparent_power, frequency, power_factor, battery; or use `as:`"
    ),
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
    if device_type == "generic_switch" and entity.domain in UNBOUND_SWITCH_DOMAINS:
        return {}
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
            how = NO_RULE_HINTS.get(entity.domain, "use `as:`")
            raise NoMapping(
                f"no device type is inferred for this {entity.domain}"
                f"{f' ({entity.hint})' if entity.hint else ''}; {how}"
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


# --- sensors -------------------------------------------------------------------------------

# Nested sub-sensor keys (`scd4x: co2: {...}`) that stand in for a device_class.
_HINT_ALIASES = {
    "co2": "carbon_dioxide",
    "co": "carbon_monoxide",
    "pm_1_0": "pm1",
    "pm_1": "pm1",
    "pm_2_5": "pm25",
    "pm_10_0": "pm10",
    "pm_10": "pm10",
    "voc": "volatile_organic_compounds",
    "tvoc": "volatile_organic_compounds",
    "atmospheric_pressure": "pressure",
}
_SINGLE_SENSOR_TYPES = {
    "humidity": "humidity_sensor",
    "pressure": "pressure_sensor",
    "illuminance": "light_sensor",
    "volume_flow_rate": "flow_sensor",
}
# Air quality: hint -> attribute of the air_quality_sensor device type. All of them share the
# endpoint `group:air_quality`. The AirQuality enum stays Unknown; temperature and humidity
# sensors stay separate endpoints (`attach_to:` the group endpoint merges them).
_AIR_QUALITY_ATTRS = {
    "carbon_dioxide": "carbon_dioxide",
    "carbon_monoxide": "carbon_monoxide",
    "pm1": "pm_1",
    "pm25": "pm_2_5",
    "pm10": "pm_10",
    "volatile_organic_compounds": "total_voc",
    "volatile_organic_compounds_parts": "total_voc",
    "nitrogen_dioxide": "nitrogen_dioxide",
    "ozone": "ozone",
    "formaldehyde": "formaldehyde",
    "radon": "radon",
}
# Metering: hint -> attribute of the electrical_sensor device type.
_ELECTRICAL_ATTRS = {
    "power": "active_power",
    "energy": "energy_imported",
    "voltage": "voltage",
    "current": "active_current",
    "apparent_power": "apparent_power",
    "reactive_power": "reactive_power",
    "frequency": "frequency",
    "power_factor": "power_factor",
}


@rule("sensor")
def _sensor(entity: Entity, overrides: Overrides) -> LoweredEndpoint | None:
    hint = _HINT_ALIASES.get(entity.hint, entity.hint)
    if (
        hint == "temperature"
        or entity.platform in _TEMPERATURE_PLATFORMS
        or (hint is None and entity.unit in _TEMPERATURE_UNITS)
    ):
        return LoweredEndpoint({"temperature_sensor": bind("temperature_sensor", entity)})
    if hint in _SINGLE_SENSOR_TYPES:
        name = _SINGLE_SENSOR_TYPES[hint]
        return LoweredEndpoint({name: bind(name, entity)})
    if hint in _AIR_QUALITY_ATTRS:
        return LoweredEndpoint(
            {"air_quality_sensor": {_AIR_QUALITY_ATTRS[hint]: entity.id}},
            merge_group="air_quality",
        )
    if hint in _ELECTRICAL_ATTRS:
        return LoweredEndpoint(
            {"electrical_sensor": {_ELECTRICAL_ATTRS[hint]: entity.id}},
            merge_group="electrical",
            auto_attach="meter",
        )
    if hint == "battery":
        # Power Source on the endpoint of the device it powers; a standalone power_source
        # endpoint when that endpoint is ambiguous.
        return LoweredEndpoint(
            {"power_source": bind("power_source", entity)},
            auto_attach="battery",
            attach_extra={"battery": entity.id},
        )
    return None


# --- binary sensors ------------------------------------------------------------------------

_BINARY_SENSOR_TYPES = {
    "door": "contact_sensor",
    "window": "contact_sensor",
    "opening": "contact_sensor",
    "garage_door": "contact_sensor",
    "motion": "occupancy_sensor",
    "occupancy": "occupancy_sensor",
    "presence": "occupancy_sensor",
    "moisture": "water_leak_detector",
    "cold": "water_freeze_detector",
}
_SMOKE_CO_KEYS = {"smoke": "smoke_state", "carbon_monoxide": "co_state"}


@rule("binary_sensor")
def _binary_sensor(entity: Entity, overrides: Overrides) -> LoweredEndpoint | None:
    hint = entity.hint
    if hint in _BINARY_SENSOR_TYPES:
        name = _BINARY_SENSOR_TYPES[hint]
        return LoweredEndpoint({name: bind(name, entity)})
    if hint in _SMOKE_CO_KEYS:
        # A smoke and a CO sensor share one Smoke CO Alarm endpoint.
        return LoweredEndpoint(
            {"smoke_co_alarm": {_SMOKE_CO_KEYS[hint]: entity.id}}, merge_group="smoke_co_alarm"
        )
    return None  # no class, or one without a Matter counterpart: never guessed


# --- event, button -------------------------------------------------------------------------


@rule("event")
def _event(entity: Entity, overrides: Overrides) -> LoweredEndpoint:
    return LoweredEndpoint({"generic_switch": {}})


@rule("button")
def _button(entity: Entity, overrides: Overrides) -> LoweredEndpoint:
    if overrides.via_glob:
        raise NoMapping(
            "a button has no events; list its id in `expose:` to get an unbound Generic Switch "
            "that `on_press:` drives with matter.send_switch_event"
        )
    return LoweredEndpoint({"generic_switch": {}})


# --- cover, fan, lock, valve, climate ------------------------------------------------------

# Cover classes that are not blinds. They are exposed as Window Covering with `closure_compat`
# (wave-closure), which acknowledges that controllers show them as a shutter. The native Closure
# device type is never inferred; it stays opt-in through `as:`.
_NON_BLIND_COVERS = {"garage", "gate", "door"}
# climate_ir and similar platforms are air conditioners; everything else is a thermostat.
_AC_CLIMATE_PLATFORMS = frozenset(
    {
        "climate_ir_lg", "coolix", "daikin", "daikin_arc", "daikin_brc", "fujitsu_general",
        "heatpumpir", "hitachi_ac344", "hitachi_ac424", "midea", "mitsubishi", "tcl112",
        "toshiba", "whirlpool", "yashima", "zhlt01", "samsung", "gree", "emmeti", "haier",
        "delonghi", "noblex", "ballu",
    }
)  # fmt: skip


@rule("cover")
def _cover(entity: Entity, overrides: Overrides) -> LoweredEndpoint:
    block = bind("window_covering", entity)
    if entity.device_class in _NON_BLIND_COVERS:
        block["closure_compat"] = True
    return LoweredEndpoint({"window_covering": block})


@rule("fan")
def _fan(entity: Entity, overrides: Overrides) -> LoweredEndpoint:
    return LoweredEndpoint({"fan": bind("fan", entity)})


@rule("lock")
def _lock(entity: Entity, overrides: Overrides) -> LoweredEndpoint:
    return LoweredEndpoint({"door_lock": bind("door_lock", entity)})


@rule("valve")
def _valve(entity: Entity, overrides: Overrides) -> LoweredEndpoint:
    return LoweredEndpoint({"water_valve": bind("water_valve", entity)})


@rule("climate")
def _climate(entity: Entity, overrides: Overrides) -> LoweredEndpoint:
    name = "room_air_conditioner" if entity.platform in _AC_CLIMATE_PLATFORMS else "thermostat"
    return LoweredEndpoint({name: bind(name, entity)})


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
