"""Bridge rules: Aggregator and Bridged Node endpoints (Matter device library, Aggregator / Bridged Node).

* BR-AGG-APP       an Aggregator endpoint must not carry application device types
* BR-AGG-PARENT    the Aggregator is a direct child of the root node (no nested bridges)
* BR-AGG-EMPTY     an Aggregator without Bridged Nodes
* BR-AGG-CHILD     a direct child of an Aggregator that is not a Bridged Node
* BR-NODE-NOAGG    a Bridged Node that is not below an Aggregator
* BR-NODE-PARENT   a Bridged Node whose parent is not the Aggregator itself (layout mode)
* BR-NODE-EMPTY    a Bridged Node with neither an application device type nor child endpoints
* BR-UNIQUEID      UniqueID missing, too long or shared between Bridged Nodes
* BR-NODELABEL     NodeLabel missing, too long or shared between Bridged Nodes of one Aggregator
* BR-PARTS         dump mode: the Aggregator PartsList does not list all of its descendants
"""

from __future__ import annotations

from .model import EndpointInst, NodeModel, parent_map
from .spec import Spec

AGGREGATOR = 0x000E
BRIDGED_NODE = 0x0013
BDBI = 0x39
# BDBI NodeLabel and UniqueID are limited to 32 bytes.
MAX_LENGTH = 32
ERROR, WARNING, INFO = "error", "warning", "info"


def _has(ep: EndpointInst, device_type: int) -> bool:
    return any(d == device_type for d, _ in ep.device_types)


def _application(ep: EndpointInst, spec: Spec) -> bool:
    for did, _ in ep.device_types:
        dt = spec.device_types.get(did)
        if dt is not None and dt.classification in ("simple", "dynamic") and did != AGGREGATOR:
            return True
    return False


def check_bridge(rep, model: NodeModel, spec: Spec) -> None:
    eps = model.endpoints
    aggregators = [e for e in eps.values() if e.id and _has(e, AGGREGATOR)]
    nodes = [e for e in eps.values() if e.id and _has(e, BRIDGED_NODE)]
    if not aggregators and not nodes:
        return
    parents = parent_map(model)
    layout = model.mode == "layout"

    def ancestors(ep_id: int) -> list[int]:
        out = []
        current = parents.get(ep_id, 0)
        while current and current not in out:
            out.append(current)
            current = parents.get(current, 0)
        return out

    for agg in aggregators:
        where = f"endpoint {agg.id}"
        if _application(agg, spec):
            rep.add(
                ERROR,
                "BR-AGG-APP",
                where,
                "an Aggregator endpoint must not have application device types; "
                "put them on a Bridged Node endpoint below it",
            )
        if parents.get(agg.id, 0) != 0:
            rep.add(
                ERROR,
                "BR-AGG-PARENT",
                where,
                f"the Aggregator has parent endpoint {parents[agg.id]}; it must be a direct child of the "
                "root node (nested aggregators are not supported by controllers)",
            )
        children = [c for c in eps.values() if c.id and parents.get(c.id) == agg.id]
        if not any(_has(c, BRIDGED_NODE) for c in children):
            rep.add(WARNING, "BR-AGG-EMPTY", where, "the Aggregator has no Bridged Node endpoints")
        for child in children:
            if not _has(child, BRIDGED_NODE):
                rep.add(
                    WARNING,
                    "BR-AGG-CHILD",
                    f"endpoint {child.id}",
                    f"endpoint is a direct child of Aggregator {agg.id} but is not a Bridged Node; "
                    "controllers expect Bridged Nodes there and may ignore it",
                )
        if model.mode == "dump" and agg.parts is not None:
            listed = set(agg.parts)
            descendants = {e for e in eps if e and agg.id in ancestors(e)}
            missing = sorted(descendants - listed)
            if missing:
                rep.add(
                    ERROR,
                    "BR-PARTS",
                    where,
                    f"the Aggregator PartsList does not list its descendants {missing}; an Aggregator "
                    "lists every endpoint below it (full-family pattern)",
                )

    unique_ids: dict[str, int] = {}
    labels: dict[tuple[int, str], int] = {}
    aggregator_ids = {a.id for a in aggregators}
    for node in nodes:
        where = f"endpoint {node.id}"
        chain = ancestors(node.id)
        if not any(a in aggregator_ids for a in chain):
            rep.add(
                ERROR,
                "BR-NODE-NOAGG",
                where,
                "a Bridged Node must be below an Aggregator endpoint; controllers do not find bridged "
                "devices that sit directly under the root node",
            )
        elif layout and parents.get(node.id) not in aggregator_ids:
            rep.add(
                ERROR,
                "BR-NODE-PARENT",
                where,
                f"the parent of a Bridged Node must be the Aggregator, got endpoint {parents.get(node.id)}",
            )
        if any(_has(eps[a], BRIDGED_NODE) for a in chain if a in eps):
            rep.add(ERROR, "BR-NODE-PARENT", where, "a Bridged Node cannot be nested inside another Bridged Node")
        has_children = any(parents.get(c) == node.id for c in eps if c)
        if not _application(node, spec) and not has_children:
            rep.add(
                WARNING,
                "BR-NODE-EMPTY",
                where,
                "the Bridged Node has no application device type and no child endpoints",
            )
        info = node.bridge or {}
        unique_id = info.get("unique_id")
        bdbi = node.servers.get(BDBI)
        revision = bdbi.revision if bdbi is not None else None
        if not unique_id or not isinstance(unique_id, str):
            # UniqueID became mandatory in cluster revision 4; an older device may leave it out.
            old_cluster = model.mode == "dump" and revision is not None and revision < 4
            rep.add(
                INFO if old_cluster else ERROR,
                "BR-UNIQUEID",
                where,
                "UniqueID is missing or empty; it is mandatory for Bridged Device Basic Information "
                "revision 4 and later and must never change"
                + (f" (this cluster is revision {revision})" if old_cluster else ""),
            )
        else:
            if len(unique_id.encode()) > MAX_LENGTH:
                rep.add(ERROR, "BR-UNIQUEID", where, f"UniqueID '{unique_id}' is longer than {MAX_LENGTH} bytes")
            if unique_id in unique_ids:
                rep.add(
                    ERROR,
                    "BR-UNIQUEID",
                    where,
                    f"UniqueID '{unique_id}' is also used by endpoint {unique_ids[unique_id]}; it must be unique "
                    "per bridged device",
                )
            unique_ids[unique_id] = node.id
        label = info.get("node_label")
        if isinstance(label, str) and label:
            if len(label.encode()) > MAX_LENGTH:
                rep.add(ERROR, "BR-NODELABEL", where, f"NodeLabel '{label}' is longer than {MAX_LENGTH} bytes")
            key = (parents.get(node.id, 0), label)
            if key in labels:
                rep.add(
                    WARNING,
                    "BR-NODELABEL",
                    where,
                    f"NodeLabel '{label}' is also used by endpoint {labels[key]}; controllers show two devices "
                    "with the same name",
                )
            labels[key] = node.id
        elif BDBI in node.servers:
            rep.add(
                INFO,
                "BR-NODELABEL",
                where,
                "no NodeLabel: controllers fall back to ProductName or the device type name",
            )
