import logging
from collections import defaultdict
from dataclasses import dataclass, field

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome import automation
from esphome.components import light
from esphome.components.binary_sensor import BinarySensor
from esphome.components.esp32 import add_idf_sdkconfig_option
from esphome.components.sensor import Sensor
from esphome.config import Config
from esphome.const import CONF_ID, CONF_LIGHT_ID, CONF_RESTORE_MODE, CONF_TRIGGER_ID
from esphome.core import CORE, ID, HexInt
from esphome.types import ConfigType

from . import binary_sensors
from . import conformance
from .actions import get_attribute_from_config, get_event_from_config
from .const import *
from .covers import WINDOW_COVERING
from .covers import config_init as cover_config_init
from .covers import config_lines as cover_config_lines
from .data_model.attributes import Attribute, SensorAttribute, attribute_value_type
from .data_model.clusters import CLUSTERS, CLUSTERS_BY_NAME, Cluster
from .data_model.conformance import (
    ConformanceDisposition,
    ConformanceError,
    resolve_feature_requirements,
)
from .data_model.device_types import (
    DEVICE_TYPES,
    DEVICE_TYPES_BY_CONF_KEY,
    DEVICE_TYPES_BY_ID,
    FLAG_DRIVEN_CLUSTERS,
    DeviceType,
)
from . import fan_binding, layout_dump
from .matter_climate import (
    CLIMATE_INCLUDE,
    CONF_CLIMATE_ID,
    config_lines as climate_config_lines,
    post_create_lines as climate_post_create_lines,
    prefer_cooling as climate_prefer_cooling,
)
from .lock_valve import device_config_lines, register_entity
from .generic_switch import multi_press_max_line, register_switch_binding
from .data_model.units import MEASUREMENT_UNITS
from .sensor_mapping import (
    CONF_BATTERY,
    CONF_MEASUREMENT_UNITS,
    battery_sensor_attribute,
    configure_electrical_sensor,
    converter_expression,
    electrical_config_lines,
    optional_attribute_lines,
    plan_sensor,
    sensor_unit_info,
    validate_battery_binding,
)
from .data_model.events import Event
from .types import MatterAttributeTrigger, MatterEndpointRef
from .util import iter_matter_actions, maybe_empty, snake_case

_LOGGER = logging.getLogger(__name__)


def _restore_mode_warning(
    matter_config: dict, full_config: Config, id_key: str, kind: str
):
    for endpoint_config in matter_config.get(CONF_ENDPOINTS, {}).values():
        for device_config in endpoint_config.values():
            if not isinstance(device_config, dict):
                continue
            entity_id = device_config.get(id_key)
            if entity_id is None:
                continue
            try:
                entity_path = full_config.get_path_for_id(entity_id)[:-1]
                entity_config = full_config.get_config_for_path(entity_path)
            except KeyError:
                continue
            restore_mode = entity_config.get(CONF_RESTORE_MODE)
            if restore_mode is not None and restore_mode != "ALWAYS_OFF":
                _LOGGER.warning(
                    "%s '%s' is exposed through Matter with restore mode %s and should use "
                    "restore_mode: ALWAYS_OFF so Matter can restore its power-on state",
                    kind,
                    entity_id,
                    restore_mode,
                )


def light_restore_warning(matter_config: dict, full_config: Config):
    _restore_mode_warning(matter_config, full_config, CONF_LIGHT_ID, "Light")


def switch_restore_warning(matter_config: dict, full_config: Config):
    _restore_mode_warning(matter_config, full_config, CONF_SWITCH_ID, "Switch")


def _on_attribute_schema():
    options = {}
    for cluster in CLUSTERS:
        attributes = {}
        for attribute in cluster.attributes:
            if not attribute.server:
                continue
            if attribute.name is None:
                continue
            value_type = attribute_value_type(attribute)
            trigger_schema = {}
            if value_type is not None:
                trigger_schema[cv.GenerateID(CONF_TRIGGER_ID)] = cv.declare_id(
                    MatterAttributeTrigger.template(value_type)
                )
            attribute_key = snake_case(attribute.name)
            automation_schema = automation.validate_automation(trigger_schema)
            attributes[cv.Optional(attribute_key)] = automation_schema
            options[cv.Optional(f"{snake_case(cluster.name)}.{attribute_key}")] = (
                automation_schema
            )
        if attributes:
            options[cv.Optional(snake_case(cluster.name))] = cv.Schema(attributes)
    return cv.Schema(options)


def _validate_on_attribute_forms(config):
    configured = config.get(CONF_ON_ATTRIBUTE, {})
    for cluster in CLUSTERS:
        cluster_key = snake_case(cluster.name)
        nested = configured.get(cluster_key, {})
        for attribute in cluster.attributes:
            if not attribute.server:
                continue
            if attribute.name is None:
                continue
            attribute_key = snake_case(attribute.name)
            short_key = f"{cluster_key}.{attribute_key}"
            if attribute_key in nested and short_key in configured:
                raise cv.Invalid(
                    f"Matter attribute {short_key} cannot be configured in both "
                    "nested and short form"
                )
    return config


