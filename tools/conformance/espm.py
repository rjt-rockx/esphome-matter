"""esp-matter "what does the SDK actually create" profile.

The static (YAML) mode cannot see which attributes/commands esp-matter creates
for a cluster, because that is decided in C++ (``esp_matter_cluster.cpp`` and
``esp_matter_feature.cpp``). Those files are very regular, so this module
extracts, per cluster namespace:

* the attributes/commands/events created unconditionally by ``<cluster>::create``,
* the ones created for a given feature (``feature::<f>::add`` bodies, or
  ``if (has(feature::<f>::get_id()))`` blocks inside ``create``),
* feature implications (``feature::a::add`` called under ``has(b)``).

The result is stored in ``data/espm_profile.json`` (checked in, so CI needs no
esp-matter checkout). Regenerate with::

    python -m tools.conformance.espm [--esp-matter DIR] [--out FILE]

Names are kept as snake_case C++ identifiers and matched to the spec by
normalising (lower-case, alphanumerics only).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .conformance import norm

DEFAULT_ESP_MATTER = Path("/home/user/refs/esp-matter-1.6")
PROFILE_PATH = Path(__file__).parent / "data" / "espm_profile.json"
LEGACY = "components/esp_matter/data_model/legacy"

# esp-matter class name (as in ``cluster::create(endpoint, <Class>::Id``)
# whose normalised form differs from the spec cluster name.
CLASS_ALIASES = {
    "onoff": "onoff",
    "accesscontrol": "accesscontrol",
    "pm25concentrationmeasurement": "pm25concentrationmeasurement",
}


# ------------------------------------------------------------------ C++ scanning
def _strip_comments(text: str) -> str:
    text = re.sub(
        r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.DOTALL
    )
    return re.sub(r"//[^\n]*", "", text)


def _match_brace(text: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced braces")


def _namespaces(text: str):
    """Yield (name, body) for each ``namespace name {`` directly inside ``text``."""
    pos = 0
    out = []
    while True:
        m = re.compile(r"\bnamespace\s+(\w+)\s*\{").search(text, pos)
        if not m:
            return out
        end = _match_brace(text, m.end() - 1)
        out.append((m.group(1), text[m.end() : end]))
        pos = end + 1


def _functions(body: str, names=("create", "add")):
    out = {}
    for m in re.finditer(
        r"^(?:cluster_t\s*\*|esp_err_t)\s*(\w+)\s*\([^)]*\)\s*\{",
        body,
        flags=re.MULTILINE,
    ):
        if m.group(1) in names:
            end = _match_brace(body, m.end() - 1)
            out.setdefault(m.group(1), body[m.end() : end])
    return out


_IF_RE = re.compile(r"^\s*(?:\}\s*)?(?:else\s+)?if\s*\((.*)\)\s*\{\s*$")
_ELSE_RE = re.compile(r"^\s*\}\s*else\s*\{\s*$")
_HAS_RE = re.compile(r"(!?)\s*has\(\s*feature::(\w+)::get_id\(\)\s*\)")
# An optional qualifier names the cluster namespace of a base cluster (``mode_base::attribute::create_x`` inside the
# laundry washer mode creator, ``operational_state::attribute::create_x`` inside the RVC one).
_QUALIFIER = r"(?:(?!global::)[a-z_0-9]+::)?"
_ATTR_RE = re.compile(
    r"(?<![\w:])(?:esp_matter::)?" + _QUALIFIER + r"(global::)?attribute::create_(\w+)\s*\("
)
_CMD_RE = re.compile(
    r"(?<![\w:])(?:esp_matter::)?" + _QUALIFIER + r"command::create_(\w+)\s*\("
)
_EVENT_RE = re.compile(
    r"(?<![\w:])(?:esp_matter::)?" + _QUALIFIER + r"event::create_(\w+)\s*\("
)
_FEAT_ADD_RE = re.compile(r"feature::(\w+)::add\s*\(")
_FEATURE_ALIAS_RE = re.compile(
    r"uint32_t\s+(\w+)\s*=\s*feature::(\w+)::get_id\(\)\s*;"
)


def _join_multiline_ifs(body: str) -> list[str]:
    """Join ``if (a ||\n b) {`` conditions into one line."""
    out, buf = [], ""
    for line in body.splitlines():
        if buf:
            buf += " " + line.strip()
            if buf.count("(") <= buf.count(")"):
                out.append(buf)
                buf = ""
            continue
        if re.match(r"^\s*(?:\}\s*)?(?:else\s+)?if\s*\(", line) and line.count(
            "("
        ) > line.count(")"):
            buf = line.rstrip()
            continue
        out.append(line)
    if buf:
        out.append(buf)
    return out


def _scan_function(body: str):
    """Return list of (guard, kind, name) for a create()/add() body.

    ``guard`` is None (unconditional) or ("has", [features]) / ("nothas", [features]).
    Client-only blocks are skipped; "other" conditions are treated as unconditional
    (they are config/delegate checks that do not change the attribute set).
    """
    items = []
    stack: list[tuple[str, object]] = []  # entries: (kind, payload)
    # ``uint32_t imported_feature_map = feature::imported_energy::get_id();`` followed by
    # ``if ((get_feature_map_value(cluster) & imported_feature_map) == imported_feature_map)``
    # is a "hasall" guard: the attribute needs the owning feature and the guard feature.
    aliases = dict(_FEATURE_ALIAS_RE.findall(body))
    for line in _join_multiline_ifs(body):
        em = _ELSE_RE.match(line)
        if em and stack:
            kind, payload = stack.pop()
            if kind == "has":
                stack.append(("nothas", payload))
            elif kind == "server":
                stack.append(("client", None))
            elif kind == "client":
                stack.append(("server", None))
            else:
                stack.append(("other", None))
            continue
        im = _IF_RE.match(line)
        if im:
            cond = im.group(1)
            if "CLUSTER_FLAG_CLIENT" in cond and "SERVER" not in cond:
                stack.append(("client", None))
            elif "CLUSTER_FLAG_SERVER" in cond:
                stack.append(("server", None))
            else:
                hs = _HAS_RE.findall(cond)
                ks = re.findall(r"Feature::k(\w+)", cond)
                if ks and "feature_map" in cond:
                    # config->feature_map & to_underlying(Cluster::Feature::kX)
                    hs = [("", k) for k in ks]
                alias_hits = (
                    [
                        aliases[v]
                        for v in re.findall(r"&\s*(\w+)\s*\)\s*==\s*\1\b", cond)
                        if v in aliases
                    ]
                    if not hs and "||" not in cond
                    else []
                )
                if alias_hits:
                    stack.append(("hasall", alias_hits))
                elif hs and all(neg == "" for neg, _ in hs):
                    stack.append(("has", [f for _, f in hs]))
                elif hs and all(neg == "!" for neg, _ in hs):
                    stack.append(("nothas", [f for _, f in hs]))
                else:
                    stack.append(("other", None))
            continue
        stripped = line.strip()
        # closing braces (possibly several; a bare "}" ends the innermost block)
        opens = line.count("{")
        closes = line.count("}")
        if closes > opens and stripped.startswith("}"):
            for _ in range(closes - opens):
                if stack:
                    stack.pop()
            continue
        if opens > closes:
            # some other block (switch, for, ...): track so closes stay balanced
            stack.append(("other", None))
            continue
        if any(k == "client" for k, _ in stack):
            continue
        guard = None
        for k, p in stack:
            if k in ("has", "nothas", "hasall"):
                guard = (k, p)
        m = _ATTR_RE.search(line)
        if m:
            kind = "global" if m.group(1) else "attr"
            items.append((guard, kind, m.group(2)))
        m = _CMD_RE.search(line)
        if m:
            items.append((guard, "cmd", m.group(1)))
        m = _EVENT_RE.search(line)
        if m:
            items.append((guard, "event", m.group(1)))
        m = _FEAT_ADD_RE.search(line)
        if m:
            items.append((guard, "feature", m.group(1)))
    return items


_DT_CREATE_RE = re.compile(
    r"^\s*(?:cluster_t\s*\*\s*(\w+)\s*=\s*)?(?:cluster::)?(\w+)::create\((.*)\)\s*;"
)
_DT_FEATURE_RE = re.compile(r"(?:cluster::)?(\w+)::feature::(\w+)::add\(\s*(\w+)")
_DT_ELEM_RE = re.compile(
    r"(?:cluster::)?(\w+)::(attribute|command|event)::create_(\w+)\(\s*(\w+)"
)
_DT_DEFINE_RE = re.compile(
    r"#define\s+ESP_MATTER_(\w+)_DEVICE_TYPE_(ID|VERSION)\s+(\w+)"
)


def _scan_device_type_add(body: str) -> list[dict]:
    """Clusters created by an ``endpoint::<type>::add`` body, in order."""
    clusters: list[dict] = []
    by_var: dict[str, dict] = {}
    pp: list[str] = []  # preprocessor condition stack
    ifs = 0  # runtime ``if (...) {`` nesting with the condition text
    cond_stack: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("#"):
            if re.match(r"#\s*(if|ifdef|ifndef)\b", s):
                pp.append(s)
            elif re.match(r"#\s*else\b", s):
                pp.append("!" + pp.pop())
            elif re.match(r"#\s*endif", s):
                pp.pop()
            continue
        im = re.match(r"^(?:\}\s*)?if\s*\((.*)\)\s*\{\s*$", s)
        if im:
            cond_stack.append(im.group(1))
            continue
        if s.startswith("}"):
            if cond_stack:
                cond_stack.pop()
            continue
        cond = None
        if pp or cond_stack:
            cond = " && ".join(pp + cond_stack)
        m = _DT_CREATE_RE.match(s)
        if m and m.group(2) not in ("endpoint", "common"):
            ns = m.group(2)
            args = m.group(3)
            sides = []
            if "CLUSTER_FLAG_SERVER" in args:
                sides.append("server")
            if "CLUSTER_FLAG_CLIENT" in args:
                sides.append("client")
            if not sides:
                continue
            entry = dict(
                ns=ns, sides=sides, features=[], attrs=[], cmds=[], events=[], cond=cond
            )
            clusters.append(entry)
            if m.group(1):
                by_var[m.group(1)] = entry
            continue
        m = _DT_FEATURE_RE.search(s)
        if m:
            entry = by_var.get(m.group(3)) or next(
                (c for c in reversed(clusters) if c["ns"] == m.group(1)), None
            )
            if entry is not None:
                entry["features"].append(m.group(2))
            continue
        m = _DT_ELEM_RE.search(s)
        if m:
            entry = by_var.get(m.group(4)) or next(
                (c for c in reversed(clusters) if c["ns"] == m.group(1)), None
            )
            if entry is not None:
                key = {"attribute": "attrs", "command": "cmds", "event": "events"}[
                    m.group(2)
                ]
                entry[key].append(m.group(3))
    return clusters


def _build_device_types(esp_matter: Path) -> dict:
    src = _strip_comments((esp_matter / LEGACY / "esp_matter_endpoint.cpp").read_text())
    header = (esp_matter / LEGACY / "esp_matter_endpoint_impl.h").read_text()
    macros = {}
    for name, kind, value in _DT_DEFINE_RE.findall(header):
        macros.setdefault(norm(name), {})[kind.lower()] = int(value, 0)
    top = dict(_namespaces(src))
    ep = dict(_namespaces(dict(_namespaces(top["esp_matter"]))["endpoint"]))
    out = {}
    for ns, body in ep.items():
        fns = _functions_any(body)
        if "add" not in fns:
            continue
        info = macros.get(norm(ns), {})
        out[ns] = dict(
            id=info.get("id"),
            version=info.get("version"),
            clusters=_scan_device_type_add(fns["add"]),
        )
    return out


def _functions_any(body: str):
    out = {}
    for m in re.finditer(
        r"^esp_err_t\s+add\s*\(\s*endpoint_t\s*\*\s*endpoint[^)]*\)\s*\{",
        body,
        flags=re.MULTILINE,
    ):
        end = _match_brace(body, m.end() - 1)
        out["add"] = body[m.end() : end]
    return out


def _apply_code_driven_overlay(clusters: dict) -> None:
    """Model clusters whose served model is decided by connectedhomeip C++, not the attribute store.

    esp-matter registers some root-node clusters as code-driven ServerClusters. For those,
    the attribute list and the accepted/generated command lists on the wire come from the
    cluster class, not from what esp_matter_cluster.cpp creates in the store, so the
    store-based extraction under- or over-reports.

    * General Diagnostics (GeneralDiagnosticsCluster.h): the constructor force-sets UpTime and
      DeviceLoadStatus, so DeviceLoadStatus is listed and readable although the legacy
      create() never makes it.
    * Network Commissioning (NetworkCommissioningCluster::AcceptedCommands/GeneratedCommands):
      ScanNetworks/RemoveNetwork/ConnectNetwork/ReorderNetwork and the three responses are
      only advertised with the Wi-Fi or Thread feature; Ethernet-only exposes none, although
      the legacy create() makes them in the store.

    Provider dispatch: esp_matter_data_model_provider.cpp (mRegistry.Get(path) first).
    """
    gd = clusters.get("general_diagnostics")
    if gd and "device_load_status" not in gd["base"]["attrs"]:
        gd["base"]["attrs"].append("device_load_status")
    nc = clusters.get("network_commissioning")
    if nc:
        moved = []
        for key in ("cmds",):
            for name in (
                "scan_networks",
                "remove_network",
                "connect_network",
                "reorder_network",
                "scan_networks_response",
                "network_config_response",
                "connect_network_response",
            ):
                if name in nc["base"][key]:
                    nc["base"][key].remove(name)
                    moved.append(name)
        for f in ("WiFiNetworkInterface", "ThreadNetworkInterface"):
            lst = nc["features"].setdefault(f, dict(attrs=[], cmds=[], events=[]))["cmds"]
            for name in moved:
                if name not in lst:
                    lst.append(name)


def build_profile(esp_matter: Path = DEFAULT_ESP_MATTER) -> dict:
    cluster_src = _strip_comments(
        (esp_matter / LEGACY / "esp_matter_cluster.cpp").read_text()
    )
    feature_src = _strip_comments(
        (esp_matter / LEGACY / "esp_matter_feature.cpp").read_text()
    )

    clusters: dict[str, dict] = {}

    def entry(ns: str) -> dict:
        return clusters.setdefault(
            ns,
            dict(
                cls=None,
                base=dict(attrs=[], cmds=[], events=[]),
                features={},
                unless={},
                implies={},
            ),
        )

    def feat(c: dict, f: str) -> dict:
        return c["features"].setdefault(f, dict(attrs=[], cmds=[], events=[]))

    def add_items(c: dict, items, owner_feature=None):
        for guard, kind, name in items:
            if kind == "global":
                continue
            if kind == "feature":
                if guard and guard[0] == "has":
                    for g in guard[1]:
                        c["implies"].setdefault(g, [])
                        if name not in c["implies"][g]:
                            c["implies"][g].append(name)
                elif owner_feature is None:
                    c["implies"].setdefault("*", [])
                    if name not in c["implies"]["*"]:
                        c["implies"]["*"].append(name)
                continue
            bucket = {"attr": "attrs", "cmd": "cmds", "event": "events"}[kind]
            if guard and guard[0] == "hasall":
                # Created by feature::<owner>::add() only when the guard features are on as
                # well (CumulativeEnergyImported needs CUME and IMPE): "a+b" keys need every
                # named feature.
                names = [owner_feature, *guard[1]] if owner_feature else guard[1]
                lst = feat(c, "+".join(dict.fromkeys(names)))[bucket]
                if name not in lst:
                    lst.append(name)
            elif guard and guard[0] == "has":
                for g in guard[1]:
                    lst = feat(c, g)[bucket]
                    if name not in lst:
                        lst.append(name)
            elif guard and guard[0] == "nothas":
                for g in guard[1]:
                    lst = c["unless"].setdefault(g, dict(attrs=[], cmds=[], events=[]))[
                        bucket
                    ]
                    if name not in lst:
                        lst.append(name)
            elif owner_feature is not None:
                lst = feat(c, owner_feature)[bucket]
                if name not in lst:
                    lst.append(name)
            else:
                lst = c["base"][bucket]
                if name not in lst:
                    lst.append(name)

    aliases: dict[str, tuple[str, str]] = {}
    # cluster::<ns>::create
    top = dict(_namespaces(cluster_src))
    cluster_ns = dict(_namespaces(dict(_namespaces(top["esp_matter"]))["cluster"]))
    for ns, body in cluster_ns.items():
        fns = _functions(body, ("create",))
        if "create" not in fns:
            continue
        alias = re.search(
            r"return\s+(\w+)::create\(\s*endpoint\s*,\s*config\s*,\s*flags\s*,\s*(\w+)::Id",
            fns["create"],
        )
        if alias:
            # e.g. carbon_dioxide_concentration_measurement -> shared concentration_measurement::create
            aliases[ns] = (alias.group(1), alias.group(2))
            continue
        c = entry(ns)
        m = re.search(
            r"cluster::create\(\s*endpoint\s*,\s*([\w:]+?)::Id", fns["create"]
        )
        c["cls"] = m.group(1) if m else None
        add_items(c, _scan_function(fns["create"]))

    # feature::<f>::add
    top = dict(_namespaces(feature_src))
    clus = dict(_namespaces(dict(_namespaces(top["esp_matter"]))["cluster"]))
    for ns, body in clus.items():
        sub = dict(_namespaces(body))
        if "feature" not in sub:
            continue
        c = entry(ns)
        for fname, fbody in _namespaces(sub["feature"]):
            fns = _functions(fbody, ("add",))
            if "add" in fns:
                feat(c, fname)
                add_items(c, _scan_function(fns["add"]), owner_feature=fname)
    import copy

    for ns, (template, cls) in aliases.items():
        if template in clusters:
            c = copy.deepcopy(clusters[template])
            c["cls"] = cls
            clusters[ns] = c
    revisions = {}
    rev_src = (
        esp_matter
        / "components/esp_matter/data_model/private/esp_matter_cluster_revisions.h"
    ).read_text()
    for ns, value in re.findall(
        r"namespace\s+(\w+)\s*\{\s*constexpr\s+uint16_t\s+cluster_revision\s*=\s*(\d+)",
        rev_src,
    ):
        revisions[ns] = int(value)
    for ns, rev in revisions.items():
        if ns in clusters:
            clusters[ns]["revision"] = rev
    _apply_code_driven_overlay(clusters)
    return dict(
        esp_matter=str(esp_matter),
        clusters=clusters,
        device_types=_build_device_types(esp_matter),
    )


# --------------------------------------------------------------------- queries
# Elements that esp-matter creates only for a combination of features. The extractor attributes
# them to a single feature, so the missing half of the condition is listed here:
# (cluster namespace, features that must be enabled, features that must be absent, removed).
# The elements are removed from the prediction when the combination holds.
#   switch_cluster::feature::momentary_switch_multi_press::add (esp_matter_feature.cpp) calls
#   create_multi_press_ongoing() only when ActionSwitch is not in the feature map, matching the
#   spec (MultiPressOngoing: MSM & !AS).
#   door_lock::feature::credential_over_the_air_access::add and pin_credential::add create
#   RequirePINforRemoteOperation only when the other feature is already in the feature map,
#   matching the spec (COTA & PIN).
GUARDED_AWAY = [
    (
        "switch_cluster",
        ("action_switch",),
        (),
        {"events": ["multi_press_ongoing"]},
    ),
    (
        "door_lock",
        ("credential_over_the_air_access",),
        ("pin_credential",),
        {"attrs": ["require_pin_for_remote_operation"]},
    ),
]


class Profile:
    def __init__(self, data: dict | None = None):
        if data is None:
            data = json.loads(PROFILE_PATH.read_text())
        self.data = data
        self.by_class = {}
        for ns, c in data["clusters"].items():
            if c.get("cls"):
                self.by_class[norm(c["cls"])] = ns

    def namespace_for(self, cluster_name: str) -> str | None:
        n = norm(cluster_name.removesuffix(" Cluster"))
        ns = self.by_class.get(n)
        if ns:
            return ns
        for cand in self.data["clusters"]:
            if norm(cand) == n:
                return cand
        return None

    @staticmethod
    def _fnorm(name: str) -> str:
        return norm(name).replace("and", "")

    def revision(self, cluster_name: str) -> int | None:
        ns = self.namespace_for(cluster_name)
        return self.data["clusters"][ns].get("revision") if ns else None

    def predict(self, cluster_name: str, feature_names: list[str]):
        """(attributes, commands, events) esp-matter creates, as normalised-name
        sets, or None if the cluster is not covered by the legacy model."""
        ns = self.namespace_for(cluster_name)
        if ns is None:
            return None
        c = self.data["clusters"][ns]
        if c.get("cls") is None:
            return None
        enabled = {self._fnorm(f) for f in feature_names}
        # resolve implications
        changed = True
        by_norm = {self._fnorm(k): k for k in c["features"]}
        while changed:
            changed = False
            for f in list(enabled):
                orig = by_norm.get(f)
                for imp in c["implies"].get(orig, []) if orig else []:
                    if self._fnorm(imp) not in enabled:
                        enabled.add(self._fnorm(imp))
                        changed = True
        for imp in c["implies"].get("*", []):
            enabled.add(self._fnorm(imp))
        attrs = {norm(a) for a in c["base"]["attrs"]}
        cmds = {norm(a) for a in c["base"]["cmds"]}
        events = {norm(a) for a in c["base"]["events"]}
        for fname, f in c["features"].items():
            if all(self._fnorm(part) in enabled for part in fname.split("+")):
                attrs |= {norm(a) for a in f["attrs"]}
                cmds |= {norm(a) for a in f["cmds"]}
                events |= {norm(a) for a in f["events"]}
        for fname, f in c["unless"].items():
            if self._fnorm(fname) not in enabled:
                attrs |= {norm(a) for a in f["attrs"]}
                cmds |= {norm(a) for a in f["cmds"]}
                events |= {norm(a) for a in f["events"]}
        for gns, present, absent, gone in GUARDED_AWAY:
            if (
                gns == ns
                and all(self._fnorm(f) in enabled for f in present)
                and not any(self._fnorm(f) in enabled for f in absent)
            ):
                attrs -= {norm(a) for a in gone.get("attrs", ())}
                cmds -= {norm(a) for a in gone.get("cmds", ())}
                events -= {norm(a) for a in gone.get("events", ())}
        return attrs, cmds, events


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Regenerate the esp-matter creation profile"
    )
    ap.add_argument("--esp-matter", type=Path, default=DEFAULT_ESP_MATTER)
    ap.add_argument("--out", type=Path, default=PROFILE_PATH)
    args = ap.parse_args(argv)
    profile = build_profile(args.esp_matter)
    # Keep the checked-in file relocatable and diff-friendly.
    profile["esp_matter"] = args.esp_matter.name
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(profile, indent=1, sort_keys=True) + "\n")
    print(f"{len(profile['clusters'])} clusters -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
