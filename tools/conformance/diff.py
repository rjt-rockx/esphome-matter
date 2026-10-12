"""Structural diff of two Matter node models (``diff``) and a batch mode (``gallery``).

``python -m tools.conformance diff <ours> <reference>``

* ``ours``: ESPHome YAML (layout generated through ``esphome``), a
  ``MATTER_LAYOUT_JSON`` layout, or a runtime dump (python-matter-server / Home
  Assistant node dump, matter.js harness dump, generic ``{ep: {cluster: {attr: v}}}``).
* ``reference``: a dump / layout / YAML file, or the id of a device in the research
  catalog (``$MATTER_DEVICE_CATALOG`` or ``/home/user/research/devices/*.jsonl``); the
  entry's ``local_dump`` is used, or, without one, the endpoint summary of the entry.

Endpoints are matched by device types and order, never by number. Output is a human
readable table, optionally JSON (``--json``).

``python -m tools.conformance gallery <dir>`` runs the diff for every YAML below
``<dir>`` whose header contains ``# mirrors: <catalog-id>`` and prints a summary matrix.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .espm import Profile
from .inputs import load_model
from .model import (
    DESCRIPTOR,
    ROOT_DEVICE_TYPE,
    ClusterInst,
    EndpointInst,
    NodeModel,
    parent_map,
)
from .spec import GLOBAL_ATTRIBUTES, DEFAULT_VERSION, Spec

DEFAULT_CATALOG_DIR = Path("/home/user/research/devices")
CATALOG_ENV = "MATTER_DEVICE_CATALOG"

# gap classes in display order: (class key, short column title)
CLASSES = [
    ("endpoint", "EP"),
    ("device_type", "DT"),
    ("cluster", "CL"),
    ("feature", "FEAT"),
    ("attribute", "ATTR"),
    ("command", "CMD"),
    ("event", "EVT"),
    ("composition", "COMP"),
]
CLASS_OF_KIND = {
    "endpoint": "endpoint",
    "device_type": "device_type",
    "server": "cluster",
    "client": "cluster",
    "revision": "cluster",
    "feature": "feature",
    "attribute": "attribute",
    "accepted": "command",
    "generated": "command",
    "event": "event",
    "tag": "composition",
    "label": "composition",
    "parent": "composition",
}

_CLUSTER_KINDS = {
    "server",
    "client",
    "revision",
    "feature",
    "attribute",
    "accepted",
    "generated",
    "event",
}

# diagnostics clusters whose content is runtime state, not structure
_DIAGNOSTICS = {0x33, 0x34, 0x35, 0x36, 0x37}


# ----------------------------------------------------------------------------
# catalog
# ----------------------------------------------------------------------------
def catalog_dir() -> Path:
    return Path(os.environ.get(CATALOG_ENV) or DEFAULT_CATALOG_DIR)


def load_catalog(directory: Path | None = None) -> dict[str, dict]:
    """``id -> entry`` over every ``*.jsonl`` (the first entry wins, a dump beats a doc)."""
    directory = directory or catalog_dir()
    out: dict[str, dict] = {}
    for file in sorted(directory.glob("*.jsonl")):
        for line in file.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            cid = entry.get("id")
            if not cid:
                continue
            old = out.get(cid)
            if old is None or (
                "local_dump" not in old and "local_dump" in entry
            ):  # prefer a real dump
                out[cid] = entry
    return out


def _hex(value) -> int:
    return value if isinstance(value, int) else int(str(value), 0)


def model_from_catalog_entry(entry: dict, spec: Spec) -> NodeModel:
    """Endpoint summary of a catalog entry (no attribute lists, free-text tags ignored)."""
    model = NodeModel("dump", notes=["built from the catalog summary, not a dump"])
    for e in entry.get("endpoints", []):
        ep = EndpointInst(int(e["id"]))
        ep.device_types = [
            (_hex(d[0]), d[2] if len(d) > 2 and d[2] is not None else None)
            for d in e.get("device_types", [])
        ]
        for c in e.get("server_clusters", []):
            cid = _hex(c[0])
            meta = c[2] if len(c) > 2 and isinstance(c[2], dict) else {}
            inst = ClusterInst(cid)
            cspec = spec.clusters.get(cid)
            for code in meta.get("features", []):
                f = cspec.feature_by_name(code) if cspec else None
                inst.features.add(f.code if f else code)
            inst.revision = meta.get("cluster_revision")
            ep.servers[cid] = inst
        for c in e.get("client_clusters", []):
            ep.clients[_hex(c[0])] = ClusterInst(_hex(c[0]), "client")
        if e.get("parent") is not None:
            ep.declared_parent = int(e["parent"])
        elif e.get("parts_list") is not None:
            ep.parts = list(e["parts_list"])
        model.endpoints[ep.id] = ep
    return model


def resolve_reference(
    arg: str, spec: Spec, profile: Profile
) -> tuple[NodeModel, str, str]:
    """Return ``(model, label, kind)`` for a file path or a catalog id."""
    path = Path(arg)
    if path.exists():
        model, kind = load_model(path, spec, profile)
        return model, str(path), kind
    catalog = load_catalog()
    entry = catalog.get(arg)
    if entry is None:
        close = difflib.get_close_matches(arg, list(catalog), n=5)
        hint = f" (similar ids: {', '.join(close)})" if close else ""
        raise FileNotFoundError(
            f"'{arg}' is neither a file nor a catalog id in {catalog_dir()}{hint}"
        )
    dump = entry.get("local_dump")
    if dump and Path(dump).exists():
        model, kind = load_model(Path(dump), spec, profile)
        return model, f"{arg} ({dump})", "catalog-dump"
    return model_from_catalog_entry(entry, spec), f"{arg} (catalog summary)", "catalog"


# ----------------------------------------------------------------------------
# facts
# ----------------------------------------------------------------------------
def _is_vendor(kind: str, key: tuple) -> bool:
    cid = key[0] if key else 0
    if kind in ("server", "client"):
        return cid > 0xFFFF or 0xFC00 <= cid <= 0xFFFE
    if cid > 0xFFFF or 0xFC00 <= cid <= 0xFFFE:
        return True
    if kind in ("attribute", "accepted", "generated", "event") and len(key) > 1:
        return key[1] >= 0x1_0000  # manufacturer prefix / manufacturer range
    return False


def parents(model: NodeModel) -> dict[int, int]:
    """Endpoint -> parent endpoint (0 = child of the root node)."""
    return parent_map(model)


def _cluster_label(spec: Spec, cid: int) -> str:
    return f"{spec.cluster_name(cid)} (0x{cid:04X})"


def _device_label(spec: Spec, did: int) -> str:
    d = spec.device_types.get(did)
    return f"{d.name} (0x{did:04X})" if d else f"0x{did:04X}"


def _elem_name(table: dict, eid: int) -> str:
    e = table.get(eid)
    return f"{e.name} (0x{eid:X})" if e else f"0x{eid:X}"


def endpoint_facts(
    spec: Spec, ep: EndpointInst, parent_value: str | None
) -> dict[tuple, Any]:
    """Everything comparable about one endpoint as ``{(kind, ...): value}``.

    Element-level facts (attributes, commands, events) are omitted for clusters
    whose lists are unknown on this side; the caller drops them from the other
    side as well so that unknown never counts as different.
    """
    facts: dict[tuple, Any] = {}
    for did, rev in ep.device_types:
        facts[("device_type", did)] = rev
    for cid, inst in ep.servers.items():
        facts[("server", cid)] = True
        if inst.revision is not None:
            facts[("revision", cid)] = inst.revision
        for code in inst.features:
            facts[("feature", cid, code)] = True
        for kind, ids in (
            ("attribute", inst.attributes),
            ("accepted", inst.accepted),
            ("generated", inst.generated),
            ("event", inst.events),
        ):
            if ids is None:
                continue
            for aid in ids:
                if kind == "attribute" and aid in GLOBAL_ATTRIBUTES:
                    continue
                facts[(kind, cid, aid)] = True
    for cid in ep.clients:
        facts[("client", cid)] = True
    for ns, tag, label in ep.tags:
        facts[("tag", ns, tag)] = label
    for label, value in ep.labels:
        facts[("label", label)] = value
    if parent_value is not None:
        facts[("parent",)] = parent_value
    return facts


def _known_lists(ep: EndpointInst) -> dict[tuple[int, str], bool]:
    out = {}
    for cid, inst in ep.servers.items():
        out[(cid, "attribute")] = inst.attributes is not None
        out[(cid, "accepted")] = inst.accepted is not None
        out[(cid, "generated")] = inst.generated is not None
        out[(cid, "event")] = inst.events is not None
    return out


# ----------------------------------------------------------------------------
# endpoint matching
# ----------------------------------------------------------------------------
def _dt_ids(ep: EndpointInst) -> frozenset[int]:
    return frozenset(d for d, _ in ep.device_types)


def _jaccard(a, b) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 1.0


def match_endpoints(
    ours: list[EndpointInst], ref: list[EndpointInst]
) -> tuple[
    list[tuple[EndpointInst, EndpointInst]], list[EndpointInst], list[EndpointInst]
]:
    """Pair endpoints by device types and order. Returns (pairs, ours-only, ref-only)."""
    pairs: list[tuple[EndpointInst, EndpointInst]] = []
    ours = sorted(ours, key=lambda e: e.id)
    ref = sorted(ref, key=lambda e: e.id)
    # pass 1: identical device type sets, k-th with k-th in endpoint order
    by_key: dict[frozenset, list[EndpointInst]] = {}
    for e in ref:
        by_key.setdefault(_dt_ids(e), []).append(e)
    left_ours = []
    used_ref: set[int] = set()
    for e in ours:
        queue = by_key.get(_dt_ids(e))
        if queue:
            r = queue.pop(0)
            pairs.append((e, r))
            used_ref.add(r.id)
        else:
            left_ours.append(e)
    left_ref = [e for e in ref if e.id not in used_ref]
    # pass 2: best overlap of device types, then of server clusters
    while left_ours and left_ref:
        best = None
        for i, e in enumerate(left_ours):
            for j, r in enumerate(left_ref):
                dt = _jaccard(_dt_ids(e), _dt_ids(r))
                if dt == 0 and _dt_ids(e) and _dt_ids(r):
                    continue
                score = (dt, _jaccard(e.servers, r.servers), -abs(e.id - r.id))
                if best is None or score > best[0]:
                    best = (score, i, j)
        if best is None:
            break
        _, i, j = best
        pairs.append((left_ours.pop(i), left_ref.pop(j)))
    pairs.sort(key=lambda p: p[1].id)
    return pairs, left_ours, left_ref


# ----------------------------------------------------------------------------
# diff
# ----------------------------------------------------------------------------
@dataclass
class Difference:
    cls: str  # CLASSES key
    kind: str  # fine-grained kind (server, attribute, tag, ...)
    change: str  # missing (only in reference) | extra (only in ours) | changed
    endpoint: str  # human endpoint pairing
    item: str
    ours: str | None
    reference: str | None
    ours_ep: int | None = None
    reference_ep: int | None = None

    def as_dict(self) -> dict:
        return {
            "class": self.cls,
            "kind": self.kind,
            "change": self.change,
            "endpoint": self.endpoint,
            "ours_endpoint": self.ours_ep,
            "reference_endpoint": self.reference_ep,
            "item": self.item,
            "ours": self.ours,
            "reference": self.reference,
        }


@dataclass
class DiffResult:
    ours_label: str
    reference_label: str
    ours_mode: str
    reference_mode: str
    score: float
    pairs: list[dict]
    differences: list[Difference]
    notes: list[str] = field(default_factory=list)
    ignored: dict[str, int] = field(default_factory=dict)

    @property
    def gaps(self) -> list[Difference]:
        """Things the reference has that ours lacks or has differently."""
        return [d for d in self.differences if d.change in ("missing", "changed")]

    @property
    def extras(self) -> list[Difference]:
        return [d for d in self.differences if d.change == "extra"]

    def gap_counts(self) -> dict[str, int]:
        c = Counter(d.cls for d in self.gaps)
        return {k: c.get(k, 0) for k, _ in CLASSES}

    def extra_counts(self) -> dict[str, int]:
        c = Counter(d.cls for d in self.extras)
        return {k: c.get(k, 0) for k, _ in CLASSES}

    def as_dict(self) -> dict:
        return {
            "ours": {"input": self.ours_label, "mode": self.ours_mode},
            "reference": {"input": self.reference_label, "mode": self.reference_mode},
            "score": round(self.score, 4),
            "endpoint_pairs": self.pairs,
            "summary": {
                "gaps": self.gap_counts(),
                "extras": self.extra_counts(),
                "total_gaps": len(self.gaps),
                "total_extras": len(self.extras),
            },
            "ignored": self.ignored,
            "notes": self.notes,
            "differences": [d.as_dict() for d in self.differences],
        }


def _dt_text(spec: Spec, ep: EndpointInst) -> str:
    return (
        ", ".join(
            _device_label(spec, d) + (f" r{r}" if r is not None else "")
            for d, r in ep.device_types
        )
        or "-"
    )


def _fmt_value(kind: str, key: tuple, value, spec: Spec) -> str | None:
    if kind == "device_type":
        return "present" + (f" r{value}" if value is not None else "")
    if kind == "tag":
        return f"ns {key[1]} tag {key[2]}" + (f" '{value}'" if value else "")
    if kind == "label":
        return f"{key[1]}={value!r}"
    if kind == "parent":
        return str(value)
    if kind == "revision":
        return f"rev {value}"
    return "present"


def _item_name(kind: str, key: tuple, spec: Spec) -> str:
    if kind == "tag":
        return "Descriptor tag"
    if kind == "label":
        return f"FixedLabel '{key[0]}'"
    if kind == "parent":
        return "parent endpoint"
    cid = key[0] if key else 0
    if kind == "device_type":
        return f"device type {_device_label(spec, key[0])}"
    if kind in ("server", "client"):
        return f"{kind} cluster {_cluster_label(spec, cid)}"
    if kind == "revision":
        return f"{_cluster_label(spec, cid)} ClusterRevision"
    cspec = spec.clusters.get(cid)
    cname = spec.cluster_name(cid)
    if kind == "feature":
        f = cspec.features.get(key[1]) if cspec else None
        return f"{cname} feature {f.name if f else key[1]} ({key[1]})"
    if kind == "attribute":
        return (
            f"{cname} attribute {_elem_name(cspec.attributes if cspec else {}, key[1])}"
        )
    if kind == "accepted":
        return f"{cname} accepted command {_elem_name(cspec.accepted_commands() if cspec else {}, key[1])}"
    if kind == "generated":
        return f"{cname} generated command {_elem_name(cspec.generated_commands() if cspec else {}, key[1])}"
    if kind == "event":
        return f"{cname} event {_elem_name(cspec.events if cspec else {}, key[1])}"
    return str(key)


def _owner_cluster(kind: str, key: tuple) -> int | None:
    if kind in ("feature", "attribute", "accepted", "generated", "event", "revision"):
        return key[0]
    return None


def diff_models(
    ours: NodeModel,
    reference: NodeModel,
    spec: Spec,
    *,
    ours_label: str = "ours",
    reference_label: str = "reference",
    ignore_root: bool = False,
    ignore_noise: bool = False,
) -> DiffResult:
    """Structural diff. ``ignore_root`` skips endpoint 0; ``ignore_noise`` also drops
    vendor-specific content and runtime-state diagnostics clusters of the reference
    (what a trimmed interview or a vendor firmware adds on top of the spec)."""
    notes: list[str] = []
    ignored: Counter = Counter()
    o_par, r_par = parents(ours), parents(reference)

    def non_root(model):
        return [
            e
            for e in model.endpoints.values()
            if e.id != 0 and ROOT_DEVICE_TYPE not in _dt_ids(e)
        ]

    pairs, only_o, only_r = match_endpoints(non_root(ours), non_root(reference))
    pair_names: dict[tuple[str, int], str] = {}  # (side, ep) -> ref-based name
    pair_list: list[tuple[EndpointInst, EndpointInst, str]] = []
    if not ignore_root and 0 in ours.endpoints and 0 in reference.endpoints:
        pair_list.append((ours.endpoints[0], reference.endpoints[0], "root"))
    for o, r in pairs:
        name = f"ep{o.id}<->ep{r.id}"
        pair_names[("o", o.id)] = f"ref-ep{r.id}"
        pair_names[("r", r.id)] = f"ref-ep{r.id}"
        pair_list.append((o, r, name))

    def parent_value(side: str, model: NodeModel, ep: EndpointInst, par: dict):
        p = par.get(ep.id, 0)
        if p == 0:
            return "root"
        return pair_names.get((side, p), "unmatched")

    diffs: list[Difference] = []
    pair_dicts: list[dict] = []
    total_ours = total_ref = common = 0

    def filter_noise(facts: dict, ep: EndpointInst) -> dict:
        if not ignore_noise:
            return facts
        out = {}
        for key, value in facts.items():
            kind = key[0]
            if kind == "revision":
                ignored["revisions"] += 1  # reference firmware may predate the spec
                continue
            if kind == "device_type":
                out[key] = None
                continue
            if kind in _CLUSTER_KINDS and _is_vendor(kind, key[1:]):
                ignored["vendor-specific"] += 1
                continue
            owner = _owner_cluster(kind, key[1:])
            if (
                kind in ("attribute", "accepted", "generated", "event")
                and owner in _DIAGNOSTICS
                and ep.id != 0
            ):
                ignored["diagnostics-runtime"] += 1
                continue
            out[key] = value
        return out

    for o, r, name in pair_list:
        of = endpoint_facts(
            spec, o, parent_value("o", ours, o, o_par) if o.id else None
        )
        rf = endpoint_facts(
            spec, r, parent_value("r", reference, r, r_par) if r.id else None
        )
        # unknown element lists: drop on both sides
        for (cid, kind), known in _known_lists(o).items():
            if not known:
                for k in [k for k in rf if k[0] == kind and k[1] == cid]:
                    del rf[k]
                    ignored["ours-unknown-lists"] += 1
        for (cid, kind), known in _known_lists(r).items():
            if not known:
                for k in [k for k in of if k[0] == kind and k[1] == cid]:
                    del of[k]
                    ignored["reference-unknown-lists"] += 1
        # tags / labels / parents are only comparable when both sides know them
        if "catalog summary" in reference_label:
            for k in [k for k in of if k[0] in ("tag", "label")]:
                del of[k]
            rf = {k: v for k, v in rf.items() if k[0] not in ("tag", "label")}
        of, rf = filter_noise(of, o), filter_noise(rf, r)
        total_ours += len(of)
        total_ref += len(rf)
        pair_common = 0
        p_ours = set(o.servers)
        p_ref = set(r.servers)
        for key in sorted(set(of) | set(rf), key=lambda k: tuple(str(x) for x in k)):
            kind = key[0]
            in_o, in_r = key in of, key in rf
            if in_o and in_r and of[key] == rf[key]:
                pair_common += 1
                continue
            if kind in ("device_type", "tag") and in_o and in_r:
                pass  # value differs: revision / label
            owner = _owner_cluster(kind, key[1:])
            if owner is not None and kind != "revision":
                # sub-items of a cluster that exists on one side only
                if (owner in p_ours) != (owner in p_ref):
                    continue
            if kind == "revision" and (owner in p_ours) != (owner in p_ref):
                continue
            change = "changed" if in_o and in_r else ("extra" if in_o else "missing")
            if kind == "parent" and not (in_o and in_r):
                continue
            ov = _fmt_value(kind, key, of.get(key), spec) if in_o else None
            rv = _fmt_value(kind, key, rf.get(key), spec) if in_r else None
            if kind == "device_type" and in_o and in_r:
                kind_item = "device type revision"
                item = f"{kind_item} {_device_label(spec, key[1])}"
            else:
                item = _item_name(kind, key[1:], spec)
            diffs.append(
                Difference(
                    CLASS_OF_KIND[kind],
                    kind,
                    change,
                    name,
                    item,
                    ov,
                    rv,
                    o.id if o is not None else None,
                    r.id if r is not None else None,
                )
            )
        common += pair_common
        pair_dicts.append(
            {
                "ours": o.id,
                "reference": r.id,
                "device_types_ours": _dt_text(spec, o),
                "device_types_reference": _dt_text(spec, r),
                "common": pair_common,
                "ours_items": len(of),
                "reference_items": len(rf),
                "score": round(
                    2 * pair_common / (len(of) + len(rf)) if of or rf else 1.0, 4
                ),
            }
        )

    # endpoints with no counterpart
    for side, eps, model in (("o", only_o, ours), ("r", only_r, reference)):
        for e in eps:
            facts = filter_noise(endpoint_facts(spec, e, None), e)
            if side == "o":
                total_ours += len(facts)
            else:
                total_ref += len(facts)
            diffs.append(
                Difference(
                    "endpoint",
                    "endpoint",
                    "extra" if side == "o" else "missing",
                    f"ep{e.id}",
                    f"endpoint {_dt_text(spec, e)}",
                    "present" if side == "o" else None,
                    None if side == "o" else "present",
                    e.id if side == "o" else None,
                    e.id if side == "r" else None,
                )
            )
            pair_dicts.append(
                {
                    "ours": e.id if side == "o" else None,
                    "reference": e.id if side == "r" else None,
                    "device_types_ours": _dt_text(spec, e) if side == "o" else None,
                    "device_types_reference": _dt_text(spec, e)
                    if side == "r"
                    else None,
                    "common": 0,
                    "ours_items": len(facts) if side == "o" else 0,
                    "reference_items": len(facts) if side == "r" else 0,
                    "score": 0.0,
                }
            )

    for model, label in ((ours, ours_label), (reference, reference_label)):
        for n in model.notes:
            if "catalog summary" in n:
                notes.append(f"{label}: {n}")
    score = 2 * common / (total_ours + total_ref) if total_ours + total_ref else 1.0
    return DiffResult(
        ours_label,
        reference_label,
        ours.mode,
        reference.mode,
        score,
        pair_dicts,
        diffs,
        notes,
        dict(ignored),
    )


# ----------------------------------------------------------------------------
# rendering
# ----------------------------------------------------------------------------
def render_diff(res: DiffResult, verbose: bool = False) -> str:
    out = [
        f"ours:      {res.ours_label} [{res.ours_mode}]",
        f"reference: {res.reference_label} [{res.reference_mode}]",
        f"match score: {res.score * 100:.1f}%   gaps: {len(res.gaps)}   extras: {len(res.extras)}",
        "",
        "Endpoint pairing (by device types, then order):",
    ]
    for p in res.pairs:
        o = f"ep{p['ours']}" if p["ours"] is not None else "-"
        r = f"ep{p['reference']}" if p["reference"] is not None else "-"
        dts = p["device_types_ours"] or p["device_types_reference"]
        out.append(f"  {o:>5} <-> {r:<5} {p['score'] * 100:5.1f}%  {dts}")
    sym = {"missing": "-", "extra": "+", "changed": "~"}
    rows = [
        (
            sym[d.change],
            d.endpoint,
            d.item,
            d.ours or "",
            d.reference or "",
        )
        for d in res.differences
    ]
    if rows:
        head = ("", "endpoint", "item", "ours", "reference")
        widths = [
            min(max(len(str(r[i])) for r in [head, *rows]), cap)
            for i, cap in enumerate((1, 14, 64, 22, 22))
        ]
        out += ["", "Differences (- only in reference, + only in ours, ~ differs):"]
        for r in [head, *rows]:
            out.append(
                "  "
                + "  ".join(
                    str(c)[: widths[i]].ljust(widths[i]) for i, c in enumerate(r)
                ).rstrip()
            )
    else:
        out += ["", "No structural differences."]
    counts = res.gap_counts()
    out += [
        "",
        "Gaps by class: "
        + ", ".join(f"{t}={counts[k]}" for k, t in CLASSES if counts[k])
        if res.gaps
        else "Gaps by class: none",
    ]
    if res.ignored and verbose:
        out.append(
            "Ignored: " + ", ".join(f"{k}={v}" for k, v in sorted(res.ignored.items()))
        )
    for n in res.notes:
        out.append(f"Note: {n}")
    return "\n".join(out)


# ----------------------------------------------------------------------------
# gallery
# ----------------------------------------------------------------------------
MIRRORS_RE = re.compile(r"^\s*#\s*mirrors\s*:\s*(.+?)\s*$", re.IGNORECASE)


def find_mirrors(path: Path, max_lines: int = 60) -> list[str]:
    """Catalog ids from the ``# mirrors: <id>[, <id>...]`` header of a YAML."""
    ids: list[str] = []
    with path.open(errors="replace") as fh:
        for i, line in enumerate(fh):
            if i >= max_lines:
                break
            m = MIRRORS_RE.match(line)
            if m:
                ids += [x.strip() for x in re.split(r"[,\s]+", m.group(1)) if x.strip()]
    return ids