ENDPOINT_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(MatterEndpointRef),
            cv.Optional(CONF_CLUSTERS, default=dict): {
                cluster.schema_key: maybe_empty(cluster.schema())
                for cluster in CLUSTERS
            },
            # TODO: move on_attribute to clusters
            cv.Optional(CONF_ON_ATTRIBUTE): _on_attribute_schema(),
            # Battery level sensor (%) exposed through a Power Source cluster on this endpoint.
            cv.Optional(CONF_BATTERY): cv.use_id(Sensor),
        }
        | conformance.SCHEMA_OPTIONS
        | {device_type.schema_key: device_type.schema() for device_type in DEVICE_TYPES}
    ),
    _validate_on_attribute_forms,
    validate_battery_binding,
)


@dataclass
class _ClusterConfig:
    # Whether or not the cluster is already created by a device type config.
    created: bool = False
    # Keys are feature names.
    enabled_features: dict[str, bool] = field(default_factory=dict)


class Endpoint:
    def __init__(
        self,
        endpoint_id: int,
        config: dict,
        plan: conformance.EndpointPlan | None = None,
    ):
        self._endpoint_id = endpoint_id
        self._config = config
        self.usages: list[tuple[Cluster, Attribute | Event]] = []
        self.plan = plan or conformance.EndpointPlan()

        self.enabled_sdkconfig_options: set[str] = set()
        self.global_includes: set[str] = set()
        self._cluster_configs: defaultdict[str, _ClusterConfig] = defaultdict(
            _ClusterConfig
        )
        self._device_types: list[DeviceType] = []
        self._light_variables = {}
        self._fan_variable = None
        self._climate_variables = {}
        # (Cluster, Attribute) of sensor attributes that esp-matter does not create itself.
        self._optional_attributes: list[tuple[Cluster, Attribute]] = []
        self._electrical_power_features: list[str] = []

    @property
    def endpoint_id(self) -> int:
        return self._endpoint_id

    @property
    def config(self) -> dict:
        return self._config

    def register_usage(self, cluster: Cluster, usage: Attribute | Event) -> None:
        self.usages.append((cluster, usage))

    def _cluster_state(self, cluster: Cluster):
        enabled_codes = set()
        cluster_present = False
        device_features = []

        explicit_cluster_config = self._config[CONF_CLUSTERS].get(
            snake_case(cluster.name)
        )
        if explicit_cluster_config is not None:
            cluster_present = True
            for feature_name in explicit_cluster_config.get(CONF_WITH_FEATURES, ()):
                feature = cluster.get_feature(feature_name)
                if feature is not None:
                    enabled_codes.add(feature.code)

        for config_key, device_config in self._config.items():
            device_type = DEVICE_TYPES_BY_CONF_KEY.get(config_key)
            if device_type is None:
                continue
            for device_cluster in device_type.server_clusters:
                if device_cluster.name != cluster.name:
                    continue
                cluster_present |= device_cluster.required
                device_features.extend(device_cluster.features)
            for feature_name in device_config.get(CONF_WITH_FEATURES, ()):
                feature = cluster.get_feature(feature_name)
                if feature is not None:
                    enabled_codes.add(feature.code)

        # A mandatory feature can make another conditional feature mandatory, so
        # evaluate the composed cluster/device-type conformance to a fixed point.
        changed = True
        while changed:
            changed = False
            for feature in device_features:
                if feature.conformance is None or feature.code in enabled_codes:
                    continue
                if (
                    feature.conformance.disposition(enabled_codes)
                    is ConformanceDisposition.MANDATORY
                ):
                    enabled_codes.add(feature.code)
                    changed = True

        disallowed_codes = {
            feature.code
            for feature in device_features
            if feature.conformance is not None
            and feature.conformance.disposition(enabled_codes)
            is ConformanceDisposition.DISALLOWED
        }

        return cluster_present, enabled_codes, disallowed_codes

    def _device_type_names(self) -> list[str]:
        return [
            key
            for key in self._config
            if key in DEVICE_TYPES_BY_CONF_KEY
        ]

    def resolve(self) -> None:
        usages_by_cluster = defaultdict(list)
        clusters_by_id = {}
        for cluster, usage in self.usages:
            clusters_by_id[cluster.id] = cluster
            if usage.conformance is not None:
                usages_by_cluster[cluster.id].append(usage)

        for cluster_name in self._config[CONF_CLUSTERS]:
            cluster = CLUSTERS_BY_NAME[cluster_name]
            clusters_by_id[cluster.id] = cluster

        for config_key, device_config in self._config.items():
            device_type = DEVICE_TYPES_BY_CONF_KEY.get(config_key)
            if device_type is None:
                continue
            configured_features = device_config.get(CONF_WITH_FEATURES, ())
            for cluster in device_type.server_clusters:
                if cluster.required or any(
                    cluster.get_feature(feature_name) is not None
                    for feature_name in configured_features
                ):
                    clusters_by_id[cluster.id] = cluster

        for cluster_id, cluster in clusters_by_id.items():
            usages = usages_by_cluster[cluster_id]
            cluster_present, enabled_codes, disallowed_codes = self._cluster_state(
                cluster
            )
            device_types = self._device_type_names()
            where = f"{cluster.name} cluster on endpoint {self._endpoint_id}" + (
                f" ({', '.join(device_types)})" if device_types else ""
            )
            if not cluster_present:
                usage_names = ", ".join(usage.name for usage in usages)
                key = snake_case(cluster.name)
                if usage_names:
                    reason = f"required by: {usage_names}"
                else:
                    features = ", ".join(
                        f.conf_key
                        for f in cluster.features
                        if f.code in enabled_codes
                    )
                    reason = f"needed by the selected with_features: {features}"
                raise cv.Invalid(
                    f"Matter endpoint {self._endpoint_id} does not contain the "
                    f"{cluster.name} cluster ({reason}). The device type"
                    f"{'s' if len(device_types) != 1 else ''} "
                    f"{', '.join(device_types) or '(none)'} does not require it; "
                    f"add it with `clusters: {{{key}: ...}}` on the endpoint"
                )

            feature_conformance = {
                feature.code: feature.conformance for feature in cluster.features
            }
            try:
                inferred_codes = resolve_feature_requirements(
                    [usage.conformance for usage in usages],
                    feature_conformance,
                    enabled_codes,
                    context=where,
                )
            except ConformanceError as err:
                usage_names = ", ".join(usage.name for usage in usages)
                raise cv.Invalid(
                    f"Cannot resolve features for {err}"
                    + (f" (from {usage_names})" if usage_names else "")
                ) from err

            invalid_codes = (enabled_codes | inferred_codes) & disallowed_codes
            if invalid_codes:
                names = ", ".join(
                    cluster.get_feature(code).conf_key for code in sorted(invalid_codes)
                )
                raise cv.Invalid(
                    f"Matter elements on endpoint {self._endpoint_id} require "
                    f"features disallowed by its device type: {names}"
                )

            resulting_codes = enabled_codes | inferred_codes
            choices = defaultdict(list)
            for feature in cluster.features:
                if feature.conformance is not None and feature.conformance.choice:
                    choices[feature.conformance.choice].append(feature)
            for choice, features in choices.items():
                selected = [
                    feature.conf_key
                    for feature in features
                    if feature.code in resulting_codes
                ]
                if choice.max is not None and len(selected) > choice.max:
                    raise cv.Invalid(
                        f"Matter endpoint {self._endpoint_id} selects conflicting "
                        f"{cluster.name} features: {', '.join(selected)}"
                    )

            cluster_config = self._cluster_configs[cluster.name]
            for code in inferred_codes:
                cluster_config.enabled_features[cluster.get_feature(code).name] = True

    async def register(self, var):
        """Registers an endpoint using the register_endpoint function in matter_component.h.

        Using ESPHome codegen, a function is built and registered that adds device types and clusters to an endpoint.
        These functions are called just before Matter is started.

        Sadly, device type and cluster creation is quite complicated and not easily generalizable for all device types.
        For example, most optional clusters must be created after the device type has been created. However, the
        Electrical Sensor is an exception to this rule. The matter spec defines that this device type must have at least
        one of the "ElectricalEnergyMeasurement" or "ElectricalPowerMeasurement" clusters. esp-matter enforces this
        by adding a "with_clusters" argument to the electrical_sensor config.

        Cluster creation also often requires a custom config to be created successfully. This config is either passed
        directly to the create function of the cluster or to the device type config if the cluster is mandatory.

        Because of all of these complications, the endpoint creation process is a bit of a mess now. The mandatory
        clusters of a device type are always created by esp-matter (except for the binding cluster...).
        The same is true for cluster attributes. Mandatory attributes are always created, some optional attributes are
        created through config options, and some are created after the cluster.
        This whole process could have easily been made more generalizable by esp-matter, but they decided not to...

        In the future I might bypass esp-matter entirely for endpoint creation and use connectedhomeip directly.
        However, this also brings challenges and isn't entirely generalizable either. For example, the concentration
        measurement clusters use a different namespace naming than most other clusters.
        """

        # Collect the complete endpoint structure before emitting its build callback.
        for conf_key, device_config in self._config.items():
            device_type = DEVICE_TYPES_BY_CONF_KEY.get(conf_key)
            if device_type:
                await self._configure_device_type(var, device_type, device_config)

        if sensor_id := self._config.get(CONF_BATTERY):
            battery = battery_sensor_attribute()
            self.enabled_sdkconfig_options.add(battery.cluster.sdkconfig_option)
            await self._register_sensor_attribute(var, sensor_id, battery)

        for cluster_name, config in self._config[CONF_CLUSTERS].items():
            cluster = CLUSTERS_BY_NAME[cluster_name]
            self.enabled_sdkconfig_options.add(cluster.sdkconfig_option)
            cluster_config = self._cluster_configs[cluster_name]
            for configured_feature in config.get(CONF_WITH_FEATURES, ()):
                if feature := cluster.get_feature(configured_feature):
                    cluster_config.enabled_features[feature.name] = True

        if self.plan.label is not None:
            # The label is served by the FixedLabel cluster (see matter_conformance.cpp).
            self.enabled_sdkconfig_options.add(
                CLUSTERS_BY_NAME[conformance.FIXED_LABEL_CLUSTER].sdkconfig_option
            )
            self._cluster_configs.setdefault(
                conformance.FIXED_LABEL_CLUSTER, _ClusterConfig()
            )

        await self._register_attribute_automations(var)
        self._register_conformance(var)

        build_fn = cg.RawExpression(self._make_build_callback())
        cg.add(var.register_endpoint(self._endpoint_id, build_fn))

    def _register_conformance(self, var):
        """Parent, label and semantic tags are applied by matter_conformance.cpp."""
        plan = self.plan
        cg.add(var.register_endpoint_parent(self._endpoint_id, plan.parent))
        if plan.label is not None:
            cg.add(var.register_endpoint_label(self._endpoint_id, plan.label))
        for tag in plan.tags:
            cg.add(
                var.register_endpoint_tag(
                    self._endpoint_id, tag.namespace, tag.tag, tag.label or ""
                )
            )

    def structure_signature(self) -> str:
        """Device types, server clusters and enabled features of this endpoint.

        Hashed into the ConfigurationVersion trigger; keep it deterministic.
        """
        clusters: dict[int, set[str]] = {}
        for device_type in self._device_types:
            for cluster in device_type.server_clusters:
                if cluster.required:
                    clusters.setdefault(cluster.id, set())
        for cluster_name, cluster_config in self._cluster_configs.items():
            cluster = CLUSTERS_BY_NAME[cluster_name]
            clusters.setdefault(cluster.id, set()).update(
                name
                for name, enabled in cluster_config.enabled_features.items()
                if enabled
            )
        device_types = ",".join(
            str(i) for i in sorted(t.id for t in self._device_types)
        )
        cluster_text = ",".join(
            f"{cid}[{'+'.join(sorted(features))}]"
            for cid, features in sorted(clusters.items())
        )
        return f"d{device_types}|c{cluster_text}"

    async def _register_attribute_automations(self, var):
        configured_clusters = self._config.get(CONF_ON_ATTRIBUTE, {})
        for cluster in CLUSTERS:
            cluster_key = snake_case(cluster.name)
            cluster_config = configured_clusters.get(cluster_key, {})
            for attribute in cluster.attributes:
                if not attribute.server:
                    continue
                if attribute.name is None:
                    continue
                value_type = attribute_value_type(attribute)
                if value_type is None:
                    continue
                attribute_key = snake_case(attribute.name)
                configurations = [
                    *cluster_config.get(attribute_key, []),
                    *configured_clusters.get(f"{cluster_key}.{attribute_key}", []),
                ]
                for conf in configurations:
                    trigger = cg.new_Pvariable(
                        conf[CONF_TRIGGER_ID],
                        var,
                        self._endpoint_id,
                        cluster.id,
                        attribute.id,
                    )
                    cg.add(var.register_attribute_trigger(trigger))
                    await automation.build_automation(
                        trigger, [(value_type, "value")], conf
                    )

    async def _configure_device_type(
        self, var, device_type: DeviceType, device_config: dict
    ):
        """Collect the endpoint structure and register its runtime entity mappings."""
        self._device_types.append(device_type)

        for cluster in device_type.server_clusters:
            # Enable all clusters and not only the enabled ones because esp_matter doesn't correctly guard endpoint compilation...
            self.enabled_sdkconfig_options.add(cluster.sdkconfig_option)
            if cluster.required:
                self._cluster_configs[cluster.name].created = True
                if cluster.name == "Binding":
                    # esp_matter doesn't create the binding cluster when adding a device type to an endpoint, even when
                    # the device type requires it so it must always be forcibly created when a device type needs it.
                    self._cluster_configs["Binding"].created = False

        # Register sensor attributes
        for sensor_attr in device_type.sensor_attributes:
            if sensor_id := device_config.get(sensor_attr.conf_key):
                await self._register_sensor_attribute(
                    var, sensor_id, sensor_attr, device_type, device_config
                )

        if device_type.name == "electrical_sensor":
            self._electrical_power_features = configure_electrical_sensor(
                self._cluster_configs, device_type, device_config
            )
            self.global_includes.add(
                '#include "esphome/components/matter/matter_sensors.h"'
            )

        # Register ESPHome entities
        if CONF_LIGHT_ID in device_config:
            light_ = await cg.get_variable(device_config[CONF_LIGHT_ID])
            self._light_variables[device_type.name] = light_
            cg.add(var.register_light(light_, self._endpoint_id))
        if CONF_SWITCH_ID in device_config:
            switch_ = await cg.get_variable(device_config[CONF_SWITCH_ID])
            cg.add(var.register_switch(switch_, self._endpoint_id))
        await register_entity(
            var, self._endpoint_id, device_type.name, device_config
        )

        if fan_binding.CONF_FAN_ID in device_config:
            self._fan_variable = await cg.get_variable(
                device_config[fan_binding.CONF_FAN_ID]
            )
            cg.add(var.register_fan(self._fan_variable, self._endpoint_id))
            self.global_includes.add('#include "esphome/components/matter/matter_fan.h"')
        if CONF_COVER_ID in device_config:
            cover_ = await cg.get_variable(device_config[CONF_COVER_ID])
            cg.add(var.register_cover(cover_, self._endpoint_id))
        if CONF_CLIMATE_ID in device_config:
            climate_ = await cg.get_variable(device_config[CONF_CLIMATE_ID])
            self._climate_variables[device_type.name] = climate_
            self.global_includes.add(CLIMATE_INCLUDE)
            cg.add(
                var.register_climate(
                    climate_,
                    self._endpoint_id,
                    climate_prefer_cooling(device_type.name),
                )
            )

        # Register Switch cluster event bindings
        await register_switch_binding(var, self._endpoint_id, device_config)

        # Register extra features
        for enabled_feature in device_config.get(CONF_WITH_FEATURES, ()):
            for cluster in device_type.server_clusters:
                if feature := cluster.get_feature(enabled_feature):
                    cluster_config = self._cluster_configs[cluster.name]
                    cluster_config.enabled_features[feature.name] = True

    def _make_device_type_lines(self, device_type: DeviceType, index: int):
        config_var = f"device_config_{index}"
        device_config = self._config[device_type.conf_key]
        config_init = ""
        if device_type.name == WINDOW_COVERING and CONF_COVER_ID in device_config:
            config_init = cover_config_init(self._endpoint_id, device_config)
        lines = [
            f"esp_matter::endpoint::{device_type.namespace}::config_t {config_var}{{{config_init}}};"
        ]

        if min_level := device_config.get(CONF_MIN_LEVEL):
            lines.append(f"{config_var}.level_control.min_level = {min_level};")
        if max_level := device_config.get(CONF_MAX_LEVEL):
            lines.append(f"{config_var}.level_control.max_level = {max_level};")

        if device_type.name in ("color_temperature_light", "extended_color_light"):
            light = self._light_variables.get(device_type.name)
            if light is not None:
                self.global_includes.add(
                    '#include "esphome/components/matter/matter_conversions.h"'
                )
                traits_var = f"light_traits_{index}"
                lines.extend(
                    (
                        f"auto {traits_var} = {light}->get_traits();",
                        f"{config_var}.color_control_color_temperature.color_temp_physical_min_mireds = "
                        f"esphome::matter::conversion::to_matter::color_temperature({traits_var}.get_min_mireds());",
                        f"{config_var}.color_control_color_temperature.color_temp_physical_max_mireds = "
                        f"esphome::matter::conversion::to_matter::color_temperature({traits_var}.get_max_mireds());",
                    )
                )

        if device_type.name == "fan" and self._fan_variable is not None:
            lines.extend(
                fan_binding.config_lines(
                    config_var,
                    self._fan_variable,
                    self._cluster_configs["FanControl"].enabled_features,
                )
            )

        cover_lines = []
        if device_type.name == WINDOW_COVERING and CONF_COVER_ID in device_config:
            self.global_includes.add(
                '#include "esphome/components/matter/matter_cover.h"'
            )
            # Emitted after the feature_flags assignment below.
            cover_lines = cover_config_lines(
                config_var, self._endpoint_id, device_config
            )

        lines.extend(
            device_config_lines(
                device_type.name,
                config_var,
                self._endpoint_id,
                device_config,
            )
        )

        # Configure cluster features
        for cluster in device_type.server_clusters:
            if not cluster.required:
                continue  # Non-required clusters must be created after the device_type
            cluster_config = self._cluster_configs[cluster.name]
            features_by_name = {f.name: f for f in cluster.features}
            feature_flags = []
            for feature_name, enabled in cluster_config.enabled_features.items():
                if not enabled:
                    continue
                feature = features_by_name[feature_name]
                if (
                    feature.conformance is not None and feature.conformance.choice
                ) or cluster.name in FLAG_DRIVEN_CLUSTERS:
                    feature_flags.append(
                        f"esp_matter::cluster::{cluster.espm_namespace}::feature::{feature.namespace}::get_id()"
                    )
            if cluster.name == "Switch" and (
                line := multi_press_max_line(config_var, device_config)
            ):
                lines.append(line)
            if feature_flags:
                lines.append(
                    f"{config_var}.{device_type.config_member(cluster)}.feature_flags = {' | '.join(feature_flags)};"
                )

        lines.extend(cover_lines)
        climate_ = self._climate_variables.get(device_type.name)
        if climate_ is not None:
            lines.extend(climate_config_lines(config_var, climate_, device_type.name))

        if device_type.name == "electrical_sensor":
            lines.extend(
                electrical_config_lines(
                    config_var,
                    self._endpoint_id,
                    self._cluster_configs,
                    self._electrical_power_features,
                )
            )

        lines.extend(
            (
                f"if (esp_matter::endpoint::{device_type.namespace}::add(endpoint, &{config_var}) != ESP_OK)",
                "  return false;",
            )
        )
        if device_type.name == "soil_sensor":
            # The code driven Soil Measurement cluster aborts at startup without limits.
            self.global_includes.add(
                '#include "esphome/components/matter/matter_sensors.h"'
            )
            lines.append(
                "esphome::matter::set_soil_moisture_limits(esp_matter::endpoint::get_id(endpoint));"
            )
        if climate_ is not None:
            lines.extend(climate_post_create_lines(climate_, device_type.name))
        return lines

    async def _register_sensor_attribute(
        self,
        var,
        sensor_id: ID,
        sensor_attribute: SensorAttribute,
        device_type: DeviceType | None = None,
        device_config: dict | None = None,
    ):
        cluster = sensor_attribute.cluster
        attribute = sensor_attribute.attribute
        if cluster is None or attribute is None:
            raise cv.Invalid(
                f"sensor_attribute {sensor_attribute.conf_key} is not initialized"
            )

        self._cluster_configs[cluster.name].created |= False
        for feature_name in sensor_attribute.features:
            self._cluster_configs[cluster.name].enabled_features[feature_name] = True
        if sensor_attribute.create_attribute:
            self._optional_attributes.append((cluster, attribute))

        # Warnings were already logged during final validation.
        override = (device_config or {}).get(CONF_MEASUREMENT_UNITS, {})
        unit, device_class = (None, None)
        if sensor_attribute.family is not None:
            unit, device_class = sensor_unit_info(CORE.config, sensor_id)
        plan = plan_sensor(
            sensor_attribute,
            unit,
            device_class,
            override.get(sensor_attribute.conf_key),
        )

        sensor = await cg.get_variable(sensor_id)
        binding = None
        converter = converter_expression(sensor_attribute, plan)
        if sensor_attribute.sensor_type is BinarySensor:
            # Polarity, occupancy sensor type and alarm levels depend on the device_class (see binary_sensors.py).
            device_class = binary_sensors.get_device_class(CORE.config, sensor_id)
            binding = binary_sensors.resolve_binding(
                device_type, sensor_attribute, device_config, device_class
            )
            converter = cg.RawExpression(
                f"esphome::matter::conversion::{binding.converter}"
            )
            if cluster.name == "OccupancySensing" and (
                feature := binary_sensors.implied_occupancy_feature(
                    device_config, device_class
                )
            ):
                self._cluster_configs[cluster.name].enabled_features[feature] = True
        if binding is not None:
            self.global_includes.add(binding.include)
            if binding.converter != sensor_attribute.converter:
                self.global_includes.add(binary_sensors.BINARY_SENSORS_INCLUDE)
            args = [sensor, self._endpoint_id, cluster.id, attribute.id, converter]
            if sensor_attribute.code_driven:
                args.append(cg.RawExpression(f"esphome::matter::{binding.updater}"))
            cg.add(var.register_binary_sensor_attribute(*args))
        elif sensor_attribute.kind == "concentration":
            cg.add(
                var.register_concentration_sensor(
                    sensor,
                    self._endpoint_id,
                    cluster.id,
                    attribute.id,
                    converter,
                    MEASUREMENT_UNITS[plan.measurement_unit],
                )
            )
        elif sensor_attribute.kind == "soil_moisture":
            cg.add(
                var.register_soil_moisture_sensor(
                    sensor, self._endpoint_id, cluster.id, attribute.id, converter
                )
            )
        elif sensor_attribute.kind == "electrical_power":
            cg.add(
                var.register_electrical_power_sensor(
                    sensor, self._endpoint_id, attribute.id, converter
                )
            )
        elif sensor_attribute.kind == "electrical_energy":
            cg.add(
                var.register_electrical_energy_sensor(
                    sensor, self._endpoint_id, attribute.id, converter
                )
            )
        elif sensor_attribute.code_driven:
            if cluster.chip_include is not None:
                self.global_includes.add(cluster.chip_include)
            if cluster.chip_fqn is None:
                raise cv.Invalid(
                    f"Cluster {cluster.conf_key} does not define a CHIP class"
                )
            cluster_type = cg.RawExpression(cluster.chip_fqn)
            value_type = cg.RawExpression(
                {
                    "int16s": "int16_t",
                    "int16u": "uint16_t",
                    "temperature": "int16_t",
                }[attribute.type]
            )
            setter = cg.RawExpression(f"&{cluster.chip_fqn}::SetMeasuredValue")
            register = var.register_code_driven_sensor_attribute.template(
                cluster_type, value_type, setter
            )
            cg.add(
                register(sensor, self._endpoint_id, cluster.id, attribute.id, converter)
            )
        else:
            register_sensor_attribute = var.register_sensor_attribute(
                sensor, self._endpoint_id, cluster.id, attribute.id, converter
            )
            cg.add(register_sensor_attribute)

    def _make_cluster_lines(self, cluster: Cluster):
        """Build lines for a cluster not created by a device type."""
        cluster_ns = f"esp_matter::cluster::{cluster.espm_namespace}"

        config_var = f"{cluster.espm_namespace}_config"
        lines = [f"{cluster_ns}::config_t {config_var}{{}};"]
        feature_flags = []
        features_by_name = {feature.name: feature for feature in cluster.features}
        cluster_config = self._cluster_configs[cluster.name]
        for feature_name, enabled in cluster_config.enabled_features.items():
            if not enabled:
                continue
            feature = features_by_name.get(feature_name)
            if not feature:
                raise cv.Invalid(
                    f"Cluster {cluster.name} has no feature {feature_name}"
                )
            feature_flags.append(
                f"{cluster_ns}::feature::{feature.namespace}::get_id()"
            )
        if feature_flags:
            lines.append(f"{config_var}.feature_flags = {' | '.join(feature_flags)};")
        lines.extend(
            (
                f"if ({cluster_ns}::create(endpoint, &{config_var}, esp_matter::CLUSTER_FLAG_SERVER) == nullptr)",
                "  return false;",
            )
        )
        return lines

    def _make_build_callback(self) -> str:
        """ """
        lines = ["[](esp_matter::endpoint_t *endpoint) -> bool {"]

        for index, device_type in enumerate(self._device_types):
            lines.extend(self._make_device_type_lines(device_type, index))

        for cluster_name, cluster_config in self._cluster_configs.items():
            if not cluster_config.created:
                lines.extend(self._make_cluster_lines(CLUSTERS_BY_NAME[cluster_name]))

        # Choice features must be present in the config used to create their
        # cluster. Other features on device-type-created clusters are added
        # directly after the device type has created the cluster.
        for cluster_name, cluster_config in self._cluster_configs.items():
            if not cluster_config.created:
                continue
            cluster = CLUSTERS_BY_NAME[cluster_name]
            if cluster_name in FLAG_DRIVEN_CLUSTERS:
                continue  # All features are already set through feature_flags.
            features_by_name = {feature.name: feature for feature in cluster.features}
            direct_features = [
                features_by_name[feature_name]
                for feature_name, enabled in cluster_config.enabled_features.items()
                if enabled
                and feature_name in features_by_name
                and (
                    features_by_name[feature_name].conformance is None
                    or features_by_name[feature_name].conformance.choice is None
                )
            ]
            # Features of a bound fan come from its traits instead.
            bound_fan = self._fan_variable if cluster.name == "FanControl" else None
            if not direct_features and bound_fan is None:
                continue
            cluster_var = f"{cluster.espm_namespace}_cluster"
            lines.extend(
                (
                    f"auto *{cluster_var} = esp_matter::cluster::get(endpoint, {cluster.id});",
                    f"if ({cluster_var} == nullptr)",
                    "  return false;",
                )
            )
            if bound_fan is not None:
                lines.extend(fan_binding.feature_lines(cluster_var, bound_fan))
            for feature in direct_features:
                lines.extend(
                    (
                        f"if (esphome::matter::add_feature({cluster_var}, esp_matter::cluster::{cluster.espm_namespace}::feature::{feature.namespace}::add) != ESP_OK)",
                        "  return false;",
                    )
                )

        # Optional attributes can only be created once their cluster exists.
        lines.extend(optional_attribute_lines(self._optional_attributes))

        lines.extend(("return true;", "}"))
        return "\n".join(lines)


