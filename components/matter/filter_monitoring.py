"""HEPA / Activated Carbon Filter Monitoring clusters bound to ESPHome entities.

```yaml
matter:
  endpoints:
    1:
      air_purifier: {}
      clusters:
        hepa_filter_monitoring:
          condition: hepa_life          # sensor, remaining life in %
          warning_level: 20%            # ChangeIndication Warning at or below
          critical_level: 5%            # ChangeIndication Critical at or below
          replace_needed: hepa_replace  # binary sensor, ON = Critical
          reset_button: hepa_reset      # button pressed by ResetCondition
          on_reset:                     # and/or an automation
            - sensor.template.publish: {id: hepa_life, state: 100}
```

The cluster is code driven (connectedhomeip's ResourceMonitoringCluster object), so
the generated code only creates the cluster and its features; the values are pushed
by matter_filter_monitoring.cpp.
"""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome import automation
from esphome.components import binary_sensor, button, sensor
from esphome.const import CONF_TRIGGER_ID

from .const import CONF_WITH_FEATURES
from esphome.core import ID

from .types import MatterFilterMapping, MatterFilterResetTrigger

CONF_CONDITION = "condition"
CONF_DEGRADATION_DIRECTION = "degradation_direction"
CONF_WARNING_LEVEL = "warning_level"
CONF_CRITICAL_LEVEL = "critical_level"
CONF_REPLACE_NEEDED = "replace_needed"
CONF_RESET_BUTTON = "reset_button"
CONF_ON_RESET = "on_reset"

FILTER_CLUSTER_KEYS = ("hepa_filter_monitoring", "activated_carbon_filter_monitoring")

# Default for critical_level when a condition sensor is bound.
DEFAULT_CRITICAL_LEVEL = 10

FILTER_INCLUDE = '#include "esphome/components/matter/matter_filter_monitoring.h"'

DEGRADATION_DIRECTIONS = {"down": 0, "up": 1}

_BINDING_KEYS = (CONF_CONDITION, CONF_REPLACE_NEEDED)
_EXTRA_KEYS = (
    CONF_DEGRADATION_DIRECTION,
    CONF_WARNING_LEVEL,
    CONF_CRITICAL_LEVEL,
    CONF_RESET_BUTTON,
    CONF_ON_RESET,
)


def is_filter_cluster(conf_key: str) -> bool:
    return conf_key in FILTER_CLUSTER_KEYS


def schema_options() -> dict:
    return {
        cv.Optional(CONF_CONDITION): cv.use_id(sensor.Sensor),
        cv.Optional(CONF_DEGRADATION_DIRECTION): cv.one_of(
            *DEGRADATION_DIRECTIONS, lower=True
        ),
        cv.Optional(CONF_WARNING_LEVEL): cv.percentage_int,
        cv.Optional(CONF_CRITICAL_LEVEL): cv.percentage_int,
        cv.Optional(CONF_REPLACE_NEEDED): cv.use_id(binary_sensor.BinarySensor),
        cv.Optional(CONF_RESET_BUTTON): cv.use_id(button.Button),
        cv.Optional(CONF_ON_RESET): automation.validate_automation(
            {cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(MatterFilterResetTrigger)}
        ),
    }


def _feature_names(config) -> set[str]:
    return {str(name) for name in config.get(CONF_WITH_FEATURES, ())}


def validate_cluster(config: dict) -> dict:
    """Validation of one filter monitoring cluster config."""
    bound = any(key in config for key in _BINDING_KEYS)
    if not bound:
        stray = [key for key in _EXTRA_KEYS if key in config]
        if stray:
            raise cv.Invalid(
                f"{', '.join(stray)} need a `{CONF_CONDITION}` sensor or a "
                f"`{CONF_REPLACE_NEEDED}` binary sensor to bind the filter state to"
            )
        return config

    features = _feature_names(config)
    has_condition = CONF_CONDITION in config
    if not has_condition:
        for key in (CONF_DEGRADATION_DIRECTION, CONF_WARNING_LEVEL, CONF_CRITICAL_LEVEL):
            if key in config:
                raise cv.Invalid(f"{key} needs the `{CONF_CONDITION}` sensor")
        if "condition" in features:
            raise cv.Invalid(
                f"The Condition feature needs the `{CONF_CONDITION}` sensor that provides it"
            )
    if CONF_WARNING_LEVEL not in config and "warning" in features:
        raise cv.Invalid(
            f"The Warning feature needs `{CONF_WARNING_LEVEL}`: it is the level at "
            "which ChangeIndication switches to Warning"
        )

    if has_condition:
        direction = config.get(CONF_DEGRADATION_DIRECTION, "down")
        critical = config.get(CONF_CRITICAL_LEVEL)
        warning = config.get(CONF_WARNING_LEVEL)
        if warning is not None and critical is None:
            critical = DEFAULT_CRITICAL_LEVEL if direction == "down" else 100 - DEFAULT_CRITICAL_LEVEL
        if warning is not None and critical is not None:
            ordered = warning > critical if direction == "down" else warning < critical
            if not ordered:
                raise cv.Invalid(
                    f"With degradation_direction {direction} the {CONF_WARNING_LEVEL} "
                    f"({warning}%) must be "
                    f"{'above' if direction == 'down' else 'below'} the "
                    f"{CONF_CRITICAL_LEVEL} ({critical}%)"
                )

    config = dict(config)
    implied = [*config.get(CONF_WITH_FEATURES, ())]
    if has_condition and "condition" not in features:
        implied.append("condition")
    if CONF_WARNING_LEVEL in config and "warning" not in features:
        implied.append("warning")
    config[CONF_WITH_FEATURES] = implied
    return config


