"""Conformance rules evaluated on a :class:`~.model.NodeModel`."""

from __future__ import annotations

from dataclasses import dataclass, field

from .conformance import Context, Outcome, evaluate, norm
from .model import (
    ATTR_ACCEPTED,
    ATTR_ATTRIBUTE_LIST,
    ATTR_CLUSTER_REVISION,
    ATTR_EVENT_LIST,
    ATTR_FEATURE_MAP,
    ATTR_GENERATED,
    ROOT_DEVICE_TYPE,
    ClusterInst,
    EndpointInst,
    NodeModel,
)
from .spec import GLOBAL_ATTRIBUTES, ClusterSpec, DeviceTypeSpec, Spec

ERROR, WARNING, INFO = "error", "warning", "info"
ELECTRICAL_SENSOR = 0x0510
ELECTRICAL_MEASUREMENT = (0x0090, 0x0091)
RUNTIME_NOTE = (
    " (features of this cluster follow entity traits at runtime; confirm with a dump)"
)


@dataclass
class Finding:
    level: str
    code: str
    where: str
    message: str

    def as_dict(self) -> dict:
        return dict(
            level=self.level, code=self.code, where=self.where, message=self.message
        )


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def add(self, level, code, where, message):
        self.findings.append(Finding(level, code, where, message))

    def count(self, level: str) -> int:
        return sum(1 for f in self.findings if f.level == level)


# ----------------------------------------------------------------------------
# context construction
# ----------------------------------------------------------------------------
def _names(ids, table) -> set[str] | None:
    if ids is None:
        return None
    return {norm(table[i].name) for i in ids if i in table}


def cluster_context(cspec: ClusterSpec, inst: ClusterInst, conditions: dict) -> Context:
    attrs = _names(inst.attributes, cspec.attributes)
    if attrs is not None:
        attrs |= {
            norm(n)
            for i, n in GLOBAL_ATTRIBUTES.items()
            if i in (inst.attributes or ())
        }
    cmds = None
    if inst.accepted is not None or inst.generated is not None:
        cmds = (_names(inst.accepted or (), cspec.accepted_commands()) or set()) | (
            _names(inst.generated or (), cspec.generated_commands()) or set()
        )
    values = {}
    for aid, v in inst.values.items():
        a = cspec.attributes.get(aid)
        if a is not None:
            values[norm(a.name)] = v
    return Context(
        features=set(inst.features),
        attributes=attrs,
        commands=cmds,
        events=_names(inst.events, cspec.events),
        values=values,
        conditions=conditions,
        revision=inst.revision if inst.revision is not None else cspec.revision,
    )


def endpoint_conditions(
    ep: EndpointInst, model: NodeModel, spec: Spec
) -> dict[str, bool]:
    net = model.network
    classes = [
        spec.device_types[d].classification
        for d, _ in ep.device_types
        if d in spec.device_types
    ]
    app_server = any(
        spec.clusters[c].role == "application"
        for c in ep.servers
        if c in spec.clusters and c != 0x1D
    )
    app_client = any(
        spec.clusters[c].role == "application" for c in ep.clients if c in spec.clusters
    )
    siblings_overlap = False
    mine = {
        d
        for d, _ in ep.device_types
        if spec.device_types.get(d)
        and spec.device_types[d].classification in ("simple", "dynamic")
    }
    for other in model.endpoints.values():
        if (
            other.id == ep.id
            or other.id == 0
            or ep.id == 0
            or other.parent != ep.parent
        ):
            continue
        if mine & {d for d, _ in other.device_types}:
            siblings_overlap = True
    cond = {
        "Ethernet": bool(net.get("ethernet")),
        "Wi-Fi": bool(net.get("wifi")),
        "Thread": bool(net.get("thread")),
        "IP": True,
        "TCP": True,
        "UDP": True,
        "IPv6": True,
        "Active": True,
        "SIT": False,
        "LIT": False,
        "Node": "node" in classes,
        "App": any(c in ("simple", "dynamic") for c in classes),
        "Simple": "simple" in classes,
        "Dynamic": "dynamic" in classes,
        "Client": app_client,
        "Server": app_server,
        "Duplicate": siblings_overlap,
        "CustomNetworkConfig": False,
    }
    return cond


# ----------------------------------------------------------------------------
# generic element evaluation
# ----------------------------------------------------------------------------
def _soft(level: str, inst: ClusterInst) -> tuple[str, str]:
    """Downgrade results that depend on a runtime-derived feature set."""
    if inst.dynamic_features and level == ERROR:
        return INFO, RUNTIME_NOTE
    return level, ""