class EndpointRegistry:
    def __init__(self):
        self._endpoints: list[Endpoint] = []
        self._lookup: dict[int | ID, Endpoint] = {}

    def register(self, endpoint: Endpoint):
        self._endpoints.append(endpoint)
        self._lookup[endpoint.endpoint_id] = endpoint
        self._lookup[endpoint.config[CONF_ID]] = endpoint

    def lookup(self, endpoint_reference: int | ID) -> Endpoint:
        endpoint = self._lookup.get(endpoint_reference)
        if endpoint is None:
            raise cv.Invalid(f"Unknown Matter endpoint '{endpoint_reference}'")
        return endpoint

    def __iter__(self):
        return iter(self._endpoints)


def build_endpoints(matter_config: dict, full_config: Config) -> EndpointRegistry:
    """During validation an endpoint registry is built and stored in CORE.data["matter"]["endpoints"]."""
    endpoints = EndpointRegistry()
    plans = conformance.plan_endpoints(matter_config[CONF_ENDPOINTS])
    for endpoint_id, endpoint_config in matter_config[CONF_ENDPOINTS].items():
        endpoints.register(Endpoint(endpoint_id, endpoint_config, plans[endpoint_id]))

    # Register matter actions that might require specific features to the endpoints.
    for action_type, action in iter_matter_actions(full_config):
        if action_type not in ("matter.send_event", "matter.set_attribute"):
            continue
        endpoint = endpoints.lookup(action[CONF_ENDPOINT])
        if action_type == "matter.send_event":
            cluster, event = get_event_from_config(action)
            endpoint.register_usage(cluster, event)
        else:
            cluster, attribute = get_attribute_from_config(action)
            endpoint.register_usage(cluster, attribute)

    for endpoint in endpoints:
        endpoint.resolve()

    CORE.data.setdefault(CONF_MATTER, {})[CONF_ENDPOINTS] = endpoints
    CORE.data[CONF_MATTER]["plans"] = plans
    return endpoints