def variant_name(path: Path, device: str) -> str:
    stem = path.stem
    if stem == device:
        return "base"
    if stem.startswith(device + "-"):
        return stem[len(device) + 1 :]
    return stem


def _layout_for(path: Path, cache: Path | None, refresh: bool) -> dict:
    from .layout import extract_layout

    if cache is not None:
        cached = cache / f"{path.stem}.layout.json"
        if cached.exists() and not refresh:
            return json.loads(cached.read_text())
    layout = extract_layout(path)
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{path.stem}.layout.json").write_text(json.dumps(layout, indent=1))
    return layout


def gallery(
    directory: Path,
    spec: Spec,
    profile: Profile,
    *,
    cache: Path | None = None,
    refresh: bool = False,
    jobs: int = 1,
    ignore_root: bool = False,
    ignore_noise: bool = False,
) -> list[dict]:
    """Diff every ``# mirrors:`` YAML below ``directory`` against its catalog reference."""
    from .model import from_layout

    work = []
    for path in sorted(directory.rglob("*.y*ml")):
        if path.suffix not in (".yaml", ".yml") or path.name == "secrets.yaml":
            continue
        for device in find_mirrors(path):
            work.append((device, path))

    def run(item):
        device, path = item
        row = {
            "device": device,
            "variant": variant_name(path, device),
            "file": str(path),
        }
        try:
            layout = _layout_for(path, cache, refresh)
            ours = from_layout(layout, spec, profile)
            ref, label, _kind = resolve_reference(device, spec, profile)
            res = diff_models(
                ours,
                ref,
                spec,
                ours_label=str(path),
                reference_label=label,
                ignore_root=ignore_root,
                ignore_noise=ignore_noise,
            )
            row.update(
                score=round(res.score, 4),
                gaps=res.gap_counts(),
                extras=res.extra_counts(),
                total_gaps=len(res.gaps),
                total_extras=len(res.extras),
                endpoints_matched=sum(
                    1
                    for p in res.pairs
                    if p["ours"] is not None and p["reference"] is not None
                ),
                reference_kind=ref.mode if "dump" in label else "summary",
            )
        except Exception as exc:  # noqa: BLE001 - one broken YAML must not stop the batch
            row["error"] = (
                str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc)
            )
        return row

    if jobs > 1:
        with ThreadPoolExecutor(jobs) as pool:
            rows = list(pool.map(run, work))
    else:
        rows = [run(w) for w in work]
    rows.sort(key=lambda r: (r["device"], r["variant"]))
    return rows


