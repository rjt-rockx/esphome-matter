"""Lookup of the esp-matter C++ identifiers for the spec names of our data model.

``espmatter_names.json`` is generated from the esp-matter headers by
``tools/gen_espmatter_names.py``; never guess identifiers by snake_casing spec
names. Lookups return ``None`` for things esp-matter does not have; things that
are missing from the table altogether (a stale table) fall back to the old
snake_case guess with a loud warning.
"""

import json
import logging
from pathlib import Path

from ..util import snake_case

_LOGGER = logging.getLogger(__name__)

_TABLE_FILE = Path(__file__).resolve().parent / "espmatter_names.json"

with open(_TABLE_FILE, "r") as _file:
    TABLE: dict = json.load(_file)

_warned: set[str] = set()


def _warn_stale(what: str) -> None:
    if what in _warned:
        return
    _warned.add(what)
    _LOGGER.warning(
        "%s is missing from espmatter_names.json, guessing its esp-matter "
        "identifier by snake_casing the spec name. The generated code may not "
        "compile; regenerate the table with tools/gen_espmatter_names.py",
        what,
    )


def _fallback_namespace(name: str) -> str:
    return snake_case(name.replace(" And ", "").replace(" ", ""))


def cluster_entry(cluster_name: str) -> dict | None:
    """Table entry of a cluster by its CamelCase name (Cluster.name)."""
    return TABLE["clusters"].get(cluster_name)


def cluster_namespace(cluster_name: str, fallback: str) -> str | None:
    """esp_matter::cluster::<namespace>, None if esp-matter lacks the cluster.

    ``fallback`` is used (with a warning) if the table doesn't know the cluster.
    """
    entry = cluster_entry(cluster_name)
    if entry is None:
        _warn_stale(f"Cluster {cluster_name}")
        return fallback
    return entry["namespace"]


def feature_namespace(cluster_name: str, feature_name: str) -> str | None:
    """esp_matter::cluster::<cluster>::feature::<namespace>, None if unavailable."""
    entry = cluster_entry(cluster_name)
    if entry is not None and entry["namespace"] is None:
        return None
    feature = (entry or {}).get("features", {}).get(feature_name)
    if feature is None:
        _warn_stale(f"Feature {feature_name} of cluster {cluster_name}")
        return _fallback_namespace(feature_name)
    return feature["namespace"]


def attribute_create_function(cluster_name: str, attribute_name: str) -> str | None:
    """Name of the esp_matter::cluster::<cluster>::attribute::create_* function."""
    entry = cluster_entry(cluster_name)
    if entry is not None and entry["namespace"] is None:
        return None
    attributes = (entry or {}).get("attributes", {})
    if attribute_name not in attributes:
        _warn_stale(f"Attribute {attribute_name} of cluster {cluster_name}")
        return f"create_{snake_case(attribute_name)}"
    return attributes[attribute_name]


def device_type_entry(device_type_name: str) -> dict | None:
    return TABLE["device_types"].get(device_type_name)


def device_type_namespace(device_type_name: str) -> str | None:
    """esp_matter::endpoint::<namespace>, None if esp-matter lacks the device type."""
    entry = device_type_entry(device_type_name)
    if entry is None:
        _warn_stale(f"Device type {device_type_name}")
        return device_type_name
    return entry["namespace"]
