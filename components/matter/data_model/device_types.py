import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path

import esphome.config_validation as cv
from esphome import automation
from esphome.components import light, switch
from esphome.const import CONF_LIGHT_ID

from ..binary_sensors import extra_schema as binary_sensor_schema
from ..const import CONF_FEATURES, CONF_MAX_LEVEL, CONF_MIN_LEVEL, CONF_SWITCH_ID
from ..fan_binding import CONF_FAN_ID, fan_id_schema, validate_fan_features
from ..matter_climate import climate_schema, validate_climate_features
from ..lock_valve import extend_device_type_schema
from ..generic_switch import apply_switch_defaults, switch_schema_options
from ..sensor_mapping import (
    CONF_CURRENT_TYPE,
    CONF_MEASUREMENT_UNITS,
    measurement_units_schema,
    validate_electrical_current_type,
)
from ..util import maybe_empty
from .attributes import SENSOR_ATTRIBUTES, SensorAttribute
from .clusters import CLUSTERS_BY_ID, CLUSTERS_BY_NAME, Cluster, Feature
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
# Clusters whose features are set by an entity binding when the given option is
# configured; the mandatory choice check does not apply to them.
BINDING_MANAGED_CLUSTERS = {
    "WindowCovering": ("cover_id",),
    "Thermostat": ("climate_id",),
    # The sensor type follows the device_class of the bound binary_sensor.
    "OccupancySensing": ("occupancy",),
}

FLAG_DRIVEN_CLUSTERS = frozenset({"Switch"})


def _parse_cluster_include(data: dict) -> Cluster:
    cluster = CLUSTERS_BY_ID[data["id"]]
    required = data.get("required", False)
    enabled_features = data.get("features", ())

    features = []
    choice_features = []
    for feature in cluster.features:
        if feature.code in enabled_features:
            feature = replace(feature, enabled=True)
        features.append(feature)
    for choice in cluster.choice_features:
        new_choice_features = []
        for feature in choice.features:
            if feature.code in enabled_features:
                feature = replace(feature, enabled=True)
            new_choice_features.append(feature)
        # Remove choice features if device type already solves the choice by enabling any of the choice features.
        if choice.max != 1 or not any(
            feature.enabled for feature in new_choice_features
        ):
            choice_features.append(replace(choice, features=tuple(new_choice_features)))
        else:
            features.extend(new_choice_features)

    # TODO: also update attribute and command info
    return replace(
        cluster,
        required=required,
        features=tuple(features),
        choice_features=tuple(choice_features),
    )


@dataclass(frozen=True, slots=True)
class DeviceType:
    id: int
    name: str  # snake_case
    server_clusters: tuple[Cluster, ...] = ()
    sensor_attributes: tuple[SensorAttribute, ...] = ()

    @classmethod
    def from_dict(cls, data: dict):
        server_clusters = tuple(
            [_parse_cluster_include(c) for c in data["server_clusters"]]
        )
        sensor_attributes = []
        for cluster in server_clusters:
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
            id=data["id"],
            server_clusters=server_clusters,
            sensor_attributes=tuple(sensor_attributes),
        )

    @property
    def namespace(self) -> str:
        """esp_matter::endpoints::<device_type>"""
        return self.name

    @property
    def conf_key(self) -> str:
        return self.name

    def has_required_cluster(self, name: str) -> bool:
        return any(c.name == name and c.required for c in self.server_clusters)

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

    # TODO: fix this mess...
    # def implicit_features(self, config: dict) -> set[str]:
    #     features = set()
    #     configured_cluster_ids = {
    #         cluster.id for cluster in self.configured_server_clusters(config)
    #     }
    #     for cluster in self.server_clusters:
    #         if cluster.id not in configured_cluster_ids:
    #             continue
    #         features.update(
    #             feature.name
    #             for feature in cluster.features
    #             if feature.code in cluster.enabled_features
    #         )
    #     for sensor_attribute in self.sensor_attributes:
    #         if config.get(sensor_attribute.conf_key) is not None:
    #             features.update(sensor_attribute.features)
    #     return features

    def _validate_features(self, config: dict) -> dict:
        """Resolve mandatory feature choices and feature dependencies.

        Clusters that a device type requires may define a choice between
        features of which a minimum number must be enabled (e.g. the Switch
        cluster requires exactly one of LatchingSwitch/MomentarySwitch).
        esp-matter silently refuses to create such a cluster when the choice
        is missing, so it is resolved here: a default from
        DEFAULT_CHOICE_FEATURES is applied when the user made no selection,
        and an error is raised when there is no sensible default.
        """
        if (
            self.name == "window_covering"
            and "end_product_type" in config
            and "cover_id" not in config
        ):
            raise cv.Invalid("end_product_type requires cover_id")
        if self.has_required_cluster("Switch"):
            config = apply_switch_defaults(config)
        enabled = list(config.get(CONF_FEATURES, ()))
        for sensor_attribute in self.sensor_attributes:
            if config.get(sensor_attribute.conf_key) is not None:
                enabled.extend(sensor_attribute.features)

        # Add the features that other enabled features depend on.
        for name in list(enabled):
            for alternatives in FEATURE_DEPENDENCIES.get(name, ()):
                if not any(alternative in enabled for alternative in alternatives):
                    enabled.append(alternatives[0])

        for cluster in self.server_clusters:
            if not cluster.required:
                continue
            if any(
                config.get(key) is not None
                for key in BINDING_MANAGED_CLUSTERS.get(cluster.name, ())
            ):
                # The feature set of this cluster is derived from the bound
                # ESPHome entity (cover/climate/binary_sensor) at code generation
                # time or in final validation.
                continue
            for choice in cluster.choice_features:
                names = [feature.name for feature in choice.features]
                selected = [name for name in enabled if name in names]
                if choice.max is not None and len(set(selected)) > choice.max:
                    raise cv.Invalid(
                        f"{self.name}: cluster {cluster.name} allows at most "
                        f"{choice.max} of these features: {', '.join(names)} "
                        f"(got {', '.join(sorted(set(selected)))})",
                        path=[CONF_FEATURES],
                    )
                if len(set(selected)) >= choice.min:
                    continue
                default = DEFAULT_CHOICE_FEATURES.get((self.name, cluster.name))
                default = default or DEFAULT_CHOICE_FEATURES.get(cluster.name)
                if default not in names:
                    raise cv.Invalid(
                        f"{self.name}: cluster {cluster.name} requires "
                        f"{'exactly' if choice.max == choice.min else 'at least'} "
                        f"{choice.min} of these features to be enabled: "
                        f"{', '.join(names)}. Select one with `features: [...]`.",
                        path=[CONF_FEATURES],
                    )
                enabled.append(default)

        if enabled:
            config = dict(config)
            config[CONF_FEATURES] = list(dict.fromkeys(enabled))
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
            schema[cv.Optional(CONF_FEATURES)] = cv.ensure_list(
                cv.one_of(*(feature.name for feature in features))
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

    for device_type_data in contents:
        device_type_class = DEVICE_TYPE_OVERRIDES.get(
            device_type_data["name"], DeviceType
        )
        device_types.append(device_type_class.from_dict(device_type_data))

    return tuple(device_types)


DEVICE_TYPES: tuple[DeviceType, ...] = _load_device_types()
DEVICE_TYPES_BY_ID: dict[int, DeviceType] = {
    device_type.id: device_type for device_type in DEVICE_TYPES
}
DEVICE_TYPES_BY_CONF_KEY: dict[str, DeviceType] = {
    device_type.conf_key: device_type for device_type in DEVICE_TYPES
}
