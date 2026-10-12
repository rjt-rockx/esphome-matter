import logging
from collections import defaultdict
from dataclasses import dataclass, field

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome import automation
from esphome.components import light
from esphome.components.binary_sensor import BinarySensor
from esphome.components.esp32 import add_idf_sdkconfig_option
from esphome.config import Config
from esphome.const import CONF_ID, CONF_LIGHT_ID, CONF_RESTORE_MODE, CONF_TRIGGER_ID
from esphome.core import CORE, ID, HexInt
from esphome.types import ConfigType

from . import binary_sensors, bridge, cluster_options
from . import conformance
from . import sensor_extras
from . import delegates
from .actions import get_attribute_from_config, get_event_from_config
from .const import *
from . import closure
from .covers import WINDOW_COVERING
from .covers import config_init as cover_config_init
from .covers import config_lines as cover_config_lines
from .data_model.attributes import Attribute, SensorAttribute, attribute_value_type
from .data_model.clusters import (
    CLUSTERS,
    CLUSTERS_BY_CONF_KEY,
    CLUSTERS_BY_NAME,
    Cluster,
)
from .data_model.conformance import (
    ConformanceDisposition,
    ConformanceError,
    resolve_feature_requirements,
)
from .data_model.device_types import (
    DEVICE_TYPES,
    DEVICE_TYPES_BY_CONF_KEY,
    DEVICE_TYPES_BY_ID,
    FEATURE_DEPENDENCIES,
    FLAG_DRIVEN_CLUSTERS,
    DeviceType,
)
from . import fan_binding, filter_monitoring, layout_dump
from . import matter_climate as climate_binding
from .matter_climate import (
    CLIMATE_INCLUDE,
    CONF_CLIMATE_ID,
    config_lines as climate_config_lines,
    post_create_lines as climate_post_create_lines,
    prefer_cooling as climate_prefer_cooling,
)
from .icd import ICD_MANAGEMENT_SDKCONFIG
from .lock_valve import device_config_lines, register_entity
from .generic_switch import multi_press_max_line, register_switch_binding
from .data_model.units import MEASUREMENT_UNITS
from . import power_source, power_topology
from .sensor_mapping import (
    CONF_MEASUREMENT_UNITS,
    configure_electrical_sensor,
    converter_expression,
    electrical_config_lines,
    optional_attribute_lines,
    plan_sensor,
    sensor_unit_info,
    validate_battery_binding,
)
from .data_model import espmatter_names
from .data_model.events import Event
from . import light_binding
from .types import MatterAttributeTrigger, MatterEndpointRef
from .util import iter_matter_actions, maybe_empty, restore_warning, snake_case

_LOGGER = logging.getLogger(__name__)


def switch_restore_warning(matter_config: dict, full_config: Config):
    restore_warning(matter_config, full_config, CONF_SWITCH_ID, "Switch")


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


def _check_heat_pump_climate(config):
    heat_pump = config.get("heat_pump")
    if not isinstance(heat_pump, dict):
        return config
    on_endpoint = heat_pump.get(climate_binding.CONF_THERMOSTAT_ON_ENDPOINT, False)
    if CONF_CLIMATE_ID in heat_pump and not on_endpoint:
        raise cv.Invalid(
            "heat_pump: the Heat Pump device type has Thermostat only as a client "
            "cluster, so a conforming device puts the climate on a separate "
            "`thermostat` endpoint below the heat pump:\n"
            "  1: {heat_pump: {}}\n"
            "  2: {parent: 1, thermostat: {climate_id: ...}}\n"
            f"To put the Thermostat server on the heat pump endpoint anyway (what "
            f"Home Assistant needs on a single endpoint, but not what the device "
            f"type describes), set `{climate_binding.CONF_THERMOSTAT_ON_ENDPOINT}: true`"
        )
    if on_endpoint:
        if CONF_CLIMATE_ID not in heat_pump:
            raise cv.Invalid(
                f"heat_pump: {climate_binding.CONF_THERMOSTAT_ON_ENDPOINT} needs climate_id"
            )
        if "thermostat" in config[CONF_CLUSTERS]:
            raise cv.Invalid(
                "heat_pump: climate_id creates the Thermostat server cluster, remove "
                "`clusters: thermostat`"
            )
        _LOGGER.warning(
            "heat_pump with thermostat_on_endpoint puts a Thermostat server on the Heat "
            "Pump endpoint; the Matter device type only lists Thermostat as a client "
            "cluster there. Prefer a `thermostat` endpoint below the heat pump "
            "(`parent:`), see docs/hvac.md"
        )
    return config


