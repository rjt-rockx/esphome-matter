import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path

import esphome.config_validation as cv
from esphome import automation
from esphome.components import light, switch
from esphome.const import CONF_LIGHT_ID

from ..binary_sensors import extra_schema as binary_sensor_schema
from ..const import (
    CONF_MAX_LEVEL,
    CONF_MIN_LEVEL,
    CONF_SWITCH_ID,
    CONF_WITH_FEATURES,
)
from ..fan_binding import CONF_FAN_ID, fan_id_schema, validate_fan_features
from ..generic_switch import apply_switch_defaults, switch_schema_options
from ..lock_valve import extend_device_type_schema
from .. import sensor_extras
from ..matter_climate import climate_schema, validate_climate_features
from ..sensor_mapping import (
    CONF_CURRENT_TYPE,
    CONF_MEASUREMENT_UNITS,
    measurement_units_schema,
    validate_electrical_current_type,
)
from ..util import maybe_empty
from . import espmatter_names
from .attributes import SENSOR_ATTRIBUTES, SensorAttribute
from .clusters import CLUSTERS_BY_ID, CLUSTERS_BY_NAME, Cluster, Feature
from .conformance import Conformance
from .units import percentage

_LOGGER = logging.getLogger(__name__)


# Device types that can be bound to an ESPHome switch through switch_id.
SWITCH_DEVICE_TYPES = ("on_off_plug_in_unit", "on_off_light", "mounted_on_off_control")


# Default for mandatory feature choices of required clusters when the user does
# not select one. Keys are "<device_type>"/"<Cluster>" tuples or a cluster name.
DEFAULT_CHOICE_FEATURES: dict[str | tuple[str, str], str] = {
    "Switch": "MomentarySwitch",
    # A single electrical_sensor node always uses the NodeTopology.
    "PowerTopology": "NodeTopology",
}

# Features that need other features to be enabled; esp-matter rejects them
# otherwise. Each entry is a list of alternatives of which at least one must be
# enabled. The first alternative is added when none is.
FEATURE_DEPENDENCIES: dict[str, tuple[tuple[str, ...], ...]] = {
    "MomentarySwitchRelease": (("MomentarySwitch",),),
    "MomentarySwitchLongPress": (
        ("MomentarySwitch",),
        ("MomentarySwitchRelease", "ActionSwitch"),
    ),
    "MomentarySwitchMultiPress": (
        ("MomentarySwitch",),
        ("MomentarySwitchRelease", "ActionSwitch"),
    ),
    "ActionSwitch": (("MomentarySwitch",),),
}

# Clusters whose esp-matter create() function evaluates every feature in
# config_t::feature_flags (instead of only the choice features), so all of their
# features, including per-feature config like MultiPressMax, are set through the
# feature flags.
FLAG_DRIVEN_CLUSTERS = frozenset({"Switch"})



def _parse_cluster_include(cluster_id: int, data: dict) -> Cluster:
    cluster = CLUSTERS_BY_ID[cluster_id]
    include_conformance = {
        code: Conformance.from_dict(feature_data.get("conformance"))
        for code, feature_data in data.get("features", {}).items()
    }

    def compose(feature: Feature) -> Feature:
        refinement = include_conformance.get(feature.code)
        if refinement is None:
            return feature
        conformance = (
            feature.conformance.compose(refinement)
            if feature.conformance is not None
            else refinement
        )
        return replace(feature, conformance=conformance)

    features = tuple(compose(feature) for feature in cluster.features)

    # TODO: also update attribute, command and event info
    return replace(
        cluster,
        features=features,
        server=data.get("server", False),
        client=data.get("client", False),
        server_locked=data.get("server_locked", False),
        client_locked=data.get("client_locked", False),
    )