def has_reset(config: dict) -> bool:
    return CONF_RESET_BUTTON in config or bool(config.get(CONF_ON_RESET))


def is_bound(config: dict) -> bool:
    return any(key in config for key in _BINDING_KEYS)


def mapping_name(endpoint_id: int, cluster_id: int) -> str:
    return f"matter_filter_{endpoint_id}_{cluster_id}"


def create_lines(cluster, config: dict, endpoint_id: int) -> list[str]:
    """Build callback lines that create the cluster and its features."""
    ns = f"esp_matter::cluster::{cluster.espm_namespace}"
    var = f"{cluster.espm_namespace}_cluster"
    lines = [
        f"{ns}::config_t {cluster.espm_namespace}_config{{}};",
    ]
    if is_bound(config):
        # esp-matter installs the delegate when the cluster is initialized, before
        # Matter serves any request. ResourceMonitoringCluster dereferences it
        # unchecked for ResetCondition, so it must never be missing.
        lines.append(
            f"{cluster.espm_namespace}_config.delegate = "
            "static_cast<chip::app::Clusters::ResourceMonitoring::Delegate *>("
            f"{mapping_name(endpoint_id, cluster.id)});"
        )
    lines += [
        f"auto *{var} = {ns}::create(endpoint, &{cluster.espm_namespace}_config, "
        "esp_matter::CLUSTER_FLAG_SERVER);",
        f"if ({var} == nullptr)",
        "  return false;",
    ]
    features = {str(f) for f in config.get(CONF_WITH_FEATURES, ())}
    if "condition" in features:
        direction = DEGRADATION_DIRECTIONS[config.get(CONF_DEGRADATION_DIRECTION, "down")]
        initial = 100 if direction == 0 else 0
        lines += [
            f"{{",
            f"  {ns}::feature::condition::config_t condition_config{{}};",
            f"  condition_config.condition = {initial};",
            f"  condition_config.degradation_direction = {direction};",
            f"  if ({ns}::feature::condition::add({var}, &condition_config) != ESP_OK)",
            "    return false;",
            "}",
        ]
    if "warning" in features:
        lines += [
            f"if ({ns}::feature::warning::add({var}) != ESP_OK)",
            "  return false;",
        ]
    if "replacement_product_list" in features:
        lines += [
            f"if ({ns}::feature::replacement_product_list::add({var}) != ESP_OK)",
            "  return false;",
        ]
    if has_reset(config):
        # Without the delegate above the command would reach an unset pointer.
        assert is_bound(config)
        lines += [
            f"if ({ns}::command::create_reset_condition({var}) == nullptr)",
            "  return false;",
        ]
    return lines


async def register_filter(var, endpoint_id: int, cluster, config: dict):
    """Registers the runtime mapping for a bound filter monitoring cluster."""
    direction = DEGRADATION_DIRECTIONS[config.get(CONF_DEGRADATION_DIRECTION, "down")]
    warning = config.get(CONF_WARNING_LEVEL, -1)
    if CONF_CONDITION in config:
        critical = config.get(CONF_CRITICAL_LEVEL)
        if critical is None:
            critical = DEFAULT_CRITICAL_LEVEL if direction == 0 else 100 - DEFAULT_CRITICAL_LEVEL
    else:
        critical = -1
    cg.add_define("USE_MATTER_FILTER_MONITORING")
    mapping = cg.new_Pvariable(
        ID(
            mapping_name(endpoint_id, cluster.id),
            is_declaration=True,
            type=MatterFilterMapping,
        ),
        endpoint_id,
        cluster.id,
        cg.RawExpression(f"esphome::matter::FilterLevels{{{direction}, {warning}, {critical}}}"),
    )
    if CONF_CONDITION in config:
        cg.add(mapping.set_condition_sensor(await cg.get_variable(config[CONF_CONDITION])))
    if CONF_REPLACE_NEEDED in config:
        cg.add(
            mapping.set_replace_needed_sensor(
                await cg.get_variable(config[CONF_REPLACE_NEEDED])
            )
        )
    if CONF_RESET_BUTTON in config:
        cg.add(mapping.set_reset_button(await cg.get_variable(config[CONF_RESET_BUTTON])))
    for conf in config.get(CONF_ON_RESET, ()):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID])
        cg.add(mapping.add_reset_trigger(trigger))
        await automation.build_automation(trigger, [], conf)
    cg.add(var.register_mapping(mapping))
