#!/usr/bin/env python3
"""Generate components/matter/data_model/espmatter_names.json.

The data model JSON files (clusters.json, device_types.json) are generated from
the Matter spec / ZAP XML, so their names are spec names ("Hue And Saturation",
"CO Alarm", "Unbolt"). The identifiers esp-matter exposes in C++
(esp_matter::cluster::<cluster>::feature::<feature>::add, the
esp_matter::endpoint::<device_type>::config_t members, the
esp_matter::cluster::<cluster>::attribute::create_<attribute> functions)
follow esp-matter's own naming, which deviates from a plain snake_casing of the
spec name in many places and is not available at all for some clusters and
features.

This tool parses the esp-matter headers that the pinned esp_matter component
(davidvtwout/esp_matter 1.6.0~2 with the legacy data model, i.e.
CONFIG_ESP_MATTER_ENABLE_GENERATED_DATA_MODEL is not set) is built from:

    components/esp_matter/data_model/legacy/esp_matter_cluster_impl.h
    components/esp_matter/data_model/legacy/esp_matter_feature_impl.h
    components/esp_matter/data_model/legacy/esp_matter_attribute_impl.h
    components/esp_matter/data_model/legacy/esp_matter_endpoint_impl.h

and writes a JSON table that maps every cluster/feature/attribute/device type of
our data model to the esp-matter identifier (or null if esp-matter has none).
Run it again after bumping the esp_matter version:

    tools/gen_espmatter_names.py [--esp-matter DIR] [--check]

--esp-matter is the ``components/esp_matter`` directory of the esp_matter
component; when omitted it is searched for in ESP_MATTER_PATH, the ESPHome
build/managed component directories below the current directory and $HOME, and
finally /home/user/refs/esp-matter-1.6.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA_MODEL = REPO / "components" / "matter" / "data_model"
OUTPUT = DATA_MODEL / "espmatter_names.json"

LEGACY = Path("data_model") / "legacy"

# --------------------------------------------------------------------------- #
# Manual aliases. Everything not listed here is matched by comparing the
# lower-cased alphanumerics of the spec name with the esp-matter identifier.
# Each alias must point at an identifier that exists in the headers, the tool
# fails otherwise.
# --------------------------------------------------------------------------- #

# spec cluster name -> esp-matter cluster namespace
CLUSTER_ALIASES: dict[str, str] = {
    # "switch" is a C++ keyword.
    "Switch": "switch_cluster",
    "Smoke CO Alarm": "smoke_co_alarm",
    "Refrigerator And Temperature Controlled Cabinet Mode": "refrigerator_and_tcc_mode",
}

# (spec cluster name, spec feature name) -> esp-matter feature namespace.
FEATURE_ALIASES: dict[tuple[str, str], str] = {
    # "auto" is a C++ keyword.
    ("Fan Control", "Auto"): "fan_auto",
    ("Door Lock", "Unbolt"): "unbolting",
    ("Door Lock", "CredentialsOverTheAirAccess"): "credential_over_the_air_access",
    ("Door Lock", "WeekDayAccessSchedules"): "weekday_access_schedules",
    ("Color Control", "Hue And Saturation"): "hue_saturation",
}
# Deliberately NOT aliased: the Thermostat "ScheduleConfiguration" (SCH) feature
# is a different spec feature than "MatterScheduleConfiguration" (MSCH), which
# is the one esp-matter calls matter_schedule_configuration.

# (spec cluster name, spec attribute name) -> esp-matter create_<x> suffix.
ATTRIBUTE_ALIASES: dict[tuple[str, str], str] = {}

# spec device type name (device_types.json "name") -> esp-matter endpoint namespace.
DEVICE_TYPE_ALIASES: dict[str, str] = {
    "evse": "energy_evse",
}


def norm(name: str) -> str:
    """Lower-cased alphanumerics: 'Hue And Saturation' -> 'hueandsaturation'."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


