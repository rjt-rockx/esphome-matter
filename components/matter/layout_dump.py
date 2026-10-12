"""Opt-in export of the endpoint layout chosen at code generation time.

Set ``MATTER_LAYOUT_JSON=<path>`` while running ``esphome config`` / ``compile
--only-generate`` and the structure that ``Endpoint`` collected (device types,
explicitly created clusters, enabled features, optional attributes) is written
there as JSON. ``tools/conformance`` consumes it. Nothing happens when the
variable is unset.
"""

import json
import os

from esphome.core import CORE

ENV_VAR = "MATTER_LAYOUT_JSON"

# Clusters whose feature set is derived from the traits of the bound ESPHome
# entity in C++ at runtime, so the exported features are incomplete.
_TRAIT_DRIVEN = {
    "WindowCovering": "cover_id",
    "Thermostat": "climate_id",
}


def _network() -> dict:
    """Radios compiled into Matter, mirroring the sdkconfig choices in __init__.py."""
    thread = "openthread" in CORE.loaded_integrations
    wifi = "wifi" in CORE.loaded_integrations
    ethernet = "ethernet" in CORE.loaded_integrations
    if not (thread or wifi or ethernet):
        # Without connectivity components both Thread and Wi-Fi are built in.
        thread = wifi = True
    return {"wifi": wifi, "thread": thread, "ethernet": ethernet}


def dump_layout(endpoints) -> None:
    path = os.environ.get(ENV_VAR)
    if not path:
        return
    out = {
        "format": 1,
        "network": _network(),
        "endpoints": {},
    }
    for endpoint in endpoints:
        out["endpoints"][str(endpoint._endpoint_id)] = _endpoint_layout(endpoint)
    with open(path, "w") as file:
        json.dump(out, file, indent=1, sort_keys=True)


def _endpoint_layout(endpoint) -> dict:
    from .data_model.clusters import CLUSTERS_BY_NAME

    clusters = {}
    dynamic = []
    for name, cluster_config in endpoint._cluster_configs.items():
        cluster = CLUSTERS_BY_NAME[name]
        by_name = {feature.name: feature.code for feature in cluster.features}
        clusters[name] = {
            "id": cluster.id,
            # "device_type": created by esp-matter's device type add();
            # "component": created by the generated build callback.
            "source": "device_type" if cluster_config.created else "component",
            "features": sorted(
                by_name[feature]
                for feature, enabled in cluster_config.enabled_features.items()
                if enabled and feature in by_name
            ),
        }
    for cluster, attribute in endpoint._optional_attributes:
        entry = clusters.setdefault(
            cluster.name, {"id": cluster.id, "source": "component", "features": []}
        )
        entry.setdefault("optional_attributes", []).append(attribute.name)
    for device_type in endpoint._device_types:
        # Required clusters of the device type that were never touched above.
        for cluster in device_type.server_clusters:
            if cluster.required:
                clusters.setdefault(
                    cluster.name,
                    {"id": cluster.id, "source": "device_type", "features": []},
                )
        device_config = endpoint._config.get(device_type.conf_key) or {}
        for cluster_name, key in _TRAIT_DRIVEN.items():
            if isinstance(device_config, dict) and device_config.get(key) is not None:
                dynamic.append(cluster_name)
    if getattr(endpoint, "fan_control_binding", None) is not None:
        dynamic.append("FanControl")
    return {
        "device_types": [
            {"name": device_type.name, "id": device_type.id}
            for device_type in endpoint._device_types
        ],
        "clusters": clusters,
        "dynamic_features": sorted(set(dynamic)),
        # composition (conformance.EndpointPlan): parent 0 = child of the root node
        "parent": endpoint.plan.parent,
        "label": endpoint.plan.label,
        "tags": [
            {"namespace": tag.namespace, "tag": tag.tag, "label": tag.label}
            for tag in endpoint.plan.tags
        ],
    }
