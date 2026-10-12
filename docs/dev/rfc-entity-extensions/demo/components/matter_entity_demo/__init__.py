"""Demo external component for the entity-extension RFC (needs the patched ESPHome).

It registers a nested per-entity key, `matter_entity_demo:`, with the shape of a
Matter expose item (`as`, `endpoint`, `features`, `label`) on the domains the
prototype patch wires up, and prints the endpoint plan the real component would
lower. It is NOT part of the shipped component and uses no monkeypatch. On an
unpatched ESPHome the import below fails; a real component would feature-check
(`hasattr(loader.ComponentManifest, "entity_extensions")`) and fall back to
`matter: expose:`.
"""

import logging

import esphome.config_validation as cv
from esphome.const import CONF_ID
from esphome.core import CORE
from esphome.core.entity_extensions import EntityExtension

_LOGGER = logging.getLogger(__name__)

DOMAIN = "matter_entity_demo"
CONF_AS = "as"
CONF_ENDPOINT = "endpoint"
CONF_FEATURES = "features"
CONF_LABEL = "label"

CONFIG_SCHEMA = cv.Schema({})

# Same keys as an `expose:` item minus `id` (the entity is the id).
ITEM_SCHEMA = cv.Schema(
    {
        cv.Optional(CONF_AS): cv.validate_id_name,
        cv.Optional(CONF_ENDPOINT): cv.int_range(1, 65534),
        cv.Optional(CONF_FEATURES): cv.ensure_list(cv.validate_id_name),
        cv.Optional(CONF_LABEL): cv.All(cv.string, cv.Length(max=32)),
    }
)


def _item(value):
    if value is True:
        return ITEM_SCHEMA({})
    if value is False:
        raise cv.Invalid("use `true` or a mapping; remove the key to not expose")
    return ITEM_SCHEMA(value)


def endpoint_for_key(key: str) -> int:
    """DIRECTION.md section 7 hash (FNV-1a 32, folded to 16 bits)."""
    h = 0x811C9DC5
    for b in key.encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    h = (h >> 16) ^ (h & 0xFFFF)
    return h % 65534 + 1


def _validate_factory(domain):
    def validate(config):
        item = config.get(DOMAIN)
        if item is None:
            return config
        if not config[CONF_ID].is_manual:
            raise cv.Invalid(
                "an exposed entity needs an explicit id: (it keys the endpoint number)",
                [DOMAIN],
            )
        key = str(config[CONF_ID].id)
        endpoint = item.get(CONF_ENDPOINT, endpoint_for_key(key))
        CORE.data.setdefault(DOMAIN, {})[key] = (domain, endpoint, item)
        return config

    return validate


ENTITY_EXTENSIONS = [
    EntityExtension(
        domain,
        _item,
        key=DOMAIN,
        validate=_validate_factory(domain),
    )
    for domain in ("binary_sensor", "number", "sensor", "switch")
]


def _final_validate(config):
    plan = CORE.data.get(DOMAIN, {})
    seen = {}
    for key, (domain, endpoint, item) in sorted(plan.items()):
        if endpoint in seen:
            raise cv.Invalid(
                f"endpoint {endpoint} collides: {seen[endpoint]} and {key}"
            )
        seen[endpoint] = key
        _LOGGER.info(
            "plan: endpoint %5d <- %s.%s as=%s features=%s label=%s",
            endpoint,
            domain,
            key,
            item.get(CONF_AS, "<inferred>"),
            item.get(CONF_FEATURES, []),
            item.get(CONF_LABEL, "<entity name>"),
        )
    return config


FINAL_VALIDATE_SCHEMA = _final_validate


async def to_code(config):
    pass