# --------------------------------------------------------------------------- #
# Header parsing
# --------------------------------------------------------------------------- #


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    return text


_TOKEN = re.compile(r"namespace\s+(\w+)\s*\{|\{|\}")


class Headers:
    """Everything we need from the esp-matter legacy data model headers."""

    def __init__(self, esp_matter_dir: Path):
        legacy = esp_matter_dir / LEGACY
        self.dir = esp_matter_dir
        # cluster namespace -> {"features": {ns: add_takes_config}, "attributes": set()}
        self.clusters: dict[str, dict] = {}
        # endpoint namespace -> {member: type}
        self.endpoints: dict[str, dict[str, str]] = {}
        self._aliases: list[tuple[str, str, str]] = []
        self._parse_clusters(legacy / "esp_matter_cluster_impl.h")
        self._parse_features(legacy / "esp_matter_feature_impl.h")
        self._parse_attributes(legacy / "esp_matter_attribute_impl.h")
        self._apply_aliases()
        self._parse_endpoints(legacy / "esp_matter_endpoint_impl.h")
        # endpoint namespace -> (server cluster namespaces, nested endpoint namespaces)
        self.implied: dict[str, tuple[set[str], set[str]]] = {}
        self._parse_implied(legacy / "esp_matter_endpoint.cpp")

    @staticmethod
    def _walk(path: Path):
        """Yield (namespace_path, line) for every line of ``path``.

        The namespace path is the stack of enclosing namespaces *before* the
        line's own braces are applied, except that a line opening a namespace is
        reported with that namespace included.
        """
        stack: list[str | None] = []  # None for non-namespace blocks
        for line in _strip_comments(path.read_text()).splitlines():
            events = list(_TOKEN.finditer(line))
            for event in events:
                if event.group(1):
                    stack.append(event.group(1))
                elif event.group(0) == "{":
                    stack.append(None)
                elif stack:
                    stack.pop()
            ns_path = [s for s in stack if s is not None]
            # Re-add namespaces closed on this very line so one-line declarations
            # (and closing lines) are still attributed correctly.
            yield stack[:], ns_path, line

    def _cluster_entry(self, name: str) -> dict:
        return self.clusters.setdefault(
            name,
            {
                "create": False,
                "feature_flags": False,
                "features": {},
                "attributes": set(),
            },
        )

    def _parse_clusters(self, path: Path) -> None:
        shared_config: dict[str, str] = {}
        for _stack, ns, line in self._walk(path):
            if (
                len(ns) == 3
                and ns[:2] == ["esp_matter", "cluster"]
                and re.search(r"\bcluster_t\s*\*\s*create\(", line)
            ):
                self._cluster_entry(ns[2])["create"] = True
            elif (
                len(ns) == 3
                and ns[:2] == ["esp_matter", "cluster"]
                and re.search(r"\bfeature_flags\s*;", line)
            ):
                # config_t::feature_flags: create() evaluates the features.
                self._cluster_entry(ns[2])["feature_flags"] = True
            elif len(ns) == 3 and ns[:2] == ["esp_matter", "cluster"]:
                shared = re.search(r"using\s+config_t\s*=\s*(\w+)::config_t", line)
                if shared:
                    shared_config[ns[2]] = shared.group(1)
        for name, target in shared_config.items():
            if self.clusters.get(target, {}).get("feature_flags"):
                self._cluster_entry(name)["feature_flags"] = True

    def _resolve_aliases(self, path: Path) -> None:
        """Handle ``namespace feature = other_cluster::feature;`` (shared sets).

        The concentration measurement clusters share the features and
        attributes of ``concentration_measurement``.
        """
        for _stack, ns, line in self._walk(path):
            alias = re.match(
                r"\s*namespace\s+(feature|attribute)\s*=\s*(\w+)::\w+\s*;", line
            )
            if alias and len(ns) == 3 and ns[:2] == ["esp_matter", "cluster"]:
                self._aliases.append((ns[2], alias.group(1), alias.group(2)))

    def _apply_aliases(self) -> None:
        for cluster, kind, target in self._aliases:
            source = self.clusters.get(target)
            if source is None:
                continue
            entry = self._cluster_entry(cluster)
            if kind == "feature":
                entry["features"].update(source["features"])
            else:
                entry["attributes"] |= source["attributes"]

    def _parse_features(self, path: Path) -> None:
        self._resolve_aliases(path)
        for _stack, ns, line in self._walk(path):
            if (
                len(ns) == 5
                and ns[:2] == ["esp_matter", "cluster"]
                and ns[3] == "feature"
                and re.search(r"\besp_err_t\s+add\(", line)
            ):
                takes_config = "config_t" in line
                self._cluster_entry(ns[2])["features"][ns[4]] = takes_config

    def _parse_attributes(self, path: Path) -> None:
        self._resolve_aliases(path)
        for _stack, ns, line in self._walk(path):
            if (
                len(ns) == 4
                and ns[:2] == ["esp_matter", "cluster"]
                and ns[3] == "attribute"
            ):
                match = re.search(r"\battribute_t\s*\*\s*create_(\w+)\(", line)
                if match:
                    self._cluster_entry(ns[2])["attributes"].add(match.group(1))

    def _parse_endpoints(self, path: Path) -> None:
        """Collect the config_t members (including inherited ones) per endpoint."""
        # struct/typedef name -> {"bases": [...], "members": {name: type}}
        structs: dict[tuple[str, str], dict] = {}
        aliases: dict[tuple[str, str], str] = {}
        current: list[dict] = []  # stack of open structs
        device_types: set[str] = set()

        for stack, ns, line in self._walk(path):
            in_endpoint = ns[:2] == ["esp_matter", "endpoint"]
            scope = ns[2] if len(ns) > 2 else ""
            # Open struct on this line?
            opened = re.search(
                r"\b(?:typedef\s+)?struct\s*(\w*)\s*(?::\s*(?:public\s+)?([\w:]+))?\s*\{",
                line,
            )
            if in_endpoint and opened and not current:
                current.append(
                    {
                        "name": opened.group(1),
                        "base": opened.group(2),
                        "members": {},
                        "scope": scope,
                        "depth": len(stack),
                    }
                )
                continue
            if current:
                top = current[-1]
                if len(stack) < top["depth"]:
                    # struct closed: "} name;" or "} config_t;"
                    close = re.match(r"\s*\}\s*(\w*)\s*;", line)
                    name = (close.group(1) if close else "") or top["name"]
                    for key in {name, top["name"]} - {""}:
                        if key == "config":
                            key = "config_t"
                        structs[(top["scope"], key)] = top
                    current.pop()
                    continue
                member = re.match(
                    r"\s*((?:cluster::)\w+(?:::\w+)*::config_t)\s+(\w+)\s*;", line
                )
                if member and len(stack) == top["depth"]:
                    cfg_type = member.group(1)[: -len("::config_t")]
                    top["members"][member.group(2)] = cfg_type
            using = re.match(r"\s*using\s+config_t\s*=\s*([\w:]+)\s*;", line)
            if in_endpoint and using and scope:
                aliases[(scope, "config_t")] = using.group(1)
            if in_endpoint and scope and re.search(r"\bget_device_type_id\(", line):
                device_types.add(scope)

        def resolve(scope: str, name: str, seen=()) -> dict[str, str]:
            name = name.replace("esp_matter::endpoint::", "")
            if "::" in name:
                scope, name = name.split("::", 1)[0], name.split("::", 1)[1]
            key = (scope, name)
            if key in aliases:
                return resolve(scope, aliases[key], seen)
            if key not in structs:
                key = ("", name)
            if key not in structs or key in seen:
                return {}
            struct = structs[key]
            members: dict[str, str] = {}
            if struct["base"]:
                members.update(resolve(key[0], struct["base"], seen + (key,)))
            members.update(struct["members"])
            return members

        for device_type in sorted(device_types):
            self.endpoints[device_type] = resolve(device_type, "config_t")


    def _parse_implied(self, path: Path) -> None:
        """Clusters that ``endpoint::<x>::add()`` creates on its own.

        esp-matter's add() creates clusters (``identify::create(endpoint, ...,
        CLUSTER_FLAG_SERVER)``, ``cluster::thermostat::create(...)``) and calls
        the add() of other device types (``electrical_sensor::add(endpoint,
        ...)``, ``endpoint::power_source::add(endpoint, ...)``) regardless of
        what we configure. Their server sources must be compiled in, or the
        link fails. Client-only clusters are ignored.
        """
        bodies: dict[str, list[str]] = {}
        for _stack, ns, line in self._walk(path):
            if len(ns) == 3 and ns[:2] == ["esp_matter", "endpoint"]:
                bodies.setdefault(ns[2], []).append(line)
        for device_type, lines in bodies.items():
            text = "\n".join(lines)
            clusters: set[str] = set()
            nested: set[str] = set()
            for stmt in text.split(";"):
                created = re.search(r"\b(\w+)::create\(\s*endpoint\b", stmt)
                if (
                    created
                    and created.group(1) in self.clusters
                    and "CLUSTER_FLAG_SERVER" in stmt
                ):
                    clusters.add(created.group(1))
                added = re.search(r"\b(\w+)::add\(\s*endpoint\b", stmt)
                if added and added.group(1) in self.endpoints:
                    nested.add(added.group(1))
            self.implied[device_type] = (clusters, nested)

    def implied_clusters(self, device_type: str) -> set[str]:
        """Transitive closure of ``_parse_implied`` (cluster namespaces)."""
        result: set[str] = set()
        todo, seen = [device_type], set()
        while todo:
            current = todo.pop()
            if current in seen or current not in self.implied:
                continue
            seen.add(current)
            clusters, nested = self.implied[current]
            result |= clusters
            todo.extend(nested)
        return result


