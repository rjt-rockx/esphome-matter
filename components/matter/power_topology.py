"""Power Topology of the electrical_sensor device type.

The Power Topology cluster says which endpoints an Electrical Sensor measures. Exactly one
of three topologies is selected with the `topology` option of `electrical_sensor`:

``node`` (default)
    The sensor measures the entire node. Use it for a single meter.
``tree``
    The sensor measures its own endpoint and all endpoints below it in the Descriptor
    composition (`parent:`). Use it for the total of a power strip whose outlets are child
    endpoints; a sensor on a leaf endpoint measures just that endpoint.
``set``
    The sensor measures an explicit set of endpoints (`available_endpoints`). With
    `active_endpoints` the Dynamic Power Flow feature is enabled and the currently
    measured subset is advertised as well. Both lists are fixed at build time.

See docs/power.md.
"""

from __future__ import annotations

import logging

import esphome.config_validation as cv

from .const import CONF_ENDPOINTS, CONF_WITH_FEATURES
from .data_model.clusters import CLUSTERS_BY_NAME

_LOGGER = logging.getLogger(__name__)

CONF_TOPOLOGY = "topology"
CONF_AVAILABLE_ENDPOINTS = "available_endpoints"
CONF_ACTIVE_ENDPOINTS = "active_endpoints"

ELECTRICAL_SENSOR = "electrical_sensor"
POWER_TOPOLOGY_CLUSTER = "PowerTopology"

TOPOLOGY_FEATURES = {
    "node": "NodeTopology",
    "tree": "TreeTopology",
    "set": "SetTopology",
}
DYNAMIC_POWER_FLOW = "DynamicPowerFlow"
# Entries of the AvailableEndpoints / ActiveEndpoints lists.
ENDPOINT_LIST_MAX = 20

_ALL_FEATURES = {*TOPOLOGY_FEATURES.values(), DYNAMIC_POWER_FLOW}


def _endpoint_list(min_length: int = 1):
    return cv.All(
        cv.ensure_list(cv.int_range(min=0, max=65534)),
        cv.Length(min=min_length, max=ENDPOINT_LIST_MAX),
    )


def schema_options() -> dict:
    return {
        cv.Optional(CONF_TOPOLOGY, default="node"): cv.one_of(
            *TOPOLOGY_FEATURES, lower=True
        ),
        cv.Optional(CONF_AVAILABLE_ENDPOINTS): _endpoint_list(),
        # The XML has no minimum for ActiveEndpoints: an empty list means nothing is measured now.
        cv.Optional(CONF_ACTIVE_ENDPOINTS): _endpoint_list(0),
    }


def feature_names(config: dict) -> list[str]:
    """Power Topology features selected by the topology options."""
    topology = config.get(CONF_TOPOLOGY, "node")
    names = [TOPOLOGY_FEATURES[topology]]
    if topology == "set" and CONF_ACTIVE_ENDPOINTS in config:
        names.append(DYNAMIC_POWER_FLOW)
    return names