@dataclass(frozen=True, slots=True)
class DeviceType:
    id: int
    name: str  # snake_case
    clusters: tuple[Cluster, ...] = ()
    sensor_attributes: tuple[SensorAttribute, ...] = ()

    @classmethod
    def from_dict(cls, device_type_id: int, data: dict):
        clusters = tuple(
            _parse_cluster_include(int(cluster_id), cluster_data)
            for cluster_id, cluster_data in data["clusters"].items()
        )
        sensor_attributes = []
        for cluster in clusters:
            if cluster.server_locked and not cluster.server:
                continue
            for attribute_name, sensor_attribute in SENSOR_ATTRIBUTES.get(
                cluster.name, {}
            ).items():
                sensor_attributes.append(
                    replace(
                        sensor_attribute,
                        cluster=cluster,
                        attribute=cluster.get_attribute(attribute_name),
                    )
                )

        return cls(
            name=data["name"],
            id=device_type_id,
            clusters=clusters,
            sensor_attributes=tuple(sensor_attributes),
        )

    @property
    def namespace(self) -> str:
        """esp_matter::endpoint::<namespace>"""
        namespace = espmatter_names.device_type_namespace(self.name)
        if namespace is None:
            raise ValueError(f"Device type {self.name} is not available in esp-matter")
        return namespace

    @property
    def espm_available(self) -> bool:
        return espmatter_names.device_type_namespace(self.name) is not None

    def config_member(self, cluster: Cluster) -> str:
        """Member of esp_matter::endpoint::<namespace>::config_t that configures the cluster."""
        entry = espmatter_names.device_type_entry(self.name)
        if entry is None:
            # Table out of date (warned about by device_type_namespace).
            return cluster.espm_namespace
        for member, cluster_type in entry["config_members"].items():
            if cluster_type == f"cluster::{cluster.espm_namespace}":
                return member
        raise cv.Invalid(
            f"esp-matter's {self.name} config has no member for the "
            f"{cluster.name} cluster"
        )

    def _check_features_available(self, names, path=None) -> None:
        """Reject features that esp-matter doesn't have in any cluster of this device type."""
        for name in names:
            candidates = [f for f in self.get_features() if f.name == name]
            if candidates and not any(f.espm_available for f in candidates):
                raise cv.Invalid(
                    f"{self.name}: {candidates[0].unavailable_message()}",
                    path=[CONF_WITH_FEATURES] if path is None else path,
                )

    def _validate_feature_name(self, value):
        """Accepts the snake_case key, CamelCase name or spec name; returns the CamelCase name."""
        features = self.get_features()
        by_key = {feature.name: feature for feature in features}
        by_key.update({feature.conf_key: feature for feature in features})
        by_key.update({feature.spec_name: feature for feature in features if feature.spec_name})
        value = cv.one_of(*by_key)(value)
        feature = by_key[value]
        self._check_features_available([feature.name])
        return feature.name

    @property
    def conf_key(self) -> str:
        return self.name

    def has_required_cluster(self, name: str) -> bool:
        return any(c.name == name and c.required for c in self.server_clusters)

    @property
    def server_clusters(self) -> tuple[Cluster, ...]:
        return tuple(
            cluster
            for cluster in self.clusters
            if cluster.server or not cluster.server_locked
        )

    @property
    def client_clusters(self) -> tuple[Cluster, ...]:
        return tuple(
            cluster
            for cluster in self.clusters
            if cluster.client or not cluster.client_locked
        )

    def get_features(self) -> set[Feature]:
        """Get all features that the clusters of this device type supports."""
        features = set()
        for cluster in self.server_clusters:
            features.update(cluster.features)
        return features

    def configured_server_clusters(self, config: dict) -> set[Cluster]:
        clusters = {cluster for cluster in self.server_clusters if cluster.required}
        for sensor_attribute in self.sensor_attributes:
            if config.get(sensor_attribute.conf_key) is not None:
                clusters.add(CLUSTERS_BY_NAME[sensor_attribute.cluster.name])
        return clusters

    def _has_feature_binding(self, config: dict) -> bool:
        """True if an entity binding in the config selects feature choices later."""
        keys = {sensor_attribute.conf_key for sensor_attribute in self.sensor_attributes}
        keys.update(("cover_id", "climate_id"))
        return any(config.get(key) is not None for key in keys)

    def _validate_features(self, config: dict) -> dict:
        """Resolve mandatory feature choices and feature dependencies.

        Clusters that a device type requires may define a choice between
        features of which a minimum number must be enabled (e.g. the Switch
        cluster requires exactly one of LatchingSwitch/MomentarySwitch).
        esp-matter silently refuses to create such a cluster when the choice
        is missing, so it is resolved here: a default from
        DEFAULT_CHOICE_FEATURES is applied when the user made no selection.
        Choices without a default are left alone because bindings such as
        climate_id, cover_id or a bound binary sensor select them later.
        """
        if (
            self.name == "window_covering"
            and "end_product_type" in config
            and "cover_id" not in config
        ):
            raise cv.Invalid("end_product_type requires cover_id")
        if self.has_required_cluster("Switch"):
            config = apply_switch_defaults(config)
        enabled = list(config.get(CONF_WITH_FEATURES, ()))

        # Add the features that other enabled features depend on.
        for name in list(enabled):
            for alternatives in FEATURE_DEPENDENCIES.get(name, ()):
                if not any(alternative in enabled for alternative in alternatives):
                    enabled.append(alternatives[0])

        for cluster in self.server_clusters:
            if not cluster.required:
                continue
            for choice, choice_features in cluster.choice_groups().items():
                names = [feature.name for feature in choice_features]
                selected = [name for name in enabled if name in names]
                if choice.max is not None and len(set(selected)) > choice.max:
                    raise cv.Invalid(
                        f"{self.name}: cluster {cluster.name} allows at most "
                        f"{choice.max} of these features: {', '.join(names)} "
                        f"(got {', '.join(sorted(set(selected)))})",
                        path=[CONF_WITH_FEATURES],
                    )
                if len(set(selected)) >= choice.min:
                    continue
                default = DEFAULT_CHOICE_FEATURES.get((self.name, cluster.name))
                default = default or DEFAULT_CHOICE_FEATURES.get(cluster.name)
                if default not in names:
                    # No sensible default: a binding (climate_id, cover_id, a bound
                    # binary sensor, ...) picks the feature while generating code.
                    # Without a binding esp-matter would refuse to create the cluster.
                    if not self._has_feature_binding(config):
                        raise cv.Invalid(
                            f"{self.name}: cluster {cluster.name} requires "
                            f"{'exactly' if choice.max == choice.min else 'at least'} "
                            f"{choice.min} of these features to be enabled: "
                            f"{', '.join(names)}. Select one with `with_features: [...]` "
                            "or bind an ESPHome entity.",
                            path=[CONF_WITH_FEATURES],
                        )
                    continue
                enabled.append(default)

        # Also covers features enabled implicitly (sensor attributes, dependencies).
        self._check_features_available(enabled)
        if enabled:
            config = dict(config)
            config[CONF_WITH_FEATURES] = list(dict.fromkeys(enabled))
        return config

    @property
    def schema_key(self):
        return cv.Optional(self.conf_key)

    def _schema(self):
        schema = {
            cv.Optional(sensor_attribute.conf_key): cv.use_id(
                sensor_attribute.sensor_type
            )
            for sensor_attribute in self.sensor_attributes
        }

        if units_schema := measurement_units_schema(self.sensor_attributes):
            schema[cv.Optional(CONF_MEASUREMENT_UNITS)] = units_schema

        if features := self.get_features():
            schema[cv.Optional(CONF_WITH_FEATURES)] = cv.ensure_list(
                self._validate_feature_name
            )

        schema.update(binary_sensor_schema(self))
        if self.has_required_cluster("Switch"):
            schema.update(switch_schema_options())

        # TODO: replace with something better
        if self.name.endswith("light"):
            schema[cv.Optional(CONF_LIGHT_ID)] = cv.use_id(light.LightState)

        if self.name in SWITCH_DEVICE_TYPES:
            schema[cv.Optional(CONF_SWITCH_ID)] = cv.use_id(switch.Switch)
        if self.name == "fan":
            schema[cv.Optional(CONF_FAN_ID)] = fan_id_schema()
        if self.name == "window_covering":
            from ..covers import schema_options

            schema.update(schema_options())
        schema.update(climate_schema(self.name))

        if self.name.endswith("light") and any(
            cluster.name == "LevelControl" and cluster.required
            for cluster in self.server_clusters
        ):
            level = cv.All(percentage(), cv.int_range(min=1, max=254))
            schema[cv.Optional(CONF_MIN_LEVEL, default=1)] = level
            schema[cv.Optional(CONF_MAX_LEVEL, default=254)] = level

        extend_device_type_schema(self.name, schema)
        sensor_extras.extend_schema(self, schema)

        # If a device type is a simple sensor with only a single sensor attribute the config may be simplified from;
        #   temperature_sensor:
        #     temperature: sensor_id
        # to;
        #   temperature_sensor: sensor_id
        if len(self.sensor_attributes) == 1:
            sensor_attribute = self.sensor_attributes[0]
            schema = automation.maybe_conf(sensor_attribute.conf_key, schema)

        return schema

    def _validate_entity_ids(self, config):
        if CONF_LIGHT_ID in config and CONF_SWITCH_ID in config:
            raise cv.Invalid(
                f"{CONF_LIGHT_ID} and {CONF_SWITCH_ID} cannot be used together"
            )
        return config

    def schema(self):
        # TODO: only maybe_empty if there are no clusters or features for which a mandatory choice must be made.
        return cv.All(
            maybe_empty(self._schema()),
            lambda config: sensor_extras.validate_device_config(self, config),
            self._validate_features,
            self._validate_entity_ids,
            validate_fan_features,
            validate_climate_features,
        )