# --------------------------------------------------------------------------- #
# Table generation
# --------------------------------------------------------------------------- #


def _find_esp_matter() -> Path:
    candidates: list[str] = []
    if env := os.environ.get("ESP_MATTER_PATH"):
        candidates.append(env)
    home = os.path.expanduser("~")
    patterns = [
        "**/davidvtwout__esp_matter*/components/esp_matter",
        "**/managed_components/davidvtwout__esp_matter/components/esp_matter",
    ]
    bases = [os.getcwd(), str(REPO), home]
    # Other worktrees of a repository checkout share their builds, so also look
    # at the checkout root that contains this one.
    bases.extend(str(p) for p in REPO.parents if (p / ("." + "git")).is_dir())
    bases.append("/home/user/build")
    seen: set[str] = set()
    for base in bases:
        if base in seen or not os.path.isdir(base):
            continue
        seen.add(base)
        for pattern in patterns:
            candidates.extend(
                sorted(
                    glob.glob(
                        os.path.join(base, pattern), recursive=True, include_hidden=True
                    ),
                    key=os.path.getmtime,
                    reverse=True,
                )
            )
    candidates.append("/home/user/refs/esp-matter-1.6/components/esp_matter")
    for candidate in candidates:
        if (Path(candidate) / LEGACY / "esp_matter_cluster_impl.h").exists():
            return Path(candidate)
    sys.exit("esp_matter headers not found, pass --esp-matter DIR")


