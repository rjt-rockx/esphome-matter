"""Matter Generic Switch support: switch events and the binary_sensor binding.

A Generic Switch (and any other device type that requires the Switch cluster) is
a stateless programmable button. It has no state to control; it only emits events
(InitialPress, ShortRelease, LongPress, ...) that controllers use as automation
triggers. Events are only generated when the matching Switch cluster feature is
enabled on the endpoint, so the `features` of the endpoint must match the events
that are sent:

  =============================  ===========================================
  Feature                        Events
  =============================  ===========================================
  MomentarySwitch (default)      initial_press
  MomentarySwitchRelease         short_release
  MomentarySwitchLongPress       long_press, long_release
  MomentarySwitchMultiPress      multi_press_ongoing, multi_press_complete
  LatchingSwitch                 switch_latched
  =============================  ===========================================
"""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome import automation
from esphome.components.binary_sensor import BinarySensor
from esphome.const import CONF_COUNT, CONF_POSITION
from esphome.core import CORE, ID
from esphome.types import ConfigType

from .actions import _resolve_endpoint_id
from .const import CONF_ENDPOINT, CONF_FEATURES
from .types import MatterEndpointRef, matter_ns

CONF_BINARY_SENSOR_ID = "binary_sensor_id"
CONF_LONG_PRESS_TIME = "long_press_time"
CONF_MULTI_PRESS_WINDOW = "multi_press_window"
CONF_MULTI_PRESS_MAX = "multi_press_max"
CONF_EVENT = "event"

MatterSwitchEvent = matter_ns.enum("MatterSwitchEvent", is_class=True)
MatterGenericSwitchMapping = matter_ns.class_("MatterGenericSwitchMapping", cg.Component)
MatterSendSwitchEventAction = matter_ns.class_(
    "MatterSendSwitchEventAction", automation.Action
)

SWITCH_EVENTS = {
    "switch_latched": MatterSwitchEvent.SWITCH_LATCHED,
    "initial_press": MatterSwitchEvent.INITIAL_PRESS,
    "long_press": MatterSwitchEvent.LONG_PRESS,
    "short_release": MatterSwitchEvent.SHORT_RELEASE,
    "long_release": MatterSwitchEvent.LONG_RELEASE,
    "multi_press_ongoing": MatterSwitchEvent.MULTI_PRESS_ONGOING,
    "multi_press_complete": MatterSwitchEvent.MULTI_PRESS_COMPLETE,
}

DEFAULT_LONG_PRESS_TIME = "500ms"
DEFAULT_MULTI_PRESS_WINDOW = "300ms"
DEFAULT_MULTI_PRESS_MAX = 2

# Features enabled when `features` is not configured.
_DEFAULT_FEATURES = ("MomentarySwitchRelease",)
_DEFAULT_BOUND_FEATURES = (
    "MomentarySwitchRelease",
    "MomentarySwitchLongPress",
    "MomentarySwitchMultiPress",
)


def switch_schema_options() -> dict:
    """Extra endpoint config options for device types with a Switch cluster."""
    return {
        cv.Optional(CONF_BINARY_SENSOR_ID): cv.use_id(BinarySensor),
        cv.Optional(CONF_LONG_PRESS_TIME): cv.All(
            cv.positive_time_period_milliseconds,
            cv.Range(min=cv.TimePeriod(milliseconds=50)),
        ),
        cv.Optional(CONF_MULTI_PRESS_WINDOW): cv.All(
            cv.positive_time_period_milliseconds,
            cv.Range(min=cv.TimePeriod(milliseconds=50)),
        ),
        cv.Optional(CONF_MULTI_PRESS_MAX): cv.int_range(min=2, max=255),
    }