class ElectricalSensor(DeviceType):
    """The electrical_sensor needs at least one of its measurement clusters.

    These are created by esp-matter itself through the with_*() builders of its
    config, see sensor_mapping.configure_electrical_sensor().
    """

    def _schema(self):
        schema = DeviceType._schema(self)
        schema[cv.Optional(CONF_CURRENT_TYPE, default="ac")] = cv.one_of(
            "ac", "dc", lower=True
        )
        return schema

    def _validate_features(self, config: dict) -> dict:
        return validate_electrical_current_type(
            self, DeviceType._validate_features(self, config)
        )


DEVICE_TYPE_OVERRIDES = {
    "electrical_sensor": ElectricalSensor,
}


def _load_device_types(
    device_types_file: Path = Path(__file__).resolve().parent / "device_types.json",
) -> tuple[DeviceType, ...]:
    device_types: list[DeviceType] = []

    with open(device_types_file, "r") as file:
        contents = json.load(file)

    for device_type_id, device_type_data in contents.items():
        device_type_class = DEVICE_TYPE_OVERRIDES.get(
            device_type_data["name"], DeviceType
        )
        device_types.append(
            device_type_class.from_dict(int(device_type_id), device_type_data)
        )

    return tuple(device_types)


DEVICE_TYPES: tuple[DeviceType, ...] = _load_device_types()
DEVICE_TYPES_BY_ID: dict[int, DeviceType] = {
    device_type.id: device_type for device_type in DEVICE_TYPES
}
DEVICE_TYPES_BY_CONF_KEY: dict[str, DeviceType] = {
    device_type.conf_key: device_type for device_type in DEVICE_TYPES
}