def check_cluster(
    rep: Report,
    spec: Spec,
    model: NodeModel,
    ep: EndpointInst,
    inst: ClusterInst,
    conditions: dict,
):
    cspec = spec.clusters.get(inst.id)
    where = f"endpoint {ep.id} / cluster 0x{inst.id:04X}"
    if cspec is None:
        if inst.id < 0xFFF10000 and inst.id < 0xFC00:
            rep.add(
                WARNING,
                "CL-UNKNOWN",
                where,
                "cluster is not in the Matter data model (manufacturer-specific?)",
            )
        return
    where = f"endpoint {ep.id} / {cspec.name} (0x{inst.id:04X})"
    ctx = cluster_context(cspec, inst, conditions)
    mode = model.mode
    who = (
        "esp-matter would not create" if mode == "layout" else "device does not expose"
    )
    present_word = "will expose" if mode == "layout" else "exposes"

    # ---------------------------------------------------------- revision
    if inst.revision is not None and mode == "dump":
        if inst.revision > cspec.revision:
            rep.add(
                ERROR,
                "CL-REVISION",
                where,
                f"ClusterRevision {inst.revision} is newer than the spec data model ({cspec.revision})",
            )
        elif inst.revision < cspec.revision:
            rep.add(
                INFO,
                "CL-REVISION",
                where,
                f"ClusterRevision {inst.revision} < {cspec.revision}: evaluated against the newer revision",
            )

    # ---------------------------------------------------------- features
    for bit in inst.unknown_feature_bits:
        rep.add(
            ERROR,
            "CL-FEATURE-UNDEFINED",
            where,
            f"FeatureMap bit {bit} is not defined for this cluster",
        )
    groups: dict[str, list[tuple[str, str, bool, Outcome]]] = {}

    def record(kind, name, present, out: Outcome):
        if out.kind == "O" and out.choice:
            groups.setdefault(out.choice, []).append((kind, name, present, out))

    for code, f in cspec.features.items():
        out = evaluate(f.conform, ctx)
        present = code in inst.features
        record("feature", f"{f.name}({code})", present, out)
        lvl, note = _soft(ERROR, inst)
        if out.kind == "M" and not present:
            rep.add(
                lvl,
                "CL-FEATURE-MISSING",
                where,
                f"feature {f.name} ({code}) is mandatory in this configuration but not enabled{note}",
            )
        elif out.kind == "X" and present:
            rep.add(
                lvl,
                "CL-FEATURE-DISALLOWED",
                where,
                f"feature {f.name} ({code}) is disallowed for this cluster{note}",
            )
        elif out.kind == "N" and present:
            rep.add(
                WARNING,
                "CL-FEATURE-NA",
                where,
                f"feature {f.name} ({code}) is enabled but its conformance expression is false (conflicts with the other enabled features)",
            )
        elif out.kind == "P" and present:
            rep.add(
                WARNING,
                "CL-FEATURE-PROVISIONAL",
                where,
                f"feature {f.name} ({code}) is provisional in the data model",
            )

    # ---------------------------------------------------- attributes etc.
    def check_elements(kind, table, present_ids, label):
        if present_ids is None:
            return
        lvl_err, note = _soft(ERROR, inst)
        for eid, e in table.items():
            out = evaluate(e.conform, ctx)
            pid = e.id
            present = pid in present_ids
            record(kind, e.name, present, out)
            if out.kind == "M" and not present:
                rep.add(
                    lvl_err,
                    f"CL-{label}-MISSING",
                    where,
                    f"{kind} {e.name} (0x{pid:X}) is mandatory but {who} it{note}",
                )
            elif out.kind == "X" and present:
                rep.add(
                    lvl_err,
                    f"CL-{label}-DISALLOWED",
                    where,
                    f"{kind} {e.name} (0x{pid:X}) is disallowed here but the node {present_word} it",
                )
            elif out.kind == "N" and present:
                rep.add(
                    WARNING,
                    f"CL-{label}-NA",
                    where,
                    f"{kind} {e.name} (0x{pid:X}) is present although its conformance condition is false (feature mismatch?)",
                )
            elif out.kind == "P" and present:
                rep.add(
                    INFO,
                    f"CL-{label}-PROVISIONAL",
                    where,
                    f"{kind} {e.name} (0x{pid:X}) is provisional in the data model",
                )
        known = {e.id for e in table.values()}
        for pid in sorted(present_ids):
            if kind == "attribute" and pid in GLOBAL_ATTRIBUTES:
                continue
            if pid not in known and pid < 0x10000:
                rep.add(
                    WARNING,
                    f"CL-{label}-UNDEFINED",
                    where,
                    f"{kind} 0x{pid:X} is not defined for this cluster in the data model",
                )

    check_elements("attribute", cspec.attributes, inst.attributes, "ATTR")
    check_elements("command", cspec.accepted_commands(), inst.accepted, "CMD")
    check_elements("command", cspec.generated_commands(), inst.generated, "CMD")
    check_elements("event", cspec.events, inst.events, "EVENT")
    if inst.attributes is None:
        rep.add(
            INFO,
            "CL-ATTR-UNVERIFIED",
            where,
            "attribute/command/event set not predictable for this cluster (not covered by the esp-matter profile); only features checked. Use a runtime dump for full verification",
        )

    # ------------------------------------------------------ choice groups
    for label, members in sorted(groups.items()):
        count = sum(1 for _, _, present, _ in members if present)
        ref = members[0][3]
        label_txt = ", ".join(n for _, n, _, _ in members)
        lvl, note = _soft(ERROR, inst)
        if count < ref.min:
            rep.add(
                lvl,
                "CL-CHOICE",
                where,
                f"choice group '{label}' needs at least {ref.min} of: {label_txt}{note}",
            )
        elif ref.max is not None and count > ref.max:
            rep.add(
                lvl,
                "CL-CHOICE",
                where,
                f"choice group '{label}' allows at most {ref.max} of: {label_txt}{note}",
            )
        elif not ref.more and ref.max is None and count != ref.min:
            rep.add(
                lvl,
                "CL-CHOICE",
                where,
                f"choice group '{label}' needs exactly {ref.min} of: {label_txt}{note}",
            )

    # ------------------------------------------------- dump-only: globals
    if mode == "dump":
        for gid in (
            ATTR_CLUSTER_REVISION,
            ATTR_FEATURE_MAP,
            ATTR_ATTRIBUTE_LIST,
            ATTR_ACCEPTED,
            ATTR_GENERATED,
        ):
            if gid not in inst.values:
                rep.add(
                    ERROR,
                    "CL-GLOBAL-MISSING",
                    where,
                    f"global attribute {GLOBAL_ATTRIBUTES[gid]} (0x{gid:X}) missing",
                )
        if ATTR_EVENT_LIST not in inst.values:
            rep.add(
                WARNING,
                "CL-GLOBAL-MISSING",
                where,
                "global attribute EventList (0xFFFA) missing",
            )
        if inst.attributes is not None and ATTR_ATTRIBUTE_LIST in inst.values:
            for gid in GLOBAL_ATTRIBUTES:
                if gid in inst.values and gid not in inst.attributes:
                    rep.add(
                        WARNING,
                        "CL-ATTRLIST-GLOBAL",
                        where,
                        f"AttributeList does not list global attribute {GLOBAL_ATTRIBUTES[gid]}",
                    )
            for aid in inst.values:
                if aid not in inst.attributes:
                    rep.add(
                        ERROR,
                        "CL-ATTRLIST-STALE",
                        where,
                        f"attribute 0x{aid:X} was readable but is not in AttributeList",
                    )
            for aid in inst.attributes:
                if aid not in inst.values and aid not in GLOBAL_ATTRIBUTES:
                    rep.add(
                        INFO,
                        "CL-ATTRLIST-UNREAD",
                        where,
                        f"attribute 0x{aid:X} is in AttributeList but was not in the dump",
                    )


