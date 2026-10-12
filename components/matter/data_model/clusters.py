import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path

import esphome.config_validation as cv

from ..const import CONF_WITH_FEATURES
from ..util import snake_case
from . import espmatter_names
from .attributes import Attribute
from .commands import Command
from .conformance import Conformance
from .events import Event

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Feature:
    bit: int
    code: str
    name: str  # CamelCase
    conformance: Conformance | None = field(default=None, compare=False, hash=False)
    # Name as in the Matter spec (may contain spaces); key in espmatter_names.json.
    spec_name: str = ""
    # Resolved from espmatter_names.json by Cluster.from_dict.
    espm_feature_namespace: str | None = None
    # False if esp-matter has no such feature. Such features must be rejected at
    # config time; using their namespace raises.
    espm_available: bool = True
    cluster_name: str = ""

    @classmethod
    def from_dict(cls, bit: int, data: dict):
        name = data["name"].replace("/", "").replace(" ", "").replace("-", "")
        return cls(
            bit=bit,
            code=data["code"],
            name=name,
            conformance=Conformance.from_dict(data.get("conformance")),
            spec_name=data["name"],
        )

    @property
    def namespace(self) -> str:
        """Feature name in esp_matter::cluster::<cluster>::feature::<feature> namespace."""
        if not self.espm_available:
            raise ValueError(
                f"Feature {self.name} of cluster {self.cluster_name} is not "
                "available in esp-matter"
            )
        if self.espm_feature_namespace is None:
            # Not resolved against the esp-matter table.
            return espmatter_names._fallback_namespace(self.spec_name or self.name)
        return self.espm_feature_namespace

    def unavailable_message(self) -> str:
        return (
            f"Feature {self.name} of the Matter {self.cluster_name} cluster is not "
            "supported by esp-matter (1.6) and can't be enabled"
        )

    @property
    def conf_key(self):
        return snake_case(self.name)


