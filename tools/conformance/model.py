"""Normalised node model that the conformance rules run on.

Two sources feed it:

* :func:`from_layout` - the codegen layout (``MATTER_LAYOUT_JSON``) combined
  with the esp-matter creation profile (``espm.py``): *predicted* data model.
* :func:`from_dump` - a runtime wildcard read: *observed* data model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .conformance import norm
from .espm import Profile
from .spec import GLOBAL_ATTRIBUTES, ClusterSpec, Spec

ROOT_DEVICE_TYPE = 0x0016
ATTR_FEATURE_MAP = 0xFFFC
ATTR_ATTRIBUTE_LIST = 0xFFFB
ATTR_ACCEPTED = 0xFFF9
ATTR_GENERATED = 0xFFF8
ATTR_EVENT_LIST = 0xFFFA
ATTR_CLUSTER_REVISION = 0xFFFD
DESCRIPTOR = 0x1D


@dataclass
class ClusterInst:
    id: int
    side: str = "server"
    features: set[str] = field(default_factory=set)  # feature codes
    # None = not known (static mode, cluster not covered by the esp-matter profile)
    attributes: set[int] | None = None
    accepted: set[int] | None = None
    generated: set[int] | None = None
    events: set[int] | None = None
    values: dict[int, Any] = field(default_factory=dict)
    revision: int | None = None
    # features come from entity traits at runtime; static results are approximate
    dynamic_features: bool = False
    # feature map bits the spec does not define (dump mode)
    unknown_feature_bits: list[int] = field(default_factory=list)


@dataclass
class EndpointInst:
    id: int
    device_types: list[tuple[int, int | None]] = field(
        default_factory=list
    )  # (id, revision)
    servers: dict[int, ClusterInst] = field(default_factory=dict)
    clients: dict[int, ClusterInst] = field(default_factory=dict)
    parts: list[int] | None = None  # Descriptor.PartsList (dump mode)
    parent: int | None = None  # None = flat (child of the root node)
    tag_list_known: bool = False


@dataclass
class NodeModel:
    mode: str  # "layout" | "dump"
    endpoints: dict[int, EndpointInst] = field(default_factory=dict)
    network: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)  # tool limitations / unmapped names


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def _to_int(key) -> int:
    if isinstance(key, int):
        return key
    return int(str(key), 0)


def _names_to_ids(
    names,
    table: dict[int, Any],
    notes: list[str],
    cluster: str,
    kind: str,
    strict: bool = True,
) -> set[int]:
    by_name = {}
    for k, e in table.items():
        by_name.setdefault(norm(e.name), e.id)
    prefix = norm(cluster)
    out = set()
    for name in names:
        n = norm(name)
        if n not in by_name and n.startswith(prefix) and n[len(prefix) :] in by_name:
            n = n[
                len(prefix) :
            ]  # esp-matter says color_control_options for ColorControl.Options
        if n in by_name:
            out.add(by_name[n])
        elif strict:
            msg = f"esp-matter creates {kind} '{name}' on {cluster} which the spec data model does not define"
            if msg not in notes:
                notes.append(msg)
    return out


def _cluster_for_ns(spec: Spec, profile: Profile, ns: str) -> ClusterSpec | None:
    for cid, c in spec.clusters.items():
        if profile.namespace_for(c.name) == ns:
            return c
    return None


def _feature_codes(
    cspec: ClusterSpec, profile: Profile, names, notes: list[str]
) -> set[str]:
    out = set()
    for n in names:
        f = cspec.feature_by_name(n) or next(
            (
                x
                for x in cspec.features.values()
                if profile._fnorm(x.name) == profile._fnorm(n)
            ),
            None,
        )
        if f is None:
            msg = f"esp-matter feature '{n}' of {cspec.name} has no spec counterpart"
            if msg not in notes:
                notes.append(msg)
        else:
            out.add(f.code)
    return out


# ----------------------------------------------------------------------------
# layout (static) mode
# ----------------------------------------------------------------------------
def _predict_cluster(
    spec: Spec,
    profile: Profile,
    cspec: ClusterSpec,
    side: str,
    sdk_features: list[str],
    layout_codes: set[str],
    extra_attr_names: list[str],
    extra: dict,
    notes: list[str],
    dynamic: bool,
) -> ClusterInst:
    inst = ClusterInst(cspec.id, side, dynamic_features=dynamic)
    inst.features = _feature_codes(cspec, profile, sdk_features, notes) | set(
        layout_codes
    )
    inst.revision = profile.revision(cspec.name) or cspec.revision
    if side == "client":
        return inst
    names = [cspec.features[c].name for c in inst.features if c in cspec.features]
    predicted = profile.predict(cspec.name, names)
    if predicted is None:
        return inst  # attribute level unknown
    attrs, cmds, events = predicted
    attrs = (
        set(attrs)
        | {norm(a) for a in extra_attr_names}
        | {norm(a) for a in extra.get("attrs", [])}
    )
    cmds = set(cmds) | {norm(a) for a in extra.get("cmds", [])}
    events = set(events) | {norm(a) for a in extra.get("events", [])}
    # esp-matter's name for an N-primaries style attribute; the real set depends on a value.
    attrs = {a for a in attrs if not a.startswith("primaryn")}
    nid = f"{cspec.name}"
    inst.attributes = _names_to_ids(
        attrs, cspec.attributes, notes, nid, "attribute"
    ) | set(GLOBAL_ATTRIBUTES)
    acc_names = {e.key for e in cspec.accepted_commands().values()}
    gen_names = {e.key for e in cspec.generated_commands().values()}
    accepted = _names_to_ids(
        [c for c in cmds if c in acc_names],
        cspec.accepted_commands(),
        notes,
        nid,
        "command",
    )
    generated = _names_to_ids(
        [c for c in cmds if c in gen_names],
        cspec.generated_commands(),
        notes,
        nid,
        "command",
    )
    unmatched = [c for c in cmds if c not in acc_names and c not in gen_names]
    for c in unmatched:
        msg = f"esp-matter creates command '{c}' on {cspec.name} which the spec data model does not define"
        if msg not in notes:
            notes.append(msg)
    inst.accepted, inst.generated = accepted, generated
    inst.events = _names_to_ids(events, cspec.events, notes, nid, "event")
    return inst


def from_layout(
    layout: dict | str | Path, spec: Spec, profile: Profile | None = None
) -> NodeModel:
    if not isinstance(layout, dict):
        layout = json.loads(Path(layout).read_text())
    profile = profile or Profile()
    notes: list[str] = []
    net = layout.get("network", {})
    model = NodeModel("layout", network=dict(net), notes=notes)
    dts = profile.data["device_types"]

    def build_endpoint(
        ep_id: int, dt_names: list[str], cluster_layout: dict, dynamic: set[str]
    ):
        ep = EndpointInst(ep_id)
        # Descriptor is added by esp-matter's endpoint::common::create() for every endpoint.
        sdk_clusters: dict[tuple[int, str], dict] = {}
        layout_by_id = {c["id"]: (n, c) for n, c in cluster_layout.items()}

        def add_sdk(entry: dict):
            for side in entry["sides"]:
                cspec = _cluster_for_ns(spec, profile, entry["ns"])
                if cspec is None:
                    msg = f"esp-matter cluster namespace '{entry['ns']}' has no spec counterpart"
                    if msg not in notes:
                        notes.append(msg)
                    continue
                key = (cspec.id, side)
                cur = sdk_clusters.setdefault(
                    key, dict(features=[], attrs=[], cmds=[], events=[], cspec=cspec)
                )
                for k in ("features", "attrs", "cmds", "events"):
                    cur[k] = cur[k] + [x for x in entry[k] if x not in cur[k]]

        add_sdk(
            dict(
                ns="descriptor",
                sides=["server"],
                features=[],
                attrs=[],
                cmds=[],
                events=[],
            )
        )
        for name in dt_names:
            info = dts.get(name)
            if info is None:
                notes.append(
                    f"device type '{name}' is not known to the esp-matter profile"
                )
                continue
            ep.device_types.append((info["id"], info["version"]))
            for entry in info["clusters"]:
                cond = entry.get("cond")
                if cond:
                    ns = entry["ns"]
                    if "CHIP_DEVICE_CONFIG_ENABLE_WIFI" in cond and not net.get("wifi"):
                        continue
                    if "CHIP_DEVICE_CONFIG_ENABLE_THREAD" in cond and not net.get(
                        "thread"
                    ):
                        continue
                    if "optional_clusters_mask" in cond or "config->" in cond:
                        # created only if the component asks for it (layout lists it)
                        cspec = _cluster_for_ns(spec, profile, ns)
                        if cspec is None or cspec.id not in layout_by_id:
                            continue
                    elif "ifndef CONFIG_CUSTOM_NETWORK_CONFIG" in cond:
                        pass
                    elif (
                        "CHIP_DEVICE_CONFIG_ENABLE_WIFI" in cond
                        or "CHIP_DEVICE_CONFIG_ENABLE_THREAD" in cond
                    ):
                        pass  # radio present (checked above); the component enables the diagnostics clusters
                    else:
                        continue  # ICD, groupcast ... not enabled by this component
                add_sdk(entry)

        # clusters the component creates itself
        for name, c in cluster_layout.items():
            if c["source"] == "component":
                cspec = spec.clusters.get(c["id"])
                if cspec is None:
                    continue
                key = (cspec.id, "server")
                sdk_clusters.setdefault(
                    key, dict(features=[], attrs=[], cmds=[], events=[], cspec=cspec)
                )

        if ep_id == 0 and (0x31, "server") in sdk_clusters:
            # esp_matter node config: feature_map of the compiled-in network interfaces
            # (WIFI wins over THREAD wins over ETHERNET, see esp_matter_cluster_impl.h)
            feature = (
                "WiFiNetworkInterface"
                if net.get("wifi")
                else "ThreadNetworkInterface"
                if net.get("thread")
                else "EthernetNetworkInterface"
            )
            sdk_clusters[(0x31, "server")]["features"].append(feature)
        for (cid, side), cur in sdk_clusters.items():
            cspec = cur["cspec"]
            lname, lc = layout_by_id.get(cid, (None, {}))
            inst = _predict_cluster(
                spec,
                profile,
                cspec,
                side,
                cur["features"],
                set(lc.get("features", [])) if side == "server" else set(),
                lc.get("optional_attributes", []) if side == "server" else [],
                cur,
                notes,
                lname in dynamic,
            )
            (ep.servers if side == "server" else ep.clients)[cid] = inst
        # Layout says the component relies on a device type cluster that the SDK does not create.
        for name, c in cluster_layout.items():
            if c["source"] == "device_type" and c["id"] not in ep.servers:
                notes.append(
                    f"endpoint {ep_id}: component expects device type cluster {name} but the esp-matter "
                    "device type add() does not create it (and the component does not create it)"
                )
        return ep

    # Root node (endpoint 0) is created by esp_matter::node::create().
    root = build_endpoint(0, ["root_node"], {}, set())
    # esp-matter's root also hosts the transport diagnostics for the enabled radios.
    model.endpoints[0] = root
    for key, e in sorted(layout["endpoints"].items(), key=lambda kv: int(kv[0])):
        ep_id = int(key)
        names = [d["name"] for d in e["device_types"]]
        model.endpoints[ep_id] = build_endpoint(
            ep_id, names, e["clusters"], set(e.get("dynamic_features", []))
        )
    return model


# ----------------------------------------------------------------------------
# runtime dump mode
# ----------------------------------------------------------------------------
def _as_dt(entry) -> tuple[int, int | None]:
    if isinstance(entry, dict):
        for a, b in (
            ("deviceType", "revision"),
            ("device_type", "revision"),
            ("0", "1"),
            (0, 1),
        ):
            if a in entry:
                return _to_int(entry[a]), (
                    _to_int(entry[b]) if b in entry and entry[b] is not None else None
                )
    if isinstance(entry, (list, tuple)) and entry:
        return _to_int(entry[0]), (_to_int(entry[1]) if len(entry) > 1 else None)
    return _to_int(entry), None


def from_dump(dump: dict | str | Path, spec: Spec) -> NodeModel:
    if not isinstance(dump, dict):
        dump = json.loads(Path(dump).read_text())
    if "endpoints" in dump and isinstance(dump["endpoints"], dict):
        dump = dump["endpoints"]
    if dump and all("/" in str(k) for k in dump):
        # python-matter-server style flat map: {"<endpoint>/<cluster>/<attribute>": value}
        nested: dict = {}
        for path, value in dump.items():
            ep_id, cluster_id, attr_id = str(path).split("/")
            nested.setdefault(ep_id, {}).setdefault(cluster_id, {})[attr_id] = value
        dump = nested
    notes: list[str] = []
    model = NodeModel("dump", notes=notes)
    for ep_key, clusters in dump.items():
        ep = EndpointInst(_to_int(ep_key))
        for ckey, attrs in clusters.items():
            cid = _to_int(ckey)
            attrs = {_to_int(k): v for k, v in attrs.items()}
            inst = ClusterInst(cid)
            inst.values = attrs
            fm = attrs.get(ATTR_FEATURE_MAP)
            cspec = spec.clusters.get(cid)
            if isinstance(fm, int):
                for bit in range(32):
                    if fm & (1 << bit):
                        f = cspec.feature_by_bit(bit) if cspec else None
                        if f:
                            inst.features.add(f.code)
                        else:
                            inst.unknown_feature_bits.append(bit)
            if ATTR_ATTRIBUTE_LIST in attrs:
                inst.attributes = {_to_int(a) for a in attrs[ATTR_ATTRIBUTE_LIST]}
            else:
                inst.attributes = set(attrs)
            if ATTR_ACCEPTED in attrs:
                inst.accepted = {_to_int(a) for a in attrs[ATTR_ACCEPTED]}
            if ATTR_GENERATED in attrs:
                inst.generated = {_to_int(a) for a in attrs[ATTR_GENERATED]}
            if ATTR_EVENT_LIST in attrs:
                inst.events = {_to_int(a) for a in attrs[ATTR_EVENT_LIST]}
            if isinstance(attrs.get(ATTR_CLUSTER_REVISION), int):
                inst.revision = attrs[ATTR_CLUSTER_REVISION]
            ep.servers[cid] = inst
            if cid == DESCRIPTOR:
                ep.device_types = [_as_dt(d) for d in (attrs.get(0) or [])]
                ep.parts = [_to_int(p) for p in (attrs.get(3) or [])]
                for c in attrs.get(2) or []:
                    ep.clients[_to_int(c)] = ClusterInst(_to_int(c), "client")
        model.endpoints[ep.id] = ep
    # radios present on the root node -> network capabilities
    root = model.endpoints.get(0)
    if root:
        model.network = {
            "wifi": 0x36 in root.servers,
            "thread": 0x35 in root.servers,
            "ethernet": 0x37 in root.servers,
        }
    return model


def to_dump(model: NodeModel, spec: Spec) -> dict:
    """Render a predicted model as a runtime-style wildcard-read dump.

    Handy for tests and for diffing predictions against a real capture. Attribute
    values other than the global ones are placeholders (0).
    """
    out: dict[str, dict] = {}
    for ep_id, ep in sorted(model.endpoints.items()):
        clusters = {}
        for cid, inst in sorted(ep.servers.items()):
            cspec = spec.clusters.get(cid)
            attrs: dict[str, Any] = {}
            for aid in sorted(inst.attributes or ()):
                attrs[str(aid)] = 0
            fm = 0
            for code in inst.features:
                if cspec and code in cspec.features:
                    fm |= 1 << cspec.features[code].bit
            attrs[str(ATTR_FEATURE_MAP)] = fm
            attrs[str(ATTR_CLUSTER_REVISION)] = inst.revision or (
                cspec.revision if cspec else 1
            )
            attrs[str(ATTR_ATTRIBUTE_LIST)] = sorted(
                set(inst.attributes or ()) | set(GLOBAL_ATTRIBUTES)
            )
            attrs[str(ATTR_ACCEPTED)] = sorted(inst.accepted or ())
            attrs[str(ATTR_GENERATED)] = sorted(inst.generated or ())
            attrs[str(ATTR_EVENT_LIST)] = sorted(inst.events or ())
            if cid == DESCRIPTOR:
                attrs["0"] = [
                    {"deviceType": d, "revision": r} for d, r in ep.device_types
                ]
                attrs["1"] = sorted(ep.servers)
                attrs["2"] = sorted(ep.clients)
                attrs["3"] = (
                    [e for e in sorted(model.endpoints) if e != 0] if ep_id == 0 else []
                )
            clusters[str(cid)] = attrs
        out[str(ep_id)] = clusters
    return out