# ----------------------------------------------------------------------------
# device type rules
# ----------------------------------------------------------------------------
def check_device_type(
    rep: Report,
    spec: Spec,
    model: NodeModel,
    ep: EndpointInst,
    dt: DeviceTypeSpec,
    conditions: dict,
    base: bool = False,
):
    where = f"endpoint {ep.id} / device type {dt.name}"
    # "current" in a device type's conformance is the device type revision the endpoint advertises
    adv_rev = next(
        (r for d, r in ep.device_types if d == dt.id and r is not None), dt.revision
    )
    for req in dt.clusters:
        side_map = ep.servers if req.side == "server" else ep.clients
        inst = side_map.get(req.id)
        present = inst is not None
        cname = f"{req.name} ({req.side})"
        cctx = Context(conditions=conditions, revision=adv_rev)
        out = evaluate(req.conform, cctx) if req.conform is not None else Outcome("?")
        if out.kind == "M" and not present:
            lvl = ERROR
            rep.add(
                lvl,
                "DT-CLUSTER-MISSING",
                where,
                f"mandatory cluster {cname} (0x{req.id:04X}) is not on the endpoint",
            )
        elif out.kind == "X" and present:
            rep.add(
                ERROR,
                "DT-CLUSTER-DISALLOWED",
                where,
                f"cluster {cname} is disallowed for this device type",
            )
        if not present or inst is None:
            continue
        cspec = spec.clusters.get(req.id)
        if (
            cspec is None
            or req.side != "server"
            and not (req.features or req.attributes)
        ):
            continue
        ctx = cluster_context(cspec, inst, conditions)
        ctx.revision = adv_rev
        lvl, note = _soft(ERROR, inst)
        for code, conf in req.features.items():
            if conf is None:
                continue
            o = evaluate(conf, ctx)
            f = cspec.features.get(code)
            fname = f"{f.name} ({code})" if f else code
            if o.kind == "M" and code not in inst.features:
                rep.add(
                    lvl,
                    "DT-FEATURE-MISSING",
                    where,
                    f"{req.name}: device type requires feature {fname}{note}",
                )
            elif o.kind == "X" and code in inst.features:
                rep.add(
                    lvl,
                    "DT-FEATURE-DISALLOWED",
                    where,
                    f"{req.name}: device type disallows feature {fname}{note}",
                )
        for kind, items, present_set, tbl in (
            ("attribute", req.attributes, inst.attributes, cspec.attributes),
            ("command", req.commands, (inst.accepted, inst.generated), None),
            ("event", req.events, inst.events, cspec.events),
        ):
            for name, conf in items:
                if conf is None:
                    continue
                o = evaluate(conf, ctx)
                n = norm(name)
                if kind == "attribute":
                    ids = (
                        None
                        if present_set is None
                        else {norm(tbl[i].name) for i in present_set if i in tbl}
                    )
                elif kind == "command":
                    acc, gen = present_set
                    if acc is None and gen is None:
                        ids = None
                    else:
                        ids = {
                            norm(cspec.accepted_commands()[i].name)
                            for i in (acc or ())
                            if i in cspec.accepted_commands()
                        } | {
                            norm(cspec.generated_commands()[i].name)
                            for i in (gen or ())
                            if i in cspec.generated_commands()
                        }
                else:
                    ids = (
                        None
                        if present_set is None
                        else {norm(tbl[i].name) for i in present_set if i in tbl}
                    )
                if ids is None:
                    continue
                if o.kind == "M" and n not in ids:
                    rep.add(
                        lvl,
                        f"DT-{kind.upper()[:4]}-MISSING",
                        where,
                        f"{req.name}: device type requires {kind} {name}{note}",
                    )
                elif o.kind == "X" and n in ids:
                    rep.add(
                        lvl,
                        f"DT-{kind.upper()[:4]}-DISALLOWED",
                        where,
                        f"{req.name}: device type disallows {kind} {name}{note}",
                    )