def render_matrix(rows: list[dict], markdown: bool = False) -> str:
    head = ["device", "variant", "score", "gaps", *[t for _, t in CLASSES], "extra"]
    body = []
    for r in rows:
        if "error" in r:
            body.append(
                [r["device"], r["variant"], "ERROR", r["error"][:60]]
                + [""] * (len(head) - 4)
            )
            continue
        body.append(
            [
                r["device"],
                r["variant"],
                f"{r['score'] * 100:.1f}%",
                str(r["total_gaps"]),
                *[str(r["gaps"][k]) if r["gaps"][k] else "." for k, _ in CLASSES],
                str(r["total_extras"]),
            ]
        )
    if markdown:
        lines = [
            "| " + " | ".join(head) + " |",
            "|" + "|".join("---" for _ in head) + "|",
        ]
        lines += ["| " + " | ".join(b) + " |" for b in body]
        return "\n".join(lines)
    widths = [max(len(x[i]) for x in [head, *body]) for i in range(len(head))]
    lines = [
        "  ".join(c.ljust(widths[i]) for i, c in enumerate(row)).rstrip()
        for row in [head, *body]
    ]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def _common(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--spec-version", default=DEFAULT_VERSION)
    ap.add_argument("--spec-dir", type=Path)
    ap.add_argument(
        "--ignore-root",
        action="store_true",
        help="skip the root node endpoint (ep0) and its infrastructure clusters",
    )
    ap.add_argument(
        "--ignore-noise",
        action="store_true",
        help="drop cluster/device type revisions, vendor-specific content and runtime diagnostics "
        "clusters on application endpoints (trimmed-interview / firmware noise)",
    )
    ap.add_argument("--json", type=Path, help="write the full result as JSON here")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m tools.conformance",
        description="Structural diff against a reference device.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diff", help="diff ours against a reference dump / catalog id")
    d.add_argument("ours", type=Path, help="YAML, layout.json or dump.json")
    d.add_argument(
        "reference",
        help="dump/layout/YAML file or catalog id (see $MATTER_DEVICE_CATALOG)",
    )
    d.add_argument("-v", "--verbose", action="store_true")
    d.add_argument("--keep-layout", help="for a YAML, save the exported layout here")
    d.add_argument(
        "--fail-on-gap", action="store_true", help="exit 1 when there is any gap"
    )
    _common(d)
    g = sub.add_parser("gallery", help="diff every '# mirrors:' YAML in a directory")
    g.add_argument("directory", type=Path)
    g.add_argument(
        "--layout-cache",
        type=Path,
        help="directory of <stem>.layout.json files: reused when present, written otherwise",
    )
    g.add_argument("--refresh", action="store_true", help="regenerate cached layouts")
    g.add_argument(
        "-j", "--jobs", type=int, default=1, help="parallel code generations"
    )
    g.add_argument("--markdown", action="store_true", help="markdown table output")
    _common(g)
    args = ap.parse_args(argv)

    spec = Spec(args.spec_dir, args.spec_version)
    profile = Profile()
    if args.cmd == "diff":
        try:
            ours, kind = load_model(args.ours, spec, profile, args.keep_layout)
            ref, label, _ = resolve_reference(args.reference, spec, profile)
        except Exception as exc:  # noqa: BLE001
            print(f"error: {exc}", file=sys.stderr)
            return 2
        res = diff_models(
            ours,
            ref,
            spec,
            ours_label=str(args.ours),
            reference_label=label,
            ignore_root=args.ignore_root,
            ignore_noise=args.ignore_noise,
        )
        print(render_diff(res, args.verbose))
        if args.json:
            args.json.write_text(json.dumps(res.as_dict(), indent=1) + "\n")
        return 1 if args.fail_on_gap and res.gaps else 0
    rows = gallery(
        args.directory,
        spec,
        profile,
        cache=args.layout_cache,
        refresh=args.refresh,
        jobs=args.jobs,
        ignore_root=args.ignore_root,
        ignore_noise=args.ignore_noise,
    )
    if not rows:
        print(f"no YAML with a '# mirrors: <catalog-id>' header below {args.directory}")
        return 0
    print(render_matrix(rows, args.markdown))
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1) + "\n")
    return 1 if any("error" in r for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