async def register_endpoints(var, config: ConfigType):
    root_node = DEVICE_TYPES_BY_ID[22]
    global_includes = set()
    enabled_sdkconfig_clusters = {
        cluster.sdkconfig_option
        for cluster in root_node.server_clusters
        if cluster.required
    }
    enabled_sdkconfig_clusters.update(
        {
            "CONFIG_SUPPORT_BINDING_CLUSTER",
            "CONFIG_SUPPORT_TLS_CERTIFICATE_MANAGEMENT_CLUSTER",
            # A bug in esp_matter builds the LevelControl cluster unconditionally...
            "CONFIG_SUPPORT_LEVEL_CONTROL_CLUSTER",
            "CONFIG_SUPPORT_COLOR_CONTROL_CLUSTER",
            "CONFIG_SUPPORT_SCENES_CLUSTER",
        }
    )

    plans = CORE.data[CONF_MATTER]["plans"]
    signatures = {}
    for endpoint in CORE.data[CONF_MATTER][CONF_ENDPOINTS]:
        endpoint_id = endpoint.endpoint_id
        await endpoint.register(var)
        signatures[endpoint_id] = endpoint.structure_signature()
        global_includes.update(endpoint.global_includes)
        enabled_sdkconfig_clusters.update(endpoint.enabled_sdkconfig_options)

    cg.add(
        var.set_structure_hash(
            HexInt(
                conformance.structure_hash(config[CONF_ENDPOINTS], plans, signatures)
            )
        )
    )
    # Exact limits: esp-matter sizes per-endpoint and per-device-type tables from them.
    # The root node endpoint counts as a dynamic endpoint.
    add_idf_sdkconfig_option(
        "CONFIG_ESP_MATTER_MAX_DYNAMIC_ENDPOINT_COUNT", len(config[CONF_ENDPOINTS]) + 1
    )
    add_idf_sdkconfig_option(
        "CONFIG_ESP_MATTER_MAX_DEVICE_TYPE_COUNT",
        conformance.max_device_types(config[CONF_ENDPOINTS]),
    )
    if any(plan.label is not None for plan in plans.values()):
        # FixedLabel needs a DeviceInfoProvider; matter_conformance.cpp installs one.
        add_idf_sdkconfig_option("CONFIG_CUSTOM_DEVICE_INFO_PROVIDER", True)

    layout_dump.dump_layout(CORE.data[CONF_MATTER][CONF_ENDPOINTS])  # opt-in: MATTER_LAYOUT_JSON

    # Set sdkconfig options to compile the correct clusters.
    for cluster in CLUSTERS:
        sdkconfig_option = cluster.sdkconfig_option
        add_idf_sdkconfig_option(
            sdkconfig_option, sdkconfig_option in enabled_sdkconfig_clusters
        )

    for global_include in global_includes:
        cg.add_global(cg.RawStatement(global_include), prepend=True)
