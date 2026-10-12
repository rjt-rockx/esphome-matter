"""Commissioning behaviours: `status_led:` and `pairing:` under `matter:`.

See docs/commissioning.md.
"""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import binary_sensor, light, output
from esphome.const import CONF_ID, CONF_LIGHT, CONF_OUTPUT, CONF_TIMEOUT

from .types import matter_ns

CONF_BEHAVIOURS_ID = "behaviours_id"
CONF_STATUS_LED = "status_led"
CONF_COMMISSIONED = "commissioned"
CONF_PAIRING = "pairing"
CONF_BINARY_SENSOR = "binary_sensor"
CONF_OPEN_WINDOW_HOLD = "open_window_hold"
CONF_FACTORY_RESET_HOLD = "factory_reset_hold"

NEVER = "never"

MatterBehaviours = matter_ns.class_("MatterBehaviours", cg.Component)

# The LED shows (matter_behaviours_logic.h):
#   uncommissioned, window closed   1 Hz blink
#   commissioning window open       4 Hz blink
#   commissioned                    `commissioned:` on (default) or off
#   error (window could not open)   two short flashes, pause
#   button held, window armed       2 Hz blink
#   button held, factory reset armed  10 Hz strobe
#   window request accepted         three quick flashes
STATUS_LED_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.Optional(CONF_OUTPUT): cv.use_id(output.BinaryOutput),
            cv.Optional(CONF_LIGHT): cv.use_id(light.LightState),
            cv.Optional(CONF_COMMISSIONED, default=True): cv.boolean,
        }
    ),
    cv.has_exactly_one_key(CONF_OUTPUT, CONF_LIGHT),
)

MIN_OPEN_HOLD = cv.TimePeriod(seconds=1)
MAX_OPEN_HOLD = cv.TimePeriod(seconds=30)
MIN_RESET_HOLD = cv.TimePeriod(seconds=5)
MAX_RESET_HOLD = cv.TimePeriod(seconds=60)
# The factory reset stage must be clearly separated from the window stage so
# the LED confirmation of the first stage is seen before the second arms.
MIN_STAGE_GAP = cv.TimePeriod(seconds=3)


def _validate_pairing(config):
    reset = config[CONF_FACTORY_RESET_HOLD]
    if reset != NEVER:
        gap = (
            reset.total_milliseconds - config[CONF_OPEN_WINDOW_HOLD].total_milliseconds
        )
        if gap < MIN_STAGE_GAP.total_milliseconds:
            raise cv.Invalid(
                f"{CONF_FACTORY_RESET_HOLD} ({reset}) must be at least "
                f"{MIN_STAGE_GAP} longer than {CONF_OPEN_WINDOW_HOLD} "
                f"({config[CONF_OPEN_WINDOW_HOLD]}), so the LED confirms the first "
                "stage before the factory reset arms",
                path=[CONF_FACTORY_RESET_HOLD],
            )
    return config


# The action happens when the button is released, after the LED confirmed the
# armed stage. Use a binary_sensor (a `button:` has no hold duration).
PAIRING_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.Required(CONF_BINARY_SENSOR): cv.use_id(binary_sensor.BinarySensor),
            cv.Optional(CONF_OPEN_WINDOW_HOLD, default="3s"): cv.All(
                cv.positive_time_period_milliseconds,
                cv.Range(min=MIN_OPEN_HOLD, max=MAX_OPEN_HOLD),
            ),
            cv.Optional(CONF_FACTORY_RESET_HOLD, default="10s"): cv.Any(
                cv.one_of(NEVER, lower=True),
                cv.All(
                    cv.positive_time_period_milliseconds,
                    cv.Range(min=MIN_RESET_HOLD, max=MAX_RESET_HOLD),
                ),
            ),
            # Matter allows 3 to 15 minutes.
            cv.Optional(CONF_TIMEOUT, default="5min"): cv.All(
                cv.positive_time_period_seconds,
                cv.Range(min=cv.TimePeriod(minutes=3), max=cv.TimePeriod(minutes=15)),
            ),
        }
    ),
    _validate_pairing,
)

# Extends the top level `matter:` schema.
BEHAVIOURS_SCHEMA = cv.Schema(
    {
        cv.GenerateID(CONF_BEHAVIOURS_ID): cv.declare_id(MatterBehaviours),
        cv.Optional(CONF_STATUS_LED): STATUS_LED_SCHEMA,
        cv.Optional(CONF_PAIRING): PAIRING_SCHEMA,
    }
)


async def behaviours_to_code(config):
    if CONF_STATUS_LED not in config and CONF_PAIRING not in config:
        return
    var = cg.new_Pvariable(config[CONF_BEHAVIOURS_ID])
    await cg.register_component(var, {CONF_ID: config[CONF_BEHAVIOURS_ID]})

    if led := config.get(CONF_STATUS_LED):
        if CONF_OUTPUT in led:
            cg.add(var.set_status_output(await cg.get_variable(led[CONF_OUTPUT])))
        else:
            cg.add(var.set_status_light(await cg.get_variable(led[CONF_LIGHT])))
        cg.add(var.set_status_led_enabled(True, led[CONF_COMMISSIONED]))

    if pairing := config.get(CONF_PAIRING):
        cg.add(
            var.set_pairing_sensor(await cg.get_variable(pairing[CONF_BINARY_SENSOR]))
        )
        reset = pairing[CONF_FACTORY_RESET_HOLD]
        cg.add(
            var.set_pairing(
                pairing[CONF_OPEN_WINDOW_HOLD].total_milliseconds,
                0 if reset == NEVER else reset.total_milliseconds,
                int(pairing[CONF_TIMEOUT].total_seconds),
            )
        )