def _match(spec_name: str, candidates, alias: str | None = None) -> str | None:
    if alias is not None:
        if alias not in candidates:
            raise SystemExit(f"alias {spec_name!r} -> {alias!r} not in esp-matter")
        return alias
    matches = [c for c in candidates if norm(c) == norm(spec_name)]
    return matches[0] if len(matches) == 1 else None


def build_table(headers: Headers) -> dict:
    clusters_json = json.loads((DATA_MODEL / "clusters.json").read_text())
    device_types_json = json.loads((DATA_MODEL / "device_types.json").read_text())

    created = {n for n, c in headers.clusters.items() if c["create"]}
    table_clusters: dict[str, dict] = {}
    for cluster in clusters_json.values():
        spec_name = cluster["name"]
        # Key: the CamelCase name used as Cluster.name in clusters.py.
        key = re.sub(r"[/ \-.]", "", spec_name)
        ns = _match(spec_name, created, CLUSTER_ALIASES.get(spec_name))
        info = headers.clusters.get(ns) if ns else None
        entry: dict = {
            "namespace": ns,
            # Whether esp-matter's config_t of the cluster has feature_flags.
            "feature_flags": bool(info and info["feature_flags"]),
            "features": {},
            "attributes": {},
        }

        def walk_features(items):
            for feature in items:
                if feature.get("type") == "choice":
                    yield from walk_features(feature["features"])
                else:
                    yield feature

        for feature in walk_features(cluster.get("features", {}).values()):
            fname = feature["name"]
            fns = None
            if info:
                fns = _match(
                    fname,
                    info["features"],
                    FEATURE_ALIASES.get((spec_name, fname)),
                )
            entry["features"][fname] = {
                "namespace": fns,
                "add_takes_config": bool(fns and info["features"][fns]),
            }
        for attribute in cluster.get("attributes", {}).values():
            if not attribute.get("server"):
                continue
            # Same naming as Attribute.from_dict in data_model/attributes.py.
            aname = attribute.get("name", attribute.get("define"))
            if not aname:
                continue
            suffix = None
            if info:
                suffix = _match(
                    aname,
                    info["attributes"],
                    ATTRIBUTE_ALIASES.get((spec_name, aname)),
                )
            entry["attributes"][aname] = f"create_{suffix}" if suffix else None
        table_clusters[key] = entry

    # esp-matter cluster namespace -> CamelCase cluster key
    key_by_namespace = {
        e["namespace"]: key for key, e in table_clusters.items() if e["namespace"]
    }
    table_device_types: dict[str, dict] = {}
    for device_type in device_types_json.values():
        name = device_type["name"]
        ns = _match(name, headers.endpoints, DEVICE_TYPE_ALIASES.get(name))
        table_device_types[name] = {
            "namespace": ns,
            "config_members": dict(sorted(headers.endpoints[ns].items())) if ns else {},
            # Server clusters esp-matter creates in add() (also via nested
            # device types), as keys of "clusters". Must be compiled in.
            "implied_clusters": sorted(
                key_by_namespace[c]
                for c in (headers.implied_clusters(ns) if ns else ())
                if c in key_by_namespace
            ),
        }

    return {
        "_comment": "Generated by tools/gen_espmatter_names.py, do not edit. "
        "null means esp-matter has no such identifier.",
        "clusters": table_clusters,
        "device_types": table_device_types,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--esp-matter", type=Path, help="components/esp_matter dir")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--check", action="store_true", help="fail if the output is out of date"
    )
    args = parser.parse_args()

    esp_matter = args.esp_matter or _find_esp_matter()
    print(f"Parsing esp-matter headers in {esp_matter}", file=sys.stderr)
    table = build_table(Headers(esp_matter))
    text = json.dumps(table, indent=2, sort_keys=False) + "\n"
    if args.check:
        if not args.output.exists() or args.output.read_text() != text:
            print(f"{args.output} is out of date", file=sys.stderr)
            return 1
        return 0
    args.output.write_text(text)
    unsupported = [
        f"{c}.{f}"
        for c, entry in table["clusters"].items()
        if entry["namespace"]
        for f, v in entry["features"].items()
        if v["namespace"] is None
    ]
    print(
        f"Wrote {args.output}: "
        f"{sum(1 for c in table['clusters'].values() if c['namespace'])} clusters, "
        f"{len(unsupported)} features without esp-matter support",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
