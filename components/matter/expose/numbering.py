"""Stable endpoint numbers for `matter: expose:` (DIRECTION.md section 7).

An endpoint number is a pure function of the entity key. Nothing is salted, probed or stored:
a collision is a compile error that suggests a pin.
"""

import re

import esphome.config_validation as cv

ENDPOINT_MIN = 1
ENDPOINT_MAX = 65534
GROUP_PREFIX = "group:"


def endpoint_for_key(key: str) -> int:
    """FNV-1a 32 folded to 16 bits, mapped to 1..65534 (never 0 or 0xFFFF)."""
    h = 0x811C9DC5
    for b in key.encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF  # FNV-1a 32
    h = (h >> 16) ^ (h & 0xFFFF)  # fold to 16 bits
    return h % 65534 + 1  # 1..65534


def group_key(name: str) -> str:
    """Key of a merge group, e.g. `group:air_quality`."""
    return f"{GROUP_PREFIX}{name}"


def natural_key(text: str) -> list:
    """Sort key that orders `relay_2` before `relay_10`."""
    return [
        (0, int(p), "") if p.isdigit() else (1, 0, p)
        for p in re.split(r"(\d+)", text)
        if p != ""
    ]


def _free_suggestion(key: str, taken: set[int]) -> int:
    """First free number reachable by salting the key. Printed only, never applied."""
    salt = 1
    while (n := endpoint_for_key(f"{key}#{salt}")) in taken:
        salt += 1
    return n


def resolve_numbers(
    keys: list[str], pins: dict[str, int], reserved: set[int] | None = None
) -> dict[str, int]:
    """Return key -> endpoint number for every key in `keys` (pinned or hashed).

    `pins` maps keys to explicit numbers (item `endpoint:`). `reserved` holds numbers pinned
    without a key (hand-written `endpoints:` entries). Duplicate pins and any hash collision
    raise cv.Invalid naming both keys.
    """
    reserved = set(reserved or ())
    owners: dict[int, list[str]] = {}
    for key, number in pins.items():
        if not ENDPOINT_MIN <= number <= ENDPOINT_MAX:
            raise cv.Invalid(
                f"matter: endpoint pin {number} for '{key}' is outside 1..{ENDPOINT_MAX}"
            )
        if number in reserved:
            raise cv.Invalid(
                f"matter: endpoint {number} is pinned for '{key}' but is already defined "
                "under `endpoints:`"
            )
        owners.setdefault(number, []).append(key)
    for number, who in sorted(owners.items()):
        if len(who) > 1:
            raise cv.Invalid(
                f"matter: endpoint {number} is pinned twice: "
                f"{' and '.join(sorted(who, key=natural_key))}"
            )

    result = dict(pins)
    taken = set(owners) | reserved
    hashed: dict[int, list[str]] = {}
    for key in sorted((k for k in keys if k not in pins), key=natural_key):
        hashed.setdefault(endpoint_for_key(key), []).append(key)
    avoid = taken | set(hashed)

    for number, who in sorted(hashed.items()):
        if number in reserved or number in owners:
            holder = (
                f"{owners[number][0]} (pinned)"
                if number in owners
                else "`endpoints:` (pinned)"
            )
            alt = _free_suggestion(who[0], avoid)
            raise cv.Invalid(
                f"matter: endpoint {number} collides: {who[0]} and {holder}. "
                f"Pin {who[0]} elsewhere: {{id: {who[0]}, endpoint: {alt}}} ({alt} is free)."
            )
        if len(who) > 1:
            first, keep = who[0], who[-1]
            alt = _free_suggestion(first, avoid)
            raise cv.Invalid(
                f"matter: endpoint {number} collides: {' and '.join(who)}. "
                f"Pin one: {{id: {keep}, endpoint: {number}}} keeps it; "
                f"{first} could use {alt} (free)."
            )
    for number, who in hashed.items():
        result[who[0]] = number
    return result