def apply_switch_defaults(config: dict) -> dict:
    """Select the default Switch features and validate the switch options.

    The mandatory MomentarySwitch/LatchingSwitch choice is resolved by the generic
    device type validation. This only adds the optional features that make
    the endpoint useful out of the box.
    """
    config = dict(config)
    if CONF_FEATURES not in config:
        config[CONF_FEATURES] = list(
            _DEFAULT_BOUND_FEATURES
            if CONF_BINARY_SENSOR_ID in config
            else _DEFAULT_FEATURES
        )
    features = config[CONF_FEATURES]
    latching = "LatchingSwitch" in features
    for conf, feature in (
        (CONF_LONG_PRESS_TIME, "MomentarySwitchLongPress"),
        (CONF_MULTI_PRESS_WINDOW, "MomentarySwitchMultiPress"),
        (CONF_MULTI_PRESS_MAX, "MomentarySwitchMultiPress"),
    ):
        if conf in config and feature not in features:
            raise cv.Invalid(
                f"{conf} requires the {feature} feature to be enabled in `features`",
                path=[conf],
            )
    if latching and any(
        conf in config
        for conf in (CONF_LONG_PRESS_TIME, CONF_MULTI_PRESS_WINDOW, CONF_MULTI_PRESS_MAX)
    ):
        raise cv.Invalid(
            "long_press_time, multi_press_window and multi_press_max only apply to "
            "momentary switches"
        )
    if latching and CONF_FEATURES in config:
        momentary_only = [
            feature
            for feature in features
            if feature.startswith("MomentarySwitch") or feature == "ActionSwitch"
        ]
        if momentary_only:
            raise cv.Invalid(
                "LatchingSwitch cannot be combined with the momentary switch "
                f"features: {', '.join(momentary_only)}",
                path=[CONF_FEATURES],
            )
    if "ActionSwitch" in features and "MomentarySwitchRelease" in features:
        raise cv.Invalid(
            "ActionSwitch cannot be combined with MomentarySwitchRelease",
            path=[CONF_FEATURES],
        )
    return config


def multi_press_max_line(config_var: str, device_config: dict) -> str | None:
    """C++ line that sets MultiPressMax in the device type config, if configured."""
    if "MomentarySwitchMultiPress" not in device_config.get(CONF_FEATURES, ()):
        return None
    value = device_config.get(CONF_MULTI_PRESS_MAX, DEFAULT_MULTI_PRESS_MAX)
    return (
        f"{config_var}.switch_cluster.features.momentary_switch_multi_press."
        f"multi_press_max = {value};"
    )


async def register_switch_binding(var, endpoint_id: int, device_config: dict):
    """Create the binary_sensor -> Switch events mapping of an endpoint."""
    if CONF_BINARY_SENSOR_ID not in device_config:
        return
    sensor = await cg.get_variable(device_config[CONF_BINARY_SENSOR_ID])
    long_press_time = device_config.get(
        CONF_LONG_PRESS_TIME, cv.positive_time_period_milliseconds(DEFAULT_LONG_PRESS_TIME)
    )
    window = device_config.get(
        CONF_MULTI_PRESS_WINDOW,
        cv.positive_time_period_milliseconds(DEFAULT_MULTI_PRESS_WINDOW),
    )
    mapping_id = cv.declare_id(MatterGenericSwitchMapping)(
        f"matter_generic_switch_mapping_{endpoint_id}"
    )
    mapping = cg.new_Pvariable(
        mapping_id,
        sensor,
        endpoint_id,
        long_press_time.total_milliseconds,
        window.total_milliseconds,
    )
    # The ID is created here and not declared in the config, so mark it as a component.
    CORE.component_ids.add(mapping_id.id)
    await cg.register_component(mapping, {})
    cg.add(var.register_mapping(mapping))


# ------------------------------------------------ #
#  matter.send_switch_event                        #
# ------------------------------------------------ #


@automation.register_action(
    "matter.send_switch_event",
    MatterSendSwitchEventAction,
    cv.Schema(
        {
            cv.Required(CONF_ENDPOINT): cv.Any(
                cv.use_id(MatterEndpointRef), cv.uint16_t
            ),
            cv.Required(CONF_EVENT): cv.enum(SWITCH_EVENTS, lower=True),
            cv.Optional(CONF_POSITION, default=1): cv.templatable(cv.uint8_t),
            cv.Optional(CONF_COUNT, default=1): cv.templatable(cv.uint8_t),
        }
    ),
    synchronous=True,
)
async def matter_send_switch_event_to_code(
    config: ConfigType, action_id: ID, template_arg, args
):
    var = cg.new_Pvariable(action_id, template_arg)
    cg.add(var.set_endpoint_id(_resolve_endpoint_id(config[CONF_ENDPOINT])))
    cg.add(var.set_event(config[CONF_EVENT]))
    cg.add(var.set_position(await cg.templatable(config[CONF_POSITION], args, cg.uint8)))
    cg.add(var.set_count(await cg.templatable(config[CONF_COUNT], args, cg.uint8)))
    return var
