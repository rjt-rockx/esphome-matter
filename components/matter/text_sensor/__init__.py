import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import text_sensor
from esphome.const import (
    CONF_DISABLED_BY_DEFAULT,
    CONF_TYPE,
    ENTITY_CATEGORY_DIAGNOSTIC,
)

from ..types import matter_ns

DEPENDENCIES = ["matter"]

MatterTextSensor = matter_ns.class_(
    "MatterTextSensor", text_sensor.TextSensor, cg.Component
)
MatterTextSensorType = matter_ns.enum("MatterTextSensorType", is_class=True)

TYPES = {
    "pairing_code": MatterTextSensorType.PAIRING_CODE,
    "qr_code": MatterTextSensorType.QR_CODE,
    "commissioning_state": MatterTextSensorType.COMMISSIONING_STATE,
    "fabric_count": MatterTextSensorType.FABRIC_COUNT,
}

# The pairing and QR code sensors expose a secret (only while a commissioning
# window is open). They are disabled by default so that Home Assistant does not
# record them; enable them explicitly.
_SECRET_TYPES = ("pairing_code", "qr_code")


def _default_disabled(config):
    # Runs before the schema fills in its own default.
    if (
        isinstance(config, dict)
        and str(config.get(CONF_TYPE, "")).lower() in _SECRET_TYPES
        and CONF_DISABLED_BY_DEFAULT not in config
    ):
        config = {**config, CONF_DISABLED_BY_DEFAULT: True}
    return config


CONFIG_SCHEMA = cv.All(
    _default_disabled,
    text_sensor.text_sensor_schema(
        MatterTextSensor, entity_category=ENTITY_CATEGORY_DIAGNOSTIC
    )
    .extend({cv.Required(CONF_TYPE): cv.one_of(*TYPES, lower=True)})
    .extend(cv.COMPONENT_SCHEMA),
)


async def to_code(config):
    var = await text_sensor.new_text_sensor(config)
    await cg.register_component(var, config)
    cg.add(var.set_type(TYPES[config[CONF_TYPE]]))
