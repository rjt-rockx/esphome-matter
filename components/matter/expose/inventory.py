"""Inventory of ESPHome entities read from the raw (unvalidated) config.

`CORE.raw_config` holds the user config after packages and substitutions and before any schema
ran. Only literal keys are visible: explicit `id:` strings, `platform:`, `device_class:`, `name:`,
`internal:`, `entity_category:` and `unit_of_measurement:`. Platform defaults are not applied;
the FINAL_VALIDATE re-check (final_check.py) compares the inference with the validated entity.
"""

from dataclasses import dataclass, field

# Entity domains the inventory walks. Domains without an inference rule are still listed so that
# an explicit id gets the "cannot be exposed because ..." error instead of "no entity".
ENTITY_DOMAINS = (
    "alarm_control_panel",
    "binary_sensor",
    "button",
    "climate",
    "cover",
    "datetime",
    "event",
    "fan",
    "light",
    "lock",
    "media_player",
    "number",
    "select",
    "sensor",
    "switch",
    "text",
    "text_sensor",
    "update",
    "valve",
)
# Domains whose platforms commonly nest sub-entities (`scd4x: temperature: {id: t}`).
NESTED_DOMAINS = ("sensor", "binary_sensor", "text_sensor")
MAX_NESTING = 2

CONF_ID = "id"
CONF_NAME = "name"
CONF_PLATFORM = "platform"
CONF_DEVICE_CLASS = "device_class"
CONF_INTERNAL = "internal"
CONF_ENTITY_CATEGORY = "entity_category"
CONF_UNIT = "unit_of_measurement"
NON_TRAIT_KEYS = {CONF_ID, CONF_NAME, CONF_PLATFORM}
# Top-level platforms whose schema defaults `entity_category: diagnostic`. The raw config does
# not show that, so globs and `all` skip them up front (final_check.py catches the rest).
DIAGNOSTIC_DEFAULT_PLATFORMS = frozenset(
    {
        "debug",
        "internal_temperature",
        "openthread_info",
        "packet_transport",
        "status",
        "uptime",
        "wifi_signal",
        "wireguard",
    }
)


@dataclass
class Entity:
    domain: str
    platform: str | None
    id: str | None
    name: str | None = None
    internal: bool = False
    entity_category: str | None = None
    device_class: str | None = None
    unit: str | None = None
    # Key of the nested sub-entity (`temperature`, `pm_2_5`), used as a device_class hint.
    sub_key: str | None = None
    # Raw keys of the entity block, e.g. {"red", "green", "blue"} for a light.
    traits: frozenset[str] = frozenset()
    path: tuple = ()
    # Raw values of the entity block that inference rules may need (only scalars and ids).
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def exposable(self) -> bool:
        """Only entities with an explicit `id:` can be referenced from `endpoints:`."""
        return self.id is not None

    @property
    def hint(self) -> str | None:
        """Best device_class guess: explicit class, else the nested sub-key."""
        return self.device_class or self.sub_key

    @property
    def skipped_by_glob(self) -> str | None:
        """Why globs and `all` skip this entity, or None."""
        if self.internal:
            return "internal: true" if self.name else "no name (internal)"
        if self.entity_category in ("config", "diagnostic"):
            return f"entity_category: {self.entity_category}"
        if (
            self.entity_category is None
            and self.platform in DIAGNOSTIC_DEFAULT_PLATFORMS
        ):
            return "entity_category: diagnostic (platform default)"
        return None

    @property
    def label(self) -> str:
        return self.id or f"<no id: {'.'.join(map(str, self.path))}>"


def _text(value) -> str | None:
    value = getattr(value, "id", value)  # tolerate ID objects
    return value if isinstance(value, str) else None


def make_entity(domain, platform, node: dict, sub_key, path) -> Entity:
    name = node.get(CONF_NAME)
    return Entity(
        domain=domain,
        platform=platform,
        id=_text(node.get(CONF_ID)),
        name=name if isinstance(name, str) and name else None,
        # ESPHome forces nameless entities to internal: true during validation.
        internal=bool(node.get(CONF_INTERNAL, False)) or not name,
        entity_category=_text(node.get(CONF_ENTITY_CATEGORY)),
        device_class=_text(node.get(CONF_DEVICE_CLASS)),
        unit=_text(node.get(CONF_UNIT)),
        sub_key=sub_key,
        traits=frozenset(
            k for k in node if isinstance(k, str) and k not in NON_TRAIT_KEYS
        ),
        path=tuple(path),
        raw={k: v for k, v in node.items() if isinstance(v, (str, int, float, bool))},
    )


def _visit(domain, platform, node, sub_key, path, depth, out: list[Entity]) -> None:
    if not isinstance(node, dict):
        return
    if sub_key is None or CONF_ID in node or CONF_NAME in node:
        out.append(make_entity(domain, platform, node, sub_key, path))
    if domain not in NESTED_DOMAINS or depth >= MAX_NESTING:
        return
    for key, value in node.items():
        if (
            isinstance(value, dict)
            and isinstance(key, str)
            and not key.startswith("on_")
        ):
            _visit(domain, platform, value, key, [*path, key], depth + 1, out)


def build_inventory(raw: dict | None) -> list[Entity]:
    """All entities of the raw config, in config order. Nested sub-sensors are included."""
    out: list[Entity] = []
    for domain in (d for d in (raw or {}) if d in ENTITY_DOMAINS):
        items = raw.get(domain)
        if isinstance(items, dict):  # single-platform shorthand
            items = [items]
        for index, item in enumerate(items or []):
            if isinstance(item, dict):
                _visit(
                    domain, item.get(CONF_PLATFORM), item, None, [domain, index], 0, out
                )
    return out


def by_id(inventory: list[Entity]) -> dict[str, Entity]:
    """Entities with an explicit id (the first one wins on duplicates; ESPHome rejects those)."""
    result: dict[str, Entity] = {}
    for entity in inventory:
        if entity.id is not None:
            result.setdefault(entity.id, entity)
    return result