def _normalize_fan_control(config):
    return fan_binding.normalize_endpoint(config, DEVICE_TYPES_BY_CONF_KEY)


ENDPOINT_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(MatterEndpointRef),
            cv.Optional(CONF_CLUSTERS, default=dict): {
                cluster.schema_key: maybe_empty(
                    cluster.schema(),
                    cluster_options.validate(cluster.conf_key),
                    *delegates.cluster_validators(cluster.name),
                )
                for cluster in CLUSTERS
            },
            # TODO: move on_attribute to clusters
            cv.Optional(CONF_ON_ATTRIBUTE): _on_attribute_schema(),
        }
        # battery: / wired: expose a Power Source cluster on this endpoint (see power_source.py).
        | power_source.SCHEMA_OPTIONS
        | conformance.SCHEMA_OPTIONS
        | {device_type.schema_key: device_type.schema() for device_type in DEVICE_TYPES}
    ),
    _validate_on_attribute_forms,
    validate_battery_binding,
    _normalize_fan_control,
    _check_heat_pump_climate,
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
        all_endpoint_ids: tuple[int, ...] = (),
    ):
        self._endpoint_id = endpoint_id
        self._config = config
        # Every configured endpoint (for the EndpointList of a root node Power Source).
        self.all_endpoint_ids = tuple(all_endpoint_ids)
        # (endpoint, kind, order, present sensor) of every Power Source; set by build_endpoints.
        self.all_power_sources: list[tuple] = []
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
        self._fan_binding: fan_binding.FanBinding | None = None
        self._climate_variables = {}
        # Device types whose climate_id needs a Thermostat server created next to them.
        self._thermostat_outside: set[str] = set()
        # Clusters whose creation lines are emitted with their device type.
        self._custom_created: set[str] = set()
        # (Cluster, Attribute) of sensor attributes that esp-matter does not create itself.
        self._optional_attributes: list[tuple[Cluster, Attribute]] = []
        # Extra lines that set the initial value of a created optional attribute.
        self._initial_values: dict[tuple[int, int], list[str]] = {}
        self._electrical_power_features: list[str] = []
        # Extra C++ for clusters configured by sensor_extras.py (evaluated lazily, see sensor_extras.Extra).
        self.extras: list[sensor_extras.Extra] = []
        # Cluster ids whose delegate is installed at the end of the build callback (delegates.py).
        self._delegate_cluster_ids: list[int] = []

    @property
    def endpoint_id(self) -> int:
        return self._endpoint_id

    @property
    def config(self) -> dict:
        return self._config

    @property
    def cluster_configs(self):
        return self._cluster_configs

    @property
    def optional_attributes(self) -> list[tuple[Cluster, Attribute]]:
        return self._optional_attributes

    @property
    def is_root(self) -> bool:
        """Endpoint 0 is the root node, created by esp-matter itself."""
        return self._endpoint_id == power_source.ROOT_ENDPOINT

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
                    # The `alarms`/`sensitivity` options create the optional
                    # Boolean State Configuration cluster (sensor_extras.py).
                    cluster_present |= sensor_extras.creates_cluster(
                        cluster.name, device_config
                    )

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
        self._validate_light_options()
        usages_by_cluster = defaultdict(list)
        clusters_by_id = {}
        for cluster, usage in self.usages:
            clusters_by_id[cluster.id] = cluster
            if usage.conformance is not None:
                usages_by_cluster[cluster.id].append(usage)

        for cluster_key in self._config[CONF_CLUSTERS]:
            cluster = CLUSTERS_BY_CONF_KEY[cluster_key]
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

    def _validate_light_options(self) -> None:
        for config_key, device_config in self._config.items():
            device_type = DEVICE_TYPES_BY_CONF_KEY.get(config_key)
            if device_type is None or not isinstance(device_config, dict):
                continue
            if light_binding.CONF_ON_OFF_TRANSITION_TIME not in device_config:
                continue
            present, enabled_codes, _ = self._cluster_state(
                CLUSTERS_BY_NAME["LevelControl"]
            )
            if not present:
                raise cv.Invalid(
                    f"Matter endpoint {self._endpoint_id}: "
                    f"{light_binding.CONF_ON_OFF_TRANSITION_TIME} needs the "
                    f"LevelControl cluster, which {device_type.name} doesn't have. "
                    "Add it with `clusters: level_control:`"
                )
            if "OO" not in enabled_codes:
                raise cv.Invalid(
                    f"Matter endpoint {self._endpoint_id}: "
                    f"{light_binding.CONF_ON_OFF_TRANSITION_TIME} needs the OnOff "
                    "feature of the LevelControl cluster"
                )

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
        # The Bridged Node device type goes first in the DeviceTypeList (the primary one), like esp-matter's bridge does.
        for conf_key, device_config in sorted(
            self._config.items(),
            key=lambda item: item[0] != bridge.DEVICE_TYPE_BRIDGED_NODE,
        ):
            device_type = DEVICE_TYPES_BY_CONF_KEY.get(conf_key)
            if device_type:
                await self._configure_device_type(var, device_type, device_config)

        if bridged_node := self._config.get(bridge.DEVICE_TYPE_BRIDGED_NODE):
            await self._register_bridged_node(var, bridged_node)

        await power_source.register(self, var)

        await self._register_fan_control(var)

        for cluster_key, config in self._config[CONF_CLUSTERS].items():
            cluster = CLUSTERS_BY_CONF_KEY[cluster_key]
            self.enabled_sdkconfig_options.add(cluster.sdkconfig_option)
            cluster_config = self._cluster_configs[cluster.name]
            for configured_feature in config.get(CONF_WITH_FEATURES, ()):
                if feature := cluster.get_feature(configured_feature):
                    cluster_config.enabled_features[feature.name] = True
            if delegates.is_delegate_cluster(
                cluster.name
            ) and await delegates.register_cluster(
                var, self._endpoint_id, cluster, config
            ):
                self._delegate_cluster_ids.append(cluster.id)
                self.global_includes.add(delegates.DELEGATES_INCLUDE)

        await self._register_filter_monitoring(var)

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

    @property
    def fan_control_binding(self) -> "fan_binding.FanBinding | None":
        return self._fan_binding

    async def _register_fan_control(self, var):
        """Binds the Fan Control cluster to an ESPHome fan or to the fan modes of a climate."""
        binding = fan_binding.find_binding(self._config, DEVICE_TYPES_BY_CONF_KEY)
        if binding is None:
            return
        self._fan_binding = binding
        self._fan_variable = await cg.get_variable(binding.entity_id)
        if binding.kind == "fan":
            self.global_includes.add('#include "esphome/components/matter/matter_fan.h"')
            args = [self._fan_variable, self._endpoint_id]
            if binding.wind:
                args += [
                    binding.wind_natural or cg.RawExpression("nullptr"),
                    binding.wind_sleep or cg.RawExpression("nullptr"),
                ]
            cg.add(var.register_fan(*args))
        else:
            self.global_includes.add(
                '#include "esphome/components/matter/matter_climate_fan.h"'
            )
            cg.add_define("USE_MATTER_CLIMATE_FAN")
            cg.add(var.register_climate_fan(self._fan_variable, self._endpoint_id))

    def _filter_clusters(self):
        for cluster_key, config in self._config[CONF_CLUSTERS].items():
            if filter_monitoring.is_filter_cluster(cluster_key):
                yield CLUSTERS_BY_CONF_KEY[cluster_key], config

    async def _register_filter_monitoring(self, var):
        for cluster, config in self._filter_clusters():
            if not filter_monitoring.is_bound(config):
                continue
            self.global_includes.add(filter_monitoring.FILTER_INCLUDE)
            await filter_monitoring.register_filter(
                var, self._endpoint_id, cluster, config
            )

    async def _register_bridged_node(self, var, bridged_node: dict):
        """Bind BDBI Reachable to a binary sensor. The parent link is part of the conformance plan."""
        if reachable_id := bridged_node.get(bridge.CONF_REACHABLE):
            sensor = await cg.get_variable(reachable_id)
            self.global_includes.add(
                '#include "esphome/components/matter/matter_conversions.h"'
            )
            cg.add(
                var.register_binary_sensor_attribute(
                    sensor,
                    self._endpoint_id,
                    bridge.BDBI_CLUSTER_ID,
                    bridge.BDBI_REACHABLE_ATTRIBUTE_ID,
                    cg.RawExpression("esphome::matter::conversion::boolean_state"),
                )
            )

    def _register_conformance(self, var):
        """Parent, label and semantic tags are applied by matter_conformance.cpp."""
        if self.is_root:
            return  # the root node has no parent, label or tags
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
        if bridged_node := self._config.get(bridge.DEVICE_TYPE_BRIDGED_NODE):
            # The BDBI attributes a controller reads (NodeLabel, VendorName, ...) are part of the structure.
            device_types += "|b" + ",".join(sorted(bridge.attribute_names(bridged_node)))
        cluster_text = ",".join(
            f"{cid}[{'+'.join(sorted(features))}]"
            for cid, features in sorted(clusters.items())
        )
        signature = f"d{device_types}|c{cluster_text}"
        # Attributes, commands and events created for the sensor and alarm clusters (hold time, ...).
        extra_text = ",".join(
            sorted(
                f"{extra.cluster}.{kind}.{name}"
                for extra in self.extras
                for kind, name in extra.elements()
            )
        )
        if extra_text:
            signature += f"|e{extra_text}"
        return signature

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

        # esp_matter's endpoint::<device_type>::add() also creates clusters on its own (and
        # calls the add() of other device types, e.g. heat_pump -> power_source, electrical_sensor,
        # device_energy_management). Their server code has to be compiled in or the link fails.
        for cluster_name in espmatter_names.device_type_implied_clusters(
            device_type.name
        ):
            if implied := CLUSTERS_BY_NAME.get(cluster_name):
                self.enabled_sdkconfig_options.add(implied.sdkconfig_option)

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
            self.global_includes.add(
                '#include "esphome/components/matter/matter_lights.h"'
            )
            register_light_args = [light_, self._endpoint_id]
            if loop_effect := device_config.get(light_binding.CONF_COLOR_LOOP_EFFECT):
                register_light_args.append(loop_effect)
            cg.add(var.register_light(*register_light_args))
            self._register_light_level_attributes(device_type, device_config)
        if CONF_SWITCH_ID in device_config:
            switch_ = await cg.get_variable(device_config[CONF_SWITCH_ID])
            cg.add(var.register_switch(switch_, self._endpoint_id))
        await register_entity(
            var, self._endpoint_id, device_type.name, device_config
        )

        await closure.register_entity(var, self._endpoint_id, device_type.name, device_config)
        if CONF_COVER_ID in device_config and device_type.name == WINDOW_COVERING:
            cover_ = await cg.get_variable(device_config[CONF_COVER_ID])
            cg.add(var.register_cover(cover_, self._endpoint_id))
        if CONF_CLIMATE_ID in device_config:
            climate_ = await cg.get_variable(device_config[CONF_CLIMATE_ID])
            self._climate_variables[device_type.name] = climate_
            if not climate_binding.has_thermostat_member(device_type.name):
                # (only reachable for heat_pump with thermostat_on_endpoint)
                # The device type has no Thermostat server of its own (Heat
                # Pump): the cluster is created next to it, like an explicit one.
                self._thermostat_outside.add(device_type.name)
                thermostat = CLUSTERS_BY_NAME["Thermostat"]
                self.enabled_sdkconfig_options.add(thermostat.sdkconfig_option)
                self._cluster_configs.setdefault(thermostat.name, _ClusterConfig())
                self._custom_created.add(thermostat.name)
            self.global_includes.add(CLIMATE_INCLUDE)
            climate_args = [
                climate_,
                self._endpoint_id,
                climate_prefer_cooling(device_type.name),
            ]
            if climate_binding.heating_only(device_type.name):
                climate_args.append(True)
            cg.add(var.register_climate(*climate_args))

        # Register Switch cluster event bindings
        await register_switch_binding(
            var, self._endpoint_id, device_config, device_type.name
        )

        # Register extra features
        for enabled_feature in device_config.get(CONF_WITH_FEATURES, ()):
            for cluster in device_type.server_clusters:
                if feature := cluster.get_feature(enabled_feature):
                    cluster_config = self._cluster_configs[cluster.name]
                    cluster_config.enabled_features[feature.name] = True

        # Optional cluster features of the sensor and alarm clusters (hold time, alarms, air quality, ...)
        await sensor_extras.register(self, var, device_type, device_config)

    def _register_light_level_attributes(
        self, device_type: DeviceType, device_config: dict
    ) -> None:
        """Create the optional LevelControl attributes of a bound light.

        The transition time attributes only exist with the OnOff feature
        (OnOffTransitionTime, OnTransitionTime, OffTransitionTime); the light
        mapping uses them for On, Off, Toggle and MoveToLevel commands.
        """
        level_cluster = CLUSTERS_BY_NAME["LevelControl"]
        present, enabled_codes, _ = self._cluster_state(level_cluster)
        if not present:
            return
        has_on_off = "OO" in enabled_codes
        for name in light_binding.level_attribute_names(has_on_off):
            attribute = next(a for a in level_cluster.attributes if a.name == name)
            if (level_cluster, attribute) not in self._optional_attributes:
                self._optional_attributes.append((level_cluster, attribute))
            if extra := light_binding.initial_value_lines(name, device_config):
                self._initial_values[(level_cluster.id, attribute.id)] = extra

    def _make_device_type_lines(self, device_type: DeviceType, index: int):
        config_var = f"device_config_{index}"
        device_config = self._config[device_type.conf_key]
        config_init = ""
        if device_type.name == WINDOW_COVERING and CONF_COVER_ID in device_config:
            config_init = cover_config_init(self._endpoint_id, device_config)
        lines = [
            f"esp_matter::endpoint::{device_type.namespace}::config_t {config_var}{{{config_init}}};"
        ]
        if device_type.name == bridge.DEVICE_TYPE_BRIDGED_NODE:
            lines.extend(
                bridge.config_lines(config_var, self._endpoint_id, device_config)
            )
            self.global_includes.add(
                '#include "esphome/components/matter/matter_conformance.h"'
            )
            self.global_includes.add(bridge.DATA_MODEL_GUARD)

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
                        f"{traits_var}.get_min_mireds() > 0 ? esphome::matter::conversion::to_matter::color_temperature({traits_var}.get_min_mireds()) : {light_binding.DEFAULT_MIN_MIREDS};",
                        f"{config_var}.color_control_color_temperature.color_temp_physical_max_mireds = "
                        f"{traits_var}.get_max_mireds() > 0 ? esphome::matter::conversion::to_matter::color_temperature({traits_var}.get_max_mireds()) : {light_binding.DEFAULT_MAX_MIREDS};",
                    )
                )

        if device_type.name == "power_source":
            lines.extend(
                power_source.device_type_lines(
                    f"{config_var}.{device_type.config_member(CLUSTERS_BY_NAME[power_source.POWER_SOURCE_CLUSTER])}",
                    device_config,
                )
            )

        if self._fan_binding is not None and device_type.has_required_cluster(
            "FanControl"
        ):
            lines.extend(
                fan_binding.config_lines(
                    config_var,
                    self._fan_binding,
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
        if device_type.name == "mode_select":
            lines.extend(
                delegates.cluster_config_lines(
                    "ModeSelect",
                    f"{config_var}.mode_select",
                    self._config[CONF_CLUSTERS].get("mode_select"),
                )
            )
        closure_lines = closure.device_config_lines(
            device_type.name, config_var, self._endpoint_id, device_config
        )
        if closure_lines:
            self.global_includes.add(
                '#include "esphome/components/matter/matter_closure.h"'
            )
            lines.extend(closure_lines)

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
        if climate_ is not None and device_type.name not in self._thermostat_outside:
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
            if power_topology.needs_power_header(device_config):
                self.global_includes.add(power_topology.POWER_HEADER_INCLUDE)
            lines.extend(
                power_topology.delegate_lines(
                    config_var,
                    power_topology.power_topology_member(device_type),
                    device_config,
                )
            )

        bound_light = self._light_variables.get(device_type.name)
        if device_type.name == "extended_color_light" and bound_light is not None:
            # The ColorTemperature feature follows the traits of the light.
            add_call = f"esphome::matter::add_extended_color_light(endpoint, &{config_var}, {bound_light})"
        else:
            add_call = f"esp_matter::endpoint::{device_type.namespace}::add(endpoint, &{config_var})"
        lines.extend(
            (
                f"if ({add_call} != ESP_OK)",
                f'  return MATTER_BUILD_FAIL("device type {device_type.name}");',
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
            if device_type.name in self._thermostat_outside:
                lines.extend(
                    climate_binding.separate_thermostat_lines(
                        climate_, device_type.name
                    )
                )
            lines.extend(climate_post_create_lines(climate_, device_type.name))
        if device_type.name == bridge.DEVICE_TYPE_BRIDGED_NODE:
            lines.extend(bridge.post_create_lines(device_config))
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

    @staticmethod
    def _standalone_cluster_config_lines(cluster: Cluster, cfg, features):
        """Initial config of clusters that a device type would normally configure."""
        if cluster.name != "ColorControl":
            return []
        codes = {feature.code for feature in features}
        # ColorMode: 0 hue/saturation, 1 XY, 2 color temperature; the enhanced
        # mode adds 3 for enhanced hue.
        if "XY" in codes:
            mode = 1
        elif "CT" in codes:
            mode = 2
        else:
            mode = 0
        enhanced = 3 if "EHUE" in codes and mode == 0 else mode
        return [
            f"{cfg}.color_mode = {mode};",
            f"{cfg}.enhanced_color_mode = {enhanced};",
        ]

    @staticmethod
    def _standalone_cluster_extra_lines(cluster: Cluster, cluster_var):
        """The mandatory elements that device types add after creating the cluster."""
        if cluster.name == "OnOff":
            ns = "esp_matter::cluster::on_off::command"
            return [
                f"{ns}::create_on({cluster_var});",
                f"{ns}::create_toggle({cluster_var});",
            ]
        if cluster.name == "ColorControl":
            ns = "esp_matter::cluster::color_control"
            return [
                f"{ns}::attribute::create_remaining_time({cluster_var}, 0);",
                f"{ns}::command::create_stop_move_step({cluster_var});",
            ]
        return []

    def _make_cluster_lines(self, cluster: Cluster):
        """Build lines for a cluster not created by a device type."""
        if cluster.name == "FanControl":
            return self._make_fan_control_lines(cluster)
        if filter_monitoring.is_filter_cluster(cluster.conf_key):
            return filter_monitoring.create_lines(
                cluster,
                self._config[CONF_CLUSTERS][cluster.conf_key],
                self._endpoint_id,
            )
        for extra in self.extras:
            if extra.creates_cluster and extra.cluster == cluster.name:
                # Clusters whose creation needs more than the generic create call (see sensor_extras.py).
                return extra.lines()
        cluster_ns = f"esp_matter::cluster::{cluster.espm_namespace}"

        config_var = f"{cluster.espm_namespace}_config"
        cluster_var = f"{cluster.espm_namespace}_cluster"
        lines = [f"{cluster_ns}::config_t {config_var}{{}};"]
        feature_flags = []
        features = []
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
            features.append(feature)
            feature_flags.append(
                f"{cluster_ns}::feature::{feature.namespace}::get_id()"
            )
        if not espmatter_names.cluster_has_feature_flags(cluster.name) and (
            features or cluster.name in ("OnOff", "ColorControl")
        ):
            # The config has no feature_flags: the features are added to the
            # created cluster (in bit order, enhanced features need the base one).
            lines.extend(self._standalone_cluster_config_lines(cluster, config_var, features))
            lines.extend(
                (
                    f"auto *{cluster_var} = {cluster_ns}::create(endpoint, &{config_var}, esp_matter::CLUSTER_FLAG_SERVER);",
                    f"if ({cluster_var} == nullptr)",
                    f'  return MATTER_BUILD_FAIL("cluster {cluster.name} create");',
                )
            )
            for feature in sorted(features, key=lambda f: f.bit):
                lines.extend(
                    (
                        f"if (esphome::matter::add_feature({cluster_var}, {cluster_ns}::feature::{feature.namespace}::add) != ESP_OK)",
                        f'  return MATTER_BUILD_FAIL("cluster {cluster.name} feature {feature.name}");',
                    )
                )
            lines.extend(self._standalone_cluster_extra_lines(cluster, cluster_var))
            return lines
        if feature_flags:
            lines.append(f"{config_var}.feature_flags = {' | '.join(feature_flags)};")
        if cluster.name == power_source.POWER_SOURCE_CLUSTER:
            lines.extend(power_source.config_lines(config_var, self._config))
        lines.extend(
            delegates.cluster_config_lines(
                cluster.name,
                config_var,
                self._config[CONF_CLUSTERS].get(cluster.conf_key),
            )
        )
        lines.extend(
            (
                f"if ({cluster_ns}::create(endpoint, &{config_var}, esp_matter::CLUSTER_FLAG_SERVER) == nullptr)",
                f'  return MATTER_BUILD_FAIL("cluster {cluster.name} create");',
            )
        )
        return lines

    def _expand_feature_dependencies(self) -> None:
        """Enable the features that enabled features need (all configuration paths)."""
        for cluster_name, cluster_config in self._cluster_configs.items():
            enabled = cluster_config.enabled_features
            changed = True
            while changed:
                changed = False
                for name in [n for n, on in enabled.items() if on]:
                    for alternatives in FEATURE_DEPENDENCIES.get(name, ()):
                        if not any(enabled.get(alt) for alt in alternatives):
                            enabled[alternatives[0]] = True
                            changed = True

    def _make_fan_control_lines(self, cluster: Cluster):
        """Creates an optional Fan Control cluster.

        esp-matter's fan_control::config_t has no feature flags: the features are
        added afterwards (see _make_build_callback).
        """
        lines = ["esp_matter::cluster::fan_control::config_t fan_control_config{};"]
        if self._fan_binding is not None:
            lines.extend(
                fan_binding.cluster_config_lines(
                    self._fan_binding,
                    self._fan_variable,
                    self._cluster_configs[cluster.name].enabled_features,
                )
            )
        lines.extend(
            (
                "if (esp_matter::cluster::fan_control::create(endpoint, &fan_control_config, "
                "esp_matter::CLUSTER_FLAG_SERVER) == nullptr)",
                "  return MATTER_BUILD_FAIL(\"cluster FanControl create\");",
            )
        )
        return lines

    def _make_build_callback(self) -> str:
        """ """
        self._expand_feature_dependencies()
        lines = ["[](esp_matter::endpoint_t *endpoint) -> bool {"]

        for index, device_type in enumerate(self._device_types):
            lines.extend(self._make_device_type_lines(device_type, index))

        if self.is_root and power_source.kind(self._config) is not None:
            # The root node endpoint exists already; it gets the Power Source device type.
            lines.extend(power_source.root_device_type_lines())

        for cluster_name, cluster_config in self._cluster_configs.items():
            if not cluster_config.created and cluster_name not in self._custom_created:
                lines.extend(self._make_cluster_lines(CLUSTERS_BY_NAME[cluster_name]))

        # Choice features must be present in the config used to create their
        # cluster. Other features on device-type-created clusters are added
        # directly after the device type has created the cluster.
        for cluster_name, cluster_config in self._cluster_configs.items():
            # The features of an optional Fan Control cluster are added like those of
            # a device type created one (it has no feature flags in its config).
            if not cluster_config.created and cluster_name != "FanControl":
                continue
            cluster = CLUSTERS_BY_NAME[cluster_name]
            if cluster_name in FLAG_DRIVEN_CLUSTERS:
                continue  # All features are already set through feature_flags.
            features_by_name = {feature.name: feature for feature in cluster.features}
            # In bit order: dependent features (ColorLoop, EnhancedHue) come after
            # the ones they need.
            direct_features = sorted(
                (
                    features_by_name[feature_name]
                    for feature_name, enabled in cluster_config.enabled_features.items()
                    if enabled
                    and feature_name in features_by_name
                    and (
                        features_by_name[feature_name].conformance is None
                        or features_by_name[feature_name].conformance.choice is None
                    )
                ),
                key=lambda feature: feature.bit,
            )
            # Features of a bound fan or climate come from its traits instead.
            bound_fan = (
                self._fan_binding if cluster.name == "FanControl" else None
            )
            if bound_fan is not None:
                # Wind and Step are added together with the binding.
                direct_features = [
                    feature
                    for feature in direct_features
                    if feature.name not in fan_binding.BOUND_ONLY_FEATURES
                ]
            if not direct_features and bound_fan is None:
                continue
            cluster_var = f"{cluster.espm_namespace}_cluster"
            lines.extend(
                (
                    f"auto *{cluster_var} = esp_matter::cluster::get(endpoint, {cluster.id});",
                    f"if ({cluster_var} == nullptr)",
                    f'  return MATTER_BUILD_FAIL("cluster {cluster.name} missing after device type creation");',
                )
            )
            if bound_fan is not None:
                lines.extend(
                    fan_binding.feature_lines(cluster_var, bound_fan, self._fan_variable)
                )
            for feature in direct_features:
                lines.extend(
                    (
                        f"if (esphome::matter::add_feature({cluster_var}, esp_matter::cluster::{cluster.espm_namespace}::feature::{feature.namespace}::add) != ESP_OK)",
                        f'  return MATTER_BUILD_FAIL("cluster {cluster.name} feature {feature.name}");',
                    )
                )

        # Optional attributes can only be created once their cluster exists.
        lines.extend(
            optional_attribute_lines(self._optional_attributes, self._initial_values)
        )

        for extra in self.extras:
            if not extra.creates_cluster:
                lines.extend(extra.lines())
        # Delegates are installed once all clusters of the endpoint exist.
        lines.extend(delegates.build_attach_lines(self._delegate_cluster_ids))

        lines.extend(("return true;", "}"))
        # Every failing step names its endpoint (the sub generators do not know it).
        return "\n".join(lines).replace(
            'MATTER_BUILD_FAIL("', f'MATTER_BUILD_FAIL("endpoint {self._endpoint_id}: '
        )


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
    # The default EndpointList of the root node Power Source: the node's own endpoints. The Aggregator and
    # the bridged devices below it have power sources of their own.
    bridged = bridge.bridge_subtree(matter_config[CONF_ENDPOINTS])
    endpoint_ids = tuple(e for e in matter_config[CONF_ENDPOINTS] if e not in bridged)
    for endpoint_id, endpoint_config in matter_config[CONF_ENDPOINTS].items():
        endpoints.register(
            Endpoint(endpoint_id, endpoint_config, plans[endpoint_id], endpoint_ids)
        )

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

    power_sources = power_source.power_sources(matter_config[CONF_ENDPOINTS])
    for endpoint in endpoints:
        endpoint.all_power_sources = power_sources
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

    if config.get("icd") is not None:
        # esp_matter always compiles data_model_provider/clusters/icd_management/integration.cpp,
        # which constructs ICDManagementCluster / ICDManagementClusterWithCIP, but only builds the
        # definitions (connectedhomeip's icd-management-server/ICDManagementCluster.cpp) when this
        # option is on. Without it the link fails with an undefined reference to the constructor.
        enabled_sdkconfig_clusters.add(ICD_MANAGEMENT_SDKCONFIG)

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
    # The root node endpoint counts as a dynamic endpoint (it is configured as endpoint 0
    # when it gets a Power Source or Electrical Sensor).
    add_idf_sdkconfig_option(
        "CONFIG_ESP_MATTER_MAX_DYNAMIC_ENDPOINT_COUNT",
        len(set(config[CONF_ENDPOINTS]) - {power_source.ROOT_ENDPOINT}) + 1,
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

    for global_include in sorted(global_includes):
        cg.add_global(cg.RawStatement(global_include), prepend=True)