def apply_topology(config: dict) -> dict:
    """Validates the topology options of an electrical_sensor and selects the cluster features.

    Called before the generic feature handling, which would otherwise pick NodeTopology.
    """
    topology = config.get(CONF_TOPOLOGY, "node")
    manual = [
        name for name in config.get(CONF_WITH_FEATURES, ()) if name in _ALL_FEATURES
    ]
    if manual:
        hints = ", ".join(
            f"{name} -> '{CONF_TOPOLOGY}: {topology_name}'"
            for name in manual
            for topology_name, feature in TOPOLOGY_FEATURES.items()
            if feature == name
        )
        raise cv.Invalid(
            "Power Topology features are no longer selected with with_features. Use the "
            f"'{CONF_TOPOLOGY}' option instead"
            + (
                f" ({hints})"
                if hints
                else " ('topology: set' plus 'active_endpoints' enables DynamicPowerFlow)"
            ),
            path=[CONF_WITH_FEATURES],
        )
    if topology == "set":
        available = config.get(CONF_AVAILABLE_ENDPOINTS)
        if not available:
            raise cv.Invalid(
                f"'{CONF_TOPOLOGY}: set' needs '{CONF_AVAILABLE_ENDPOINTS}': the endpoints this "
                "electrical sensor can measure"
            )
        if len(set(available)) != len(available):
            raise cv.Invalid(
                f"'{CONF_AVAILABLE_ENDPOINTS}' must not contain duplicates",
                path=[CONF_AVAILABLE_ENDPOINTS],
            )
        active = config.get(CONF_ACTIVE_ENDPOINTS)
        if active is not None:
            if len(set(active)) != len(active):
                raise cv.Invalid(
                    f"'{CONF_ACTIVE_ENDPOINTS}' must not contain duplicates",
                    path=[CONF_ACTIVE_ENDPOINTS],
                )
            missing = [ep for ep in active if ep not in available]
            if missing:
                raise cv.Invalid(
                    f"'{CONF_ACTIVE_ENDPOINTS}' lists endpoints that are not in "
                    f"'{CONF_AVAILABLE_ENDPOINTS}': {', '.join(map(str, missing))}",
                    path=[CONF_ACTIVE_ENDPOINTS],
                )
    else:
        for key in (CONF_AVAILABLE_ENDPOINTS, CONF_ACTIVE_ENDPOINTS):
            if key in config:
                raise cv.Invalid(f"'{key}' requires '{CONF_TOPOLOGY}: set'", path=[key])
    config = dict(config)
    config[CONF_WITH_FEATURES] = [
        *config.get(CONF_WITH_FEATURES, ()),
        *feature_names(config),
    ]
    return config


def topology_of(device_config: dict) -> str:
    return device_config.get(CONF_TOPOLOGY, "node")


def configure(cluster_configs, device_config: dict) -> None:
    """Enables the selected features in the cluster configs of the endpoint."""
    enabled = cluster_configs[POWER_TOPOLOGY_CLUSTER].enabled_features
    for name in feature_names(device_config):
        enabled[name] = True


POWER_HEADER_INCLUDE = '#include "esphome/components/matter/matter_power.h"'


def delegate_lines(config_var: str, member: str, device_config: dict) -> list[str]:
    """The SetTopology feature serves its lists through a delegate; the others need none."""
    if topology_of(device_config) != "set":
        return []
    available = device_config[CONF_AVAILABLE_ENDPOINTS]
    active = device_config.get(CONF_ACTIVE_ENDPOINTS, available)
    return [
        (
            f"{config_var}.{member}.delegate = esphome::matter::power_topology_delegate("
            f"{{{', '.join(map(str, available))}}}, {{{', '.join(map(str, active))}}});"
        )
    ]


def needs_power_header(device_config: dict) -> bool:
    return topology_of(device_config) == "set"


def power_topology_member(device_type) -> str:
    return device_type.config_member(CLUSTERS_BY_NAME[POWER_TOPOLOGY_CLUSTER])


# --------------------------------------------------------------------------- #
# Whole configuration checks
# --------------------------------------------------------------------------- #


def validate_topologies(matter_config: dict) -> None:
    """Cross endpoint checks of the electrical sensor topologies."""
    endpoints = matter_config.get(CONF_ENDPOINTS, {})
    node_sensors = []
    for endpoint_id, config in endpoints.items():
        sensor = config.get(ELECTRICAL_SENSOR)
        if sensor is None:
            continue
        topology = topology_of(sensor)
        path = [CONF_ENDPOINTS, endpoint_id, ELECTRICAL_SENSOR]
        if topology == "node":
            node_sensors.append(endpoint_id)
        else:
            for key in (CONF_AVAILABLE_ENDPOINTS, CONF_ACTIVE_ENDPOINTS):
                for target in sensor.get(key, ()):
                    if target not in endpoints:
                        raise cv.Invalid(
                            f"'{key}' lists endpoint {target}, which is not a configured endpoint",
                            path=[*path, key],
                        )
    if len(node_sensors) > 1:
        _LOGGER.warning(
            "Matter endpoints %s all have an electrical_sensor with 'topology: node'. A node "
            "topology means the sensor measures the entire node; for sub-meters put the total on "
            "an endpoint with 'topology: tree' and give the metered endpoints 'parent:' of it, or "
            "use 'topology: set'",
            ", ".join(str(ep) for ep in node_sensors),
        )