def check_endpoint(rep: Report, spec: Spec, model: NodeModel, ep: EndpointInst):
    where = f"endpoint {ep.id}"
    conditions = endpoint_conditions(ep, model, spec)

    if 0x1D not in ep.servers:
        rep.add(ERROR, "EP-DESCRIPTOR", where, "Descriptor cluster is missing")
    if not ep.device_types:
        rep.add(ERROR, "EP-DEVTYPES", where, "endpoint has no device type")
    seen = set()
    for did, rev in ep.device_types:
        dt = spec.device_types.get(did)
        if did in seen:
            rep.add(
                ERROR,
                "EP-DEVTYPE-DUP",
                where,
                f"device type 0x{did:04X} listed more than once",
            )
        seen.add(did)
        if dt is None:
            rep.add(
                WARNING,
                "EP-DEVTYPE-UNKNOWN",
                where,
                f"device type 0x{did:04X} is not in the data model",
            )
            continue
        if rev is not None:
            if rev > dt.revision:
                rep.add(
                    ERROR,
                    "EP-DEVTYPE-REVISION",
                    where,
                    f"{dt.name}: advertised revision {rev} is newer than the data model ({dt.revision})",
                )
            elif rev < dt.revision:
                rep.add(
                    INFO,
                    "EP-DEVTYPE-REVISION",
                    where,
                    f"{dt.name}: advertised revision {rev} < data model revision {dt.revision}",
                )
        if ep.id == 0 and dt.classification in ("simple", "dynamic"):
            rep.add(
                ERROR,
                "EP-DEVTYPE-CLASS",
                where,
                f"application device type {dt.name} on the root endpoint",
            )
        if ep.id != 0 and dt.classification == "node":
            rep.add(
                ERROR,
                "EP-DEVTYPE-CLASS",
                where,
                f"node device type {dt.name} only allowed on endpoint 0",
            )
    if ep.id == 0 and ROOT_DEVICE_TYPE not in seen:
        rep.add(
            ERROR,
            "NODE-ROOT",
            where,
            "endpoint 0 does not have the Root Node device type",
        )
    if ep.id != 0 and ROOT_DEVICE_TYPE in seen:
        rep.add(
            ERROR, "NODE-ROOT", where, "Root Node device type on a non-zero endpoint"
        )

    base = spec.base_device_type
    if base is not None:
        check_device_type(rep, spec, model, ep, base, conditions, base=True)
    for did, _ in ep.device_types:
        dt = spec.device_types.get(did)
        if dt is not None:
            check_device_type(rep, spec, model, ep, dt, conditions)
            # singleton clusters of the root node must not appear elsewhere
            if did == ROOT_DEVICE_TYPE:
                continue

    # singletons of the root device type on other endpoints
    root = spec.device_types[ROOT_DEVICE_TYPE]
    if ep.id != 0:
        for req in root.clusters:
            if req.singleton and req.side == "server" and req.id in ep.servers:
                rep.add(
                    ERROR,
                    "EP-SINGLETON",
                    where,
                    f"singleton cluster {req.name} (0x{req.id:04X}) may only exist on one endpoint (endpoint 0)",
                )

    # Electrical Sensor needs at least one measurement cluster.
    if any(d == ELECTRICAL_SENSOR for d, _ in ep.device_types):
        if not any(c in ep.servers for c in ELECTRICAL_MEASUREMENT):
            rep.add(
                ERROR,
                "EP-ELECTRICAL",
                where,
                "Electrical Sensor needs Electrical Power Measurement and/or Electrical Energy Measurement",
            )

    for cid, inst in sorted(ep.servers.items()):
        check_cluster(rep, spec, model, ep, inst, conditions)

    # dump: Descriptor consistency
    if model.mode == "dump" and 0x1D in ep.servers:
        d = ep.servers[0x1D]
        sl = d.values.get(1)
        if isinstance(sl, list):
            listed = {int(x) for x in sl}
            actual = set(ep.servers)
            if listed != actual:
                rep.add(
                    ERROR,
                    "EP-SERVERLIST",
                    where,
                    f"Descriptor.ServerList {sorted(hex(x) for x in listed)} differs from the clusters that were read {sorted(hex(x) for x in actual)}",
                )


