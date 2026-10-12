"""Controller compatibility hints (not spec requirements).

Optional-but-useful elements that Home Assistant / Apple Home read, taken from
research/r2-ha-matter-internals.md and r2-apple-home.md. All findings are
warnings with ``COMPAT-`` codes, so they never fail the run unless ``--strict``.
"""

from __future__ import annotations

from .checks import WARNING, Report
from .conformance import norm
from .model import NodeModel
from .spec import Spec

BASIC_INFORMATION = 0x28
OTA_REQUESTOR = 0x2A
WINDOW_COVERING = 0x102
THERMOSTAT_DTS = {0x0301, 0x0072}


def _attr_names(spec: Spec, cid: int, ids) -> set[str] | None:
    cspec = spec.clusters.get(cid)
    if cspec is None or ids is None:
        return None
    return {norm(cspec.attributes[i].name) for i in ids if i in cspec.attributes}


def check_compat(model: NodeModel, spec: Spec, rep: Report) -> None:
    root = model.endpoints.get(0)
    if root is not None:
        bi = root.servers.get(BASIC_INFORMATION)
        names = _attr_names(spec, BASIC_INFORMATION, bi.attributes if bi else None)
        if names is not None:
            for want, why in (
                ("UniqueID", "controllers use it as the stable node identity"),
                ("SerialNumber", "shown by Home Assistant and Apple Home"),
                ("NodeLabel", "user visible name"),
                ("SoftwareVersionString", "needed for Home Assistant's update entity"),
            ):
                if norm(want) not in names:
                    rep.add(
                        WARNING,
                        "COMPAT-BASIC",
                        "endpoint 0 / Basic Information",
                        f"{want} not exposed: {why}",
                    )
        if OTA_REQUESTOR not in root.servers:
            rep.add(
                WARNING,
                "COMPAT-OTA",
                "endpoint 0",
                "no OTA Software Update Requestor cluster: Home Assistant will not create an update entity",
            )
    for ep in model.endpoints.values():
        for did, _ in ep.device_types:
            where = f"endpoint {ep.id}"
            if did in THERMOSTAT_DTS:
                th = ep.servers.get(0x201)
                if (
                    th is not None
                    and not ({"HEAT", "COOL"} & th.features)
                    and not th.dynamic_features
                ):
                    rep.add(
                        WARNING,
                        "COMPAT-THERMOSTAT",
                        where,
                        "Thermostat has neither Heating nor Cooling: Home Assistant creates no climate modes",
                    )
            if did == 0x0202:  # Window Covering device type
                wc = ep.servers.get(WINDOW_COVERING)
                if (
                    wc is not None
                    and not wc.dynamic_features
                    and not ({"PA_LF", "PA_TL"} & wc.features)
                ):
                    rep.add(
                        WARNING,
                        "COMPAT-COVER",
                        where,
                        "Window Covering without PositionAwareLift/Tilt: Home Assistant only offers open/close/stop",
                    )