@dataclass(frozen=True, slots=True)
class Cluster:
    # ----------------------------------- #
    # Parsed directly from clusters.json  #
    # ----------------------------------- #
    id: int
    # Name with spaces and special characters such as "/"
    _name: str
    # CamelCase name that's used almost everywhere in esphome_matter
    name: str
    revision: int
    features: tuple[Feature, ...]
    attributes: tuple[Attribute, ...]
    commands: tuple[Command, ...]
    events: tuple[Event, ...]
    # ----------------------------------- #
    # Derived attributes                  #
    # ----------------------------------- #
    conf_key: str
    sdkconfig_option: str
    # connectedhomeip fully qualified name. e.g.: chip::app::Clusters::TemperatureMeasurementCluster
    chip_fqn: str | None
    # connectedhomeip include. e.g.: #include <app/clusters/temperature-measure-server/TemperatureMeasurementCluster.h>
    chip_include: str | None
    # esp_matter class namespace. e.g.:
    # None if esp-matter has no such cluster (see espm_namespace).
    espm_cluster_namespace: str | None
    # ----------------------------------- #
    # Set by DeviceType                   #
    # ----------------------------------- #
    server: bool = False
    client: bool = False
    server_locked: bool = False
    client_locked: bool = False

    @property
    def required(self) -> bool:
        return self.server and self.server_locked

    @property
    def espm_available(self) -> bool:
        """Whether esp-matter 1.6 has this cluster."""
        return self.espm_cluster_namespace is not None

    @property
    def espm_namespace(self) -> str:
        """Namespace in esp_matter::cluster::<namespace>."""
        if self.espm_cluster_namespace is None:
            raise ValueError(f"Cluster {self.name} is not available in esp-matter")
        return self.espm_cluster_namespace

    def choice_groups(self) -> dict:
        """Features grouped by the conformance choice they belong to (Choice -> features)."""
        groups: dict = {}
        for feature in self.features:
            if feature.conformance is not None and feature.conformance.choice:
                groups.setdefault(feature.conformance.choice, []).append(feature)
        return groups

    @classmethod
    def from_dict(cls, data: dict):
        name = data["name"]

        camel_case_name = (
            name.replace("/", "").replace(" ", "").replace("-", "").replace(".", "")
        )
        sdkconfig_option = data.get(
            "sdkconfig_option",
            f"CONFIG_SUPPORT_{
                name.replace(' ', '_')
                .replace('/', '_')
                .replace('.', '_')
                .replace('-', '')
                .upper()
            }_CLUSTER",
        )

        def resolve(feature: Feature) -> Feature:
            ns = espmatter_names.feature_namespace(camel_case_name, feature.spec_name)
            return replace(
                feature,
                espm_feature_namespace=ns,
                espm_available=ns is not None,
                cluster_name=camel_case_name,
            )

        _features = []
        for bit, feature_data in data.get("features", {}).items():
            _features.append(resolve(Feature.from_dict(int(bit), feature_data)))

        chip_class = f"{camel_case_name}Cluster"
        cluster_path = snake_case(camel_case_name).replace("_", "-")
        chip_fqn = data.get("chip_fqn", f"chip::app::Clusters::{chip_class}")
        chip_include = data.get(
            "chip_include",
            f"#include <app/clusters/{cluster_path}-server/{chip_class}.h>",
        )

        fallback_namespace = data.get(
            "esp_matter_namespace",
            name.replace("/", "_")
            .replace(" ", "_")
            .replace("-", "")
            .replace(".", "")
            .lower(),
        )
        if fallback_namespace == "switch":
            fallback_namespace = "switch_cluster"
        espm_namespace = espmatter_names.cluster_namespace(
            camel_case_name, fallback_namespace
        )

        return cls(
            id=data["id"],
            _name=name,
            name=camel_case_name,
            conf_key=snake_case(camel_case_name),
            # Some lack a revision. Assuming it's 1...
            revision=data.get("revision", 1),
            features=tuple(_features),
            attributes=tuple(
                Attribute.from_dict(int(attribute_id), attribute)
                for attribute_id, attribute in data.get("attributes", {}).items()
            ),
            commands=tuple(
                Command.from_dict(command_name, command)
                for command_name, command in data.get("commands", {}).items()
            ),
            events=tuple(
                Event.from_dict(event_name, event)
                for event_name, event in data.get("events", {}).items()
            ),
            sdkconfig_option=sdkconfig_option,
            chip_fqn=chip_fqn,
            chip_include=chip_include,
            espm_cluster_namespace=espm_namespace,
        )

    def get_feature(self, name_or_code: str) -> Feature | None:
        for feature in self.features:
            if name_or_code in (feature.name, feature.code, feature.conf_key):
                return feature
        return None

    def get_attribute(self, name_or_id: str | int) -> Attribute | None:
        for attribute in self.attributes:
            if not attribute.server:
                continue
            if name_or_id in (attribute.name, attribute.conf_key, attribute.id):
                return attribute
        return None

    def get_command(self, name_or_id: str | int) -> Command | None:
        for command in self.commands:
            if name_or_id in (command.name, command.conf_key, command.id):
                return command
        return None

    def get_event(self, name: str) -> Event | None:
        for event in self.events:
            if name in (event.name, event.conf_key, event.id):
                return event
        return None

    @property
    def schema_key(self):
        return cv.Optional(self.conf_key)

    def schema(self):
        schema = {}
        if self.features:
            # Features must be given in snake_case. e.g.: "average_measurement"
            schema[cv.Optional(CONF_WITH_FEATURES, default=list)] = cv.ensure_list(
                cv.one_of(*(feature.conf_key for feature in self.features))
            )
        # Entity bindings of some clusters (fan_control, filter monitoring).
        from .. import cluster_options

        schema.update(cluster_options.extra_schema(self.conf_key))
        return schema


def _load_clusters(
    clusters_file: Path = Path(__file__).resolve().parent / "clusters.json",
) -> tuple[Cluster, ...]:
    clusters: list[Cluster] = []
    with open(clusters_file, "r") as file:
        contents = json.load(file)
    for cluster_id, cluster_data in contents.items():
        clusters.append(Cluster.from_dict({"id": int(cluster_id), **cluster_data}))
    return tuple(clusters)


CLUSTERS: tuple[Cluster, ...] = _load_clusters()
CLUSTERS_BY_ID: dict[int, Cluster] = {cluster.id: cluster for cluster in CLUSTERS}
CLUSTERS_BY_NAME: dict[str, Cluster] = {cluster.name: cluster for cluster in CLUSTERS}
CLUSTERS_BY_CONF_KEY: dict[str, Cluster] = {
    cluster.conf_key: cluster for cluster in CLUSTERS
}