def check_tree(rep: Report, model: NodeModel):
    ids = set(model.endpoints)
    if 0 not in ids:
        rep.add(ERROR, "NODE-ROOT", "node", "no endpoint 0")
        return
    if model.mode != "dump":
        return
    edges = {}
    for ep in model.endpoints.values():
        if ep.parts is None:
            continue
        edges[ep.id] = ep.parts
        for p in ep.parts:
            if p not in ids:
                rep.add(
                    ERROR,
                    "EP-PARTS",
                    f"endpoint {ep.id}",
                    f"PartsList references unknown endpoint {p}",
                )
            if p == ep.id:
                rep.add(
                    ERROR,
                    "EP-PARTS",
                    f"endpoint {ep.id}",
                    "PartsList contains the endpoint itself",
                )
            if p == 0:
                rep.add(
                    ERROR,
                    "EP-PARTS",
                    f"endpoint {ep.id}",
                    "PartsList contains endpoint 0",
                )
    reachable = set()
    stack = [0]
    while stack:
        e = stack.pop()
        for p in edges.get(e, []):
            if p not in reachable:
                reachable.add(p)
                stack.append(p)
    for e in sorted(ids - {0} - reachable):
        rep.add(
            ERROR,
            "EP-PARTS",
            f"endpoint {e}",
            "endpoint is not reachable from endpoint 0 through PartsList",
        )
    # a cycle shows up as an endpoint that lists one of its ancestors
    for e, parts in edges.items():
        for p in parts:
            if e in edges.get(p, []):
                rep.add(
                    ERROR,
                    "EP-PARTS",
                    f"endpoint {e}",
                    f"PartsList cycle between endpoint {e} and {p}",
                )


def check_node(model: NodeModel, spec: Spec) -> Report:
    rep = Report()
    check_tree(rep, model)
    for ep in model.endpoints.values():
        check_endpoint(rep, spec, model, ep)
    for n in model.notes:
        rep.add(INFO, "TOOL-NOTE", "model", n)
    rep.stats = dict(
        endpoints=len(model.endpoints),
        clusters=sum(len(e.servers) for e in model.endpoints.values()),
        mode=model.mode,
        spec=spec.version,
    )
    return rep
