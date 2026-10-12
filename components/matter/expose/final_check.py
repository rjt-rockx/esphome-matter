"""FINAL_VALIDATE re-check of the exposed entities (DIRECTION.md section 5).

Lowering runs on the raw config at schema time, before platform defaults are applied. Here the
validated entity (fv.full_config) is read again and compared with what the plan assumed. A
mismatch only warns and names the `as:` or `exclude:` fix; the config is never mutated.
"""

import logging

from esphome.core import CORE

from ..const import CONF_MATTER
from .inference import NoMapping, infer
from .inventory import Entity, make_entity
from .plan import KEY_EXPOSE_PLAN, ExposePlan, Member

_LOGGER = logging.getLogger(__name__)

# Device types that need a unit: conf key -> accepted units.
UNIT_RULES = {
    "temperature_sensor": ("°C", "°F", "K"),
    "humidity_sensor": ("%",),
}
# Light type implied by the validated color keys, most capable first.
_LIGHT_KEY_TYPES = (
    ("extended_color_light", ({"red", "green", "blue"}, {"hue", "saturation"})),
    ("color_temperature_light", ({"color_temperature"}, {"cold_white", "warm_white"})),
)
_LIGHT_PLATFORM_TYPES = {
    "binary": "on_off_light",
    "monochromatic": "dimmable_light",
    "cwww": "color_temperature_light",
    "color_temperature": "color_temperature_light",
    "rgb": "extended_color_light",
    "rgbw": "extended_color_light",
    "rgbww": "extended_color_light",
    "rgbct": "extended_color_light",
}


def _light_type(entity: Entity) -> str | None:
    """Light type implied by the platform, else by the color keys of the validated entity."""
    if entity.platform in _LIGHT_PLATFORM_TYPES:
        return _LIGHT_PLATFORM_TYPES[entity.platform]
    for name, key_sets in _LIGHT_KEY_TYPES:
        if any(keys <= entity.traits for keys in key_sets):
            return name
    return None


def check_member(member: Member, validated: Entity) -> list[str]:
    """Warnings for one entity: raw-config assumptions (member.entity) vs the validated entity."""
    raw, o = member.entity, member.overrides
    eid = raw.id
    drop = f"add `exclude: [{eid}]`" if not member.explicit else f"remove '{eid}' from `expose:`"
    out = []

    if validated.internal and not raw.internal:
        out.append(
            f"matter.expose: '{eid}' is internal: true after validation (a nameless entity or a "
            f"platform default) but is exposed; give it a `name:` or {drop}"
        )
    if (
        validated.entity_category in ("config", "diagnostic")
        and raw.entity_category != validated.entity_category
        and not raw.skipped_by_glob  # the schema-time warning already covered it
    ):
        out.append(
            f"matter.expose: '{eid}' has entity_category: {validated.entity_category} after "
            f"validation (a platform default) but is exposed; {drop} if that is not intended"
        )

    primary = member.lowered.primary
    if not o.as_:
        try:
            now = infer(validated, o).primary
            reason = ""
        except NoMapping as exc:
            now, reason = None, f" ({exc})"
        if now != primary:
            what = f"would now infer {now}" if now else f"no longer maps{reason}"
            out.append(
                f"matter.expose: '{eid}' was inferred as {primary} from the raw config but the "
                f"validated entity {what} (device_class {validated.device_class or 'none'}, "
                f"platform default). Add `as: {primary}` to keep it, or `as:` the type you want"
            )
    if raw.domain == "light":
        natural = _light_type(validated)
        if natural and natural != primary:
            out.append(
                f"matter.expose: light '{eid}' has color keys for {natural} but is exposed as "
                f"{primary}. Use `as: {natural}`, or keep {primary} if the reduced feature set "
                "is intended"
            )

    if primary in UNIT_RULES:
        accepted = UNIT_RULES[primary]
        unit = validated.unit
        if unit is None:
            out.append(
                f"matter.expose: '{eid}' is exposed as {primary} but has no unit_of_measurement "
                f"(expected one of {', '.join(accepted)}); set `unit_of_measurement:`"
            )
        elif unit not in accepted:
            out.append(
                f"matter.expose: '{eid}' is exposed as {primary} but its unit '{unit}' is not one "
                f"of {', '.join(accepted)}; fix the unit or use `as:` for another type"
            )
        elif raw.unit and raw.unit != unit:
            out.append(
                f"matter.expose: '{eid}' unit changed from '{raw.unit}' to '{unit}' during "
                "validation"
            )
    return out


def validated_entity(full_config, member: Member) -> Entity | None:
    """The validated entity of a member, or None when it cannot be located."""
    raw = member.entity
    try:
        path = full_config.get_path_for_id(raw.id)[:-1]
        conf = full_config.get_config_for_path(path)
    except (KeyError, AttributeError, TypeError):
        return None
    if not isinstance(conf, dict):
        return None
    return make_entity(raw.domain, conf.get("platform", raw.platform), conf, raw.sub_key, path)


def check_expose(config: dict, full_config) -> list[str]:
    """Run the re-check for every lowered endpoint. Logs and returns the warnings."""
    plan: ExposePlan | None = CORE.data.get(CONF_MATTER, {}).get(KEY_EXPOSE_PLAN)
    if plan is None:
        return []
    warnings = []
    for ep in plan.endpoints:
        for member in ep.members:
            validated = validated_entity(full_config, member)
            if validated is not None:
                warnings.extend(check_member(member, validated))
    for text in warnings:
        _LOGGER.warning(text)
    return warnings
