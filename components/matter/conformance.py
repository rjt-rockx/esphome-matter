"""Matter spec conformance helpers for the explicit `endpoints:` engine.

Covers endpoint composition rules that are easy to get wrong by hand:

* `parent:`        explicit parent endpoint (PartsList), default 0.
* `tags:`          Descriptor TagList semantic tags per endpoint.
* `label:`         FixedLabel cluster with a "name" label per endpoint.
* automatic Common Number tags on sibling endpoints that share a device type
  (the Duplicate condition of the Matter device library makes TagList mandatory).
* a structure hash that is persisted at boot to bump the Basic Information
  ConfigurationVersion when the endpoint composition changes.

See docs/composition.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import zlib

import esphome.config_validation as cv

from .data_model.device_types import DEVICE_TYPES_BY_CONF_KEY

_LOGGER = logging.getLogger(__name__)

CONF_PARENT = "parent"
CONF_LABEL = "label"
CONF_TAGS = "tags"
CONF_NAMESPACE = "namespace"
CONF_TAG = "tag"

# FixedLabel / UserLabel label strings and semantic tag labels are limited to 16 bytes.
LABEL_MAX = 16
# esp-matter stores ESP_MATTER_MAX_SEMANTIC_TAG_COUNT (3) tags per endpoint.
MAX_TAGS_PER_ENDPOINT = 3

FIXED_LABEL_CLUSTER = "FixedLabel"
FIXED_LABEL_NAME = "name"

NAMESPACE_COMMON_NUMBER = 0x07
NAMESPACE_COMMON_POSITION = 0x08
NAMESPACE_SWITCHES = 0x43

# Common Number tags are Zero (0) to Thirty (30).
_NUMBER_TAG_NAMES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty twenty_one twenty_two "
    "twenty_three twenty_four twenty_five twenty_six twenty_seven twenty_eight "
    "twenty_nine thirty"
).split()
NUMBER_TAG_MAX = len(_NUMBER_TAG_NAMES) - 1

# Standard namespaces from the Matter namespaces specification (1.4).
NAMESPACES: dict[str, int] = {
    "common_number": NAMESPACE_COMMON_NUMBER,
    "common_position": NAMESPACE_COMMON_POSITION,
    "switches": NAMESPACE_SWITCHES,
}
NAMESPACE_TAGS: dict[int, dict[str, int]] = {
    NAMESPACE_COMMON_NUMBER: {name: i for i, name in enumerate(_NUMBER_TAG_NAMES)},
    NAMESPACE_COMMON_POSITION: {
        name: i
        for i, name in enumerate(("left", "right", "top", "bottom", "middle", "row", "column"))
    },
    NAMESPACE_SWITCHES: {
        name: i
        for i, name in enumerate(
            (
                "on",
                "off",
                "toggle",
                "up",
                "down",
                "next",
                "previous",
                "enter_ok_select",
                "custom",
                "open",
                "close",
                "stop",
            )
        )
    },
}

# Utility / system device types. They never count as "application" device types
# for the Duplicate condition.
UTILITY_DEVICE_TYPE_IDS = frozenset(
    {
        0x0011,  # power_source
        0x0012,  # ota_requestor
        0x0013,  # bridged_node
        0x0014,  # ota_provider
        0x0016,  # root_node
        0x0019,  # secondary_network_interface
        0x0510,  # electrical_sensor
    }
)
AGGREGATOR_DEVICE_TYPE_ID = 0x000E

# Bumped when the way the hash is built changes on purpose.
_HASH_VERSION = 1


def _label(value):
    """Explicit user-provided label: over-long values are errors, never truncated."""
    value = cv.string_strict(value)
    size = len(value.encode())
    if not value:
        raise cv.Invalid(f"Labels must be 1 to {LABEL_MAX} UTF-8 bytes long, got an empty string")
    if size > LABEL_MAX:
        raise cv.Invalid(
            f"Label '{value}' is {size} UTF-8 bytes long, but Matter labels are limited to "
            f"{LABEL_MAX} bytes; shorten it"
        )
    return value


def default_label(name: str, source: str = "entity name") -> str:
    """Label derived by default from `name` (e.g. an entity name).

    Unlike an explicit `label:`, a derived label that exceeds LABEL_MAX bytes is
    truncated on a UTF-8 character boundary with a config-time warning.
    """
    encoded = name.encode()
    if len(encoded) <= LABEL_MAX:
        return name
    truncated = encoded[:LABEL_MAX].decode("utf-8", errors="ignore").rstrip()
    _LOGGER.warning(
        "Matter endpoint label derived from %s '%s' is %d bytes; truncated to '%s' "
        "(limit %d bytes). Set an explicit `label:` (max %d bytes) to choose it yourself",
        source, name, len(encoded), truncated, LABEL_MAX, LABEL_MAX,
    )
    return truncated


def _namespace(value):
    if isinstance(value, str) and value.lower() in NAMESPACES:
        return NAMESPACES[value.lower()]
    try:
        return cv.int_range(min=0, max=0xFF)(value)
    except cv.Invalid:
        raise cv.Invalid(
            f"Unknown semantic tag namespace '{value}'. Use a number or one of: "
            + ", ".join(NAMESPACES)
        ) from None


def _tag_entry(value):
    value = cv.Schema(
        {
            cv.Required(CONF_NAMESPACE): _namespace,
            cv.Required(CONF_TAG): cv.Any(cv.int_range(min=0, max=0xFF), cv.string_strict),
            cv.Optional(CONF_LABEL): _label,
        }
    )(value)
    tag = value[CONF_TAG]
    if isinstance(tag, str):
        names = NAMESPACE_TAGS.get(value[CONF_NAMESPACE], {})
        if tag.lower() not in names:
            raise cv.Invalid(
                f"Unknown tag '{tag}' for namespace 0x{value[CONF_NAMESPACE]:02X}. "
                f"Known tags: {', '.join(names) or 'none (use a number)'}"
            )
        value = {**value, CONF_TAG: names[tag.lower()]}
    return value


# Added to ENDPOINT_SCHEMA in endpoints.py.
SCHEMA_OPTIONS = {
    cv.Optional(CONF_PARENT): cv.int_range(min=0, max=65534),
    cv.Optional(CONF_LABEL): _label,
    cv.Optional(CONF_TAGS): cv.All(
        cv.ensure_list(_tag_entry), cv.Length(min=1, max=MAX_TAGS_PER_ENDPOINT)
    ),
}


@dataclass(frozen=True)
class Tag:
    namespace: int
    tag: int
    label: str | None = None

    @property
    def key(self) -> tuple[int, int]:
        return (self.namespace, self.tag)


@dataclass
class EndpointPlan:
    parent: int = 0
    label: str | None = None
    tags: list[Tag] = field(default_factory=list)
    # Number tag that was generated because of the Duplicate condition.
    auto_tag: Tag | None = None


def application_device_type_ids(endpoint_config: dict) -> set[int]:
    ids = set()
    for conf_key in endpoint_config:
        device_type = DEVICE_TYPES_BY_CONF_KEY.get(conf_key)
        if device_type is not None and device_type.id not in UTILITY_DEVICE_TYPE_IDS:
            ids.add(device_type.id)
    return ids


def _duplicate_components(endpoints: dict[int, dict]) -> list[list[int]]:
    """Group sibling endpoints (same parent) connected by overlapping application device types."""
    by_parent: dict[int, list[int]] = {}
    for endpoint_id, config in endpoints.items():
        if endpoint_id == 0:
            continue  # the root node is nobody's sibling
        by_parent.setdefault(config.get(CONF_PARENT, 0), []).append(endpoint_id)

    components: list[list[int]] = []
    for siblings in by_parent.values():
        siblings.sort()
        types = {ep: application_device_type_ids(endpoints[ep]) for ep in siblings}
        remaining = list(siblings)
        while remaining:
            component = [remaining.pop(0)]
            changed = True
            while changed:
                changed = False
                for other in list(remaining):
                    if any(types[other] & types[member] for member in component):
                        component.append(other)
                        remaining.remove(other)
                        changed = True
            if len(component) > 1:
                components.append(sorted(component))
    return components


def validate_composition(endpoints: dict[int, dict]) -> None:
    """Cross-endpoint checks. Raises cv.Invalid."""
    for endpoint_id, config in endpoints.items():
        parent = config.get(CONF_PARENT, 0)
        if parent == 0:
            continue
        if parent == endpoint_id:
            raise cv.Invalid(f"Endpoint {endpoint_id} cannot be its own parent")
        if parent not in endpoints:
            raise cv.Invalid(
                f"Endpoint {endpoint_id} has parent {parent} which is not a configured endpoint"
            )
        seen = {endpoint_id}
        current = parent
        while current != 0:
            if current in seen:
                raise cv.Invalid(f"Endpoint {endpoint_id} is part of a parent cycle")
            seen.add(current)
            current = endpoints[current].get(CONF_PARENT, 0)
    plan_endpoints(endpoints, log=False)


def plan_endpoints(
    endpoints: dict[int, dict], *, log: bool = True
) -> dict[int, EndpointPlan]:
    """Resolve parent, label and tags (including automatic Number tags) per endpoint."""
    plans: dict[int, EndpointPlan] = {}
    for endpoint_id, config in endpoints.items():
        plans[endpoint_id] = EndpointPlan(
            parent=config.get(CONF_PARENT, 0),
            label=config.get(CONF_LABEL),
            tags=[
                Tag(t[CONF_NAMESPACE], t[CONF_TAG], t.get(CONF_LABEL))
                for t in config.get(CONF_TAGS, ())
            ],
        )

    for component in _duplicate_components(endpoints):
        used_numbers = {
            tag.tag
            for ep in component
            for tag in plans[ep].tags
            if tag.namespace == NAMESPACE_COMMON_NUMBER
        }
        next_number = 1
        for ep in component:
            plan = plans[ep]
            if plan.tags:
                continue
            while next_number in used_numbers:
                next_number += 1
            if next_number > NUMBER_TAG_MAX:
                raise cv.Invalid(
                    f"Endpoints {component} share a device type and need more Common Number tags "
                    f"than the {NUMBER_TAG_MAX} that exist; add explicit `tags:` to distinguish them"
                )
            used_numbers.add(next_number)
            plan.auto_tag = Tag(NAMESPACE_COMMON_NUMBER, next_number, plan.label)
            plan.tags = [plan.auto_tag]
            if log:
                _LOGGER.info(
                    "Endpoint %d shares its device type with sibling endpoints %s: added "
                    "Descriptor tag Common Number '%s'",
                    ep,
                    [e for e in component if e != ep],
                    _NUMBER_TAG_NAMES[next_number],
                )
        seen: dict[frozenset, int] = {}
        for ep in component:
            tag_set = frozenset(tag.key for tag in plans[ep].tags)
            if tag_set in seen:
                raise cv.Invalid(
                    f"Endpoints {seen[tag_set]} and {ep} share a device type and have identical "
                    "`tags:`. Sibling endpoints with a common device type must be distinguishable "
                    "by their TagList (Matter Duplicate condition)"
                )
            seen[tag_set] = ep
    return plans


def structure_hash(endpoints: dict[int, dict], plans: dict[int, EndpointPlan], signatures: dict[int, str]) -> int:
    """32-bit hash of everything that controllers cache about the composition."""
    parts = [f"v{_HASH_VERSION}"]
    for endpoint_id in sorted(endpoints):
        plan = plans[endpoint_id]
        tags = ",".join(f"{t.namespace}:{t.tag}:{t.label or ''}" for t in plan.tags)
        parts.append(
            f"{endpoint_id}|p{plan.parent}|l{plan.label or ''}|t{tags}|{signatures[endpoint_id]}"
        )
    return zlib.crc32("\n".join(parts).encode()) & 0xFFFFFFFF


def max_device_types(endpoints: dict[int, dict]) -> int:
    count = 1
    for endpoint_id, config in endpoints.items():
        types = sum(1 for key in config if key in DEVICE_TYPES_BY_CONF_KEY)
        if endpoint_id == 0:
            # The root node device type, plus the Power Source device type for battery / wired.
            types += 1 + (1 if "battery" in config or "wired" in config else 0)
        count = max(count, types)
    return count
