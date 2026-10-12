"""Loader for the Matter data model XML (connectedhomeip ``data_model/<ver>``)."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .conformance import conform_of, norm

DEFAULT_SPEC_ROOT = Path(
    "/home/user/refs/esp-matter-1.6/connectedhomeip/connectedhomeip/data_model"
)
DEFAULT_VERSION = "1.6"

# Global attributes every cluster instance has (id -> name). All are
# mandatory in Matter 1.4+.
GLOBAL_ATTRIBUTES = {
    0xFFF8: "GeneratedCommandList",
    0xFFF9: "AcceptedCommandList",
    0xFFFA: "EventList",
    0xFFFB: "AttributeList",
    0xFFFC: "FeatureMap",
    0xFFFD: "ClusterRevision",
}


def _int(text: str | None) -> int | None:
    if text is None:
        return None
    return int(text, 0)


@dataclass
class Element:
    id: int
    name: str
    conform: ET.Element | None
    direction: str | None = None  # commands: commandToServer / responseFromServer

    @property
    def key(self) -> str:
        return norm(self.name)


@dataclass
class Feature:
    bit: int
    code: str
    name: str
    conform: ET.Element | None


@dataclass
class ClusterSpec:
    id: int
    name: str
    revision: int
    features: dict[str, Feature] = field(default_factory=dict)  # by code
    attributes: dict[int, Element] = field(default_factory=dict)
    # Keyed by command id; responses (responseFromServer) live in their own
    # id space, so they are stored under id | 0x10000.
    commands: dict[int, Element] = field(default_factory=dict)
    events: dict[int, Element] = field(default_factory=dict)
    base: str | None = None
    role: str = ""  # application / utility

    def feature_by_bit(self, bit: int) -> Feature | None:
        for f in self.features.values():
            if f.bit == bit:
                return f
        return None

    def feature_by_name(self, name: str) -> Feature | None:
        n = norm(name)
        for f in self.features.values():
            if norm(f.name) == n or norm(f.code) == n:
                return f
        return None

    def attribute_by_name(self, name: str) -> Element | None:
        n = norm(name)
        return next((a for a in self.attributes.values() if a.key == n), None)

    def command_by_name(self, name: str) -> Element | None:
        n = norm(name)
        return next((c for c in self.commands.values() if c.key == n), None)

    def event_by_name(self, name: str) -> Element | None:
        n = norm(name)
        return next((e for e in self.events.values() if e.key == n), None)

    def accepted_commands(self) -> dict[int, Element]:
        return {
            c.id: c
            for c in self.commands.values()
            if c.direction != "responseFromServer"
        }

    def generated_commands(self) -> dict[int, Element]:
        return {
            c.id: c
            for c in self.commands.values()
            if c.direction == "responseFromServer"
        }


@dataclass
class DeviceClusterReq:
    id: int
    name: str
    side: str
    conform: ET.Element | None
    features: dict[str, ET.Element | None] = field(
        default_factory=dict
    )  # code -> conform
    attributes: list[tuple[str, ET.Element | None]] = field(default_factory=list)
    commands: list[tuple[str, ET.Element | None]] = field(default_factory=list)
    events: list[tuple[str, ET.Element | None]] = field(default_factory=list)
    singleton: bool = False


@dataclass
class DeviceTypeSpec:
    id: int
    name: str
    revision: int
    classification: str  # node / simple / utility / dynamic / ...
    scope: str
    superset: str | None
    clusters: list[DeviceClusterReq]
    conditions: dict[str, str]
    composed: list[dict]


class Spec:
    def __init__(self, root: Path | str | None = None, version: str = DEFAULT_VERSION):
        root = Path(root or os.environ.get("MATTER_DATA_MODEL") or DEFAULT_SPEC_ROOT)
        self.dir = root / version if (root / version).is_dir() else root
        self.version = version
        self.clusters: dict[int, ClusterSpec] = {}
        self.device_types: dict[int, DeviceTypeSpec] = {}
        self.base_device_type: DeviceTypeSpec | None = None
        self._load_clusters()
        self._load_device_types()

    # ------------------------------------------------------------------ clusters
    def _load_clusters(self):
        parsed = []
        for path in sorted((self.dir / "clusters").glob("*.xml")):
            root = ET.parse(path).getroot()
            if root.tag == "cluster":
                parsed.append(root)
        bases = {}
        for root in parsed:
            if root.get("id") is None:
                first = root.find("clusterIds/clusterId")
                if first is not None and first.get("id") is None:
                    bases[first.get("name")] = root
        # A base cluster can also be a real cluster with its own id (Operational State is the base of RVC
        # Operational State and Oven Cavity Operational State, which inherit its events).
        for root in parsed:
            cls = root.find("classification")
            if cls is not None and cls.get("hierarchy") == "base":
                for cid in root.findall("clusterIds/clusterId"):
                    bases.setdefault(cid.get("name"), root)
        for root in parsed:
            cls = root.find("classification")
            base_name = cls.get("baseCluster") if cls is not None else None
            for cid in root.findall("clusterIds/clusterId"):
                if cid.get("id") is None:
                    continue
                spec = self._build_cluster(root, _int(cid.get("id")), cid.get("name"))
                if base_name and base_name in bases:
                    spec.base = base_name
                    base = self._build_cluster(bases[base_name], spec.id, spec.name)
                    for f in base.features.values():
                        spec.features.setdefault(f.code, f)
                    for coll in ("attributes", "commands", "events"):
                        mine = getattr(spec, coll)
                        # A derived cluster can restate a base command without its direction
                        # (Mode_MicrowaveOven.xml disallows ChangeToModeResponse that way): such
                        # a stub overrides the base command with the same id.
                        stubs = {
                            k & 0xFFFF
                            for k, v in mine.items()
                            if coll == "commands" and v.direction is None
                        }
                        for k, v in getattr(base, coll).items():
                            if coll == "commands" and k & 0xFFFF in stubs:
                                continue
                            mine.setdefault(k, v)
                self.clusters[spec.id] = spec

    @staticmethod
    def _build_cluster(root: ET.Element, cid: int, name: str) -> ClusterSpec:
        spec = ClusterSpec(id=cid, name=name, revision=_int(root.get("revision")) or 1)
        cls = root.find("classification")
        spec.role = cls.get("role", "") if cls is not None else ""
        for f in root.findall("features/feature"):
            spec.features[f.get("code")] = Feature(
                _int(f.get("bit")), f.get("code"), f.get("name"), conform_of(f)
            )
        for a in root.findall("attributes/attribute"):
            if a.get("id") is None:
                continue
            spec.attributes[_int(a.get("id"))] = Element(
                _int(a.get("id")), a.get("name"), conform_of(a)
            )
        for c in root.findall("commands/command"):
            resp = c.get("direction") == "responseFromServer"
            spec.commands[_int(c.get("id")) | (0x10000 if resp else 0)] = Element(
                _int(c.get("id")), c.get("name"), conform_of(c), c.get("direction")
            )
        for e in root.findall("events/event"):
            spec.events[_int(e.get("id"))] = Element(
                _int(e.get("id")), e.get("name"), conform_of(e)
            )
        return spec

    # -------------------------------------------------------------- device types
    def _load_device_types(self):
        for path in sorted((self.dir / "device_types").glob("*.xml")):
            root = ET.parse(path).getroot()
            if root.tag != "deviceType":
                continue
            cls = root.find("classification")
            clusters = []
            for c in root.findall("clusters/cluster"):
                req = DeviceClusterReq(
                    id=_int(c.get("id")),
                    name=c.get("name"),
                    side=c.get("side", "server"),
                    conform=conform_of(c),
                )
                q = c.find("quality")
                req.singleton = q is not None and q.get("singleton") == "true"
                for f in c.findall("features/feature"):
                    req.features[f.get("code")] = conform_of(f)
                for a in c.findall("attributes/attribute"):
                    req.attributes.append((a.get("name"), conform_of(a)))
                for cmd in c.findall("commands/command"):
                    req.commands.append((cmd.get("name"), conform_of(cmd)))
                for ev in c.findall("events/event"):
                    req.events.append((ev.get("name"), conform_of(ev)))
                clusters.append(req)
            composed = []
            for d in root.findall("composedDeviceTypes/deviceType"):
                composed.append(
                    dict(
                        id=_int(d.get("deviceTypeId")),
                        name=d.get("deviceTypeName"),
                        clusters={
                            _int(c.get("id")): conform_of(c)
                            for c in d.findall("clusterRequirements/cluster")
                        },
                    )
                )
            dt = DeviceTypeSpec(
                id=_int(root.get("id")) if root.get("id") else -1,
                name=root.get("name"),
                revision=_int(root.get("revision")) or 1,
                classification=cls.get("class") if cls is not None else "",
                scope=cls.get("scope") if cls is not None else "",
                superset=cls.get("superset") if cls is not None else None,
                clusters=clusters,
                conditions={
                    c.get("name"): c.get("summary")
                    for c in root.findall("conditions/condition")
                },
                composed=composed,
            )
            if dt.id == -1:
                if root.get("name") == "Base Device Type":
                    self.base_device_type = dt
                continue
            self.device_types[dt.id] = dt

    def cluster_name(self, cid: int) -> str:
        c = self.clusters.get(cid)
        return c.name if c else f"0x{cid:04X}"
