"""Device types that esphome-matter cannot turn into a working Matter device yet.

The Matter data model of these device types needs an application delegate (stream management, chime sounds, EVSE
state machine, ...) that the component has no YAML layer for. `matter: allow_incomplete_device_types` is the
explicit opt-in for the ones that would fail at boot; the others only log a warning at config time. See
docs/device-types.md.
"""

from __future__ import annotations

import logging

import esphome.config_validation as cv

from .const import CONF_CLUSTERS, CONF_ENDPOINTS
from .data_model import espmatter_names
from .data_model.clusters import CLUSTERS_BY_CONF_KEY
from .data_model.device_types import DEVICE_TYPES_BY_CONF_KEY
from .ota import ota_config

_LOGGER = logging.getLogger(__name__)

CONF_ALLOW_INCOMPLETE_DEVICE_TYPES = "allow_incomplete_device_types"

# device type -> (blocks the build unless allowed, why)
INCOMPLETE_DEVICE_TYPES: dict[str, tuple[bool, str]] = {
    "chime": (
        True,
        (
            "esp-matter 1.6 (legacy data model) creates the Chime cluster only with a Chime delegate "
            "(`cluster::chime::create()` returns NULL without it), which the component does not provide, so the "
            "endpoint fails to build at boot. The mandatory ChimeStartedPlaying event is not created either"
        ),
    ),
    "camera": (
        False,
        (
            "Camera AV Stream Management, WebRTC Transport Provider and Requestor are created without a delegate, so "
            "the endpoint is structure only: no stream can be allocated and no WebRTC session can be set up"
        ),
    ),
    "evse": (
        False,
        (
            "Energy EVSE and Device Energy Management are created without a delegate, so the endpoint is "
            "structure only: EnableCharging / Disable do nothing and the attributes stay at their initial values "
            "(see docs/design/delegates-evse-dem.md)"
        ),
    ),
    "device_energy_management": (
        False,
        (
            "Device Energy Management is created without a delegate, so the endpoint is structure only: no forecast "
            "and no adjustment commands work (see docs/design/delegates-evse-dem.md)"
        ),
    ),
}


def _allow_list(value):
    return cv.ensure_list(cv.one_of(*INCOMPLETE_DEVICE_TYPES, lower=True))(value)


SCHEMA_OPTIONS = {
    cv.Optional(CONF_ALLOW_INCOMPLETE_DEVICE_TYPES, default=[]): _allow_list,
}


def validate_incomplete_device_types(matter_config: dict) -> None:
    """Final validation: reject (unless allowed) or warn about device types without a working delegate."""
    allowed = set(matter_config.get(CONF_ALLOW_INCOMPLETE_DEVICE_TYPES, ()))
    for endpoint_id, endpoint_config in matter_config.get(CONF_ENDPOINTS, {}).items():
        if "ota_requestor" in endpoint_config and ota_config(matter_config) is None:
            # esp-matter's endpoint::ota_requestor::add() returns ESP_FAIL without CONFIG_ENABLE_OTA_REQUESTOR.
            raise cv.Invalid(
                f"Matter endpoint {endpoint_id}: the ota_requestor device type needs Matter OTA, otherwise the "
                "endpoint fails to build at boot. Enable it with `matter: ota: true` (and `software_version`)"
            )
        for key in endpoint_config:
            if (
                key not in DEVICE_TYPES_BY_CONF_KEY
                or key not in INCOMPLETE_DEVICE_TYPES
            ):
                continue
            blocks, reason = INCOMPLETE_DEVICE_TYPES[key]
            if blocks and key not in allowed:
                raise cv.Invalid(
                    f"Matter endpoint {endpoint_id}: the {key} device type does not work yet: {reason}. "
                    f"Add `{key}` to `matter: {CONF_ALLOW_INCOMPLETE_DEVICE_TYPES}` to build it anyway"
                )
            _LOGGER.warning(
                "Matter endpoint %s: the %s device type is incomplete: %s",
                endpoint_id,
                key,
                reason,
            )


# esp-matter's cluster_select Kconfig declares these `depends on OPENTHREAD_ENABLED`, so without `openthread:` the
# option comes out =n and the cluster server sources are not built, while esp-matter's integration/delegate code that
# references them is still compiled: the link fails with undefined references to the cluster constructors.
OPENTHREAD_ONLY_CLUSTERS = frozenset(
    {
        "ThreadBorderRouterManagement",
        "ThreadNetworkDiagnostics",
        "ThreadNetworkDirectory",
    }
)


def validate_openthread_clusters(matter_config: dict, full_config: dict) -> None:
    """Final validation: reject Thread-only clusters on a firmware without `openthread:`."""
    if "openthread" in full_config:
        return
    for endpoint_id, endpoint_config in matter_config.get(CONF_ENDPOINTS, {}).items():
        for key in endpoint_config:
            if (device_type := DEVICE_TYPES_BY_CONF_KEY.get(key)) is not None:
                names = {cluster.name for cluster in device_type.server_clusters}
                names.update(
                    espmatter_names.device_type_implied_clusters(device_type.name)
                )
                found = names & OPENTHREAD_ONLY_CLUSTERS
                what = f"the {key} device type"
            elif key == CONF_CLUSTERS:
                found = {
                    cluster.name
                    for cluster_key in endpoint_config[key]
                    if (cluster := CLUSTERS_BY_CONF_KEY.get(cluster_key)) is not None
                } & OPENTHREAD_ONLY_CLUSTERS
                what = "a `clusters:` entry"
            else:
                continue
            if found:
                raise cv.Invalid(
                    f"Matter endpoint {endpoint_id}: {what} uses the {', '.join(sorted(found))} cluster, which "
                    "needs a Thread network. Add an `openthread:` block (it cannot be combined with `wifi:`), or "
                    "remove it from this firmware, otherwise the build fails to link"
                )
