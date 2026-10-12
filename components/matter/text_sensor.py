"""Expose Matter commissioning information through the native ESPHome API.

One `text_sensor` platform with optional sub-sensors:

* `manual_pairing_code`, `qr_code`: setup codes (disabled by default, they are secrets).
  Empty unless a commissioning window is open and the passcode is known.
* `commissioning_state`: uncommissioned | commissioning | window_open | commissioned
* `fabric_count`: number of commissioned fabrics
* `fabric_1` ... `fabric_N`: details of a commissioned fabric (compressed_fabric_id,
  fabric_id, label, node_id, vendor_id)
"""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import text_sensor
from esphome.const import (
    CONF_DISABLED_BY_DEFAULT,
    CONF_ID,
    ENTITY_CATEGORY_DIAGNOSTIC,
)

from .const import (
    CONF_COMPRESSED_FABRIC_ID,
    CONF_FABRIC_ID,
    CONF_LABEL,
    CONF_MANUAL_PAIRING_CODE,
    CONF_NODE_ID,
    CONF_QR_CODE,
    CONF_VENDOR_ID,
)
from .types import MatterComponent, matter_ns

DEPENDENCIES = ["matter"]

FABRIC_KEYS = tuple(f"fabric_{index}" for index in range(1, 256))

CONF_COMMISSIONING_STATE = "commissioning_state"
CONF_FABRIC_COUNT = "fabric_count"

MatterTextSensor = matter_ns.class_(
    "MatterTextSensor", text_sensor.TextSensor, cg.Component
)
MatterTextSensorType = matter_ns.enum("MatterTextSensorType", is_class=True)

# Sensors driven by MatterCommissioning (matter_text_sensor.cpp): key -> (type, icon, secret)
COMMISSIONING_SENSORS = {
    CONF_MANUAL_PAIRING_CODE: (MatterTextSensorType.PAIRING_CODE, "mdi:key", True),
    CONF_QR_CODE: (MatterTextSensorType.QR_CODE, "mdi:qrcode", True),
    CONF_COMMISSIONING_STATE: (
        MatterTextSensorType.COMMISSIONING_STATE,
        "mdi:state-machine",
        False,
    ),
    CONF_FABRIC_COUNT: (MatterTextSensorType.FABRIC_COUNT, "mdi:counter", False),
}


def _commissioning_sensor_schema(icon: str, secret: bool):
    schema = cv.Schema(
        text_sensor.text_sensor_schema(
            MatterTextSensor, icon=icon, entity_category=ENTITY_CATEGORY_DIAGNOSTIC
        ).extend(cv.COMPONENT_SCHEMA)
    )

    def default_disabled(config):
        # The setup codes are secrets that are only valid while a commissioning
        # window is open: do not let Home Assistant record them by default.
        if isinstance(config, dict) and CONF_DISABLED_BY_DEFAULT not in config:
            config = {**config, CONF_DISABLED_BY_DEFAULT: True}
        return config

    return cv.All(default_disabled, schema) if secret else schema


FABRIC_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.Optional(CONF_COMPRESSED_FABRIC_ID): text_sensor.text_sensor_schema(
                icon="mdi:identifier",
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_FABRIC_ID): text_sensor.text_sensor_schema(
                icon="mdi:identifier",
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_LABEL): text_sensor.text_sensor_schema(
                icon="mdi:label",
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_NODE_ID): text_sensor.text_sensor_schema(
                icon="mdi:identifier",
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
            cv.Optional(CONF_VENDOR_ID): text_sensor.text_sensor_schema(
                icon="mdi:identifier",
                entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
            ),
        }
    ),
    cv.has_at_least_one_key(
        CONF_COMPRESSED_FABRIC_ID,
        CONF_FABRIC_ID,
        CONF_LABEL,
        CONF_NODE_ID,
        CONF_VENDOR_ID,
    ),
)

CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.use_id(MatterComponent),
            **{
                cv.Optional(key): _commissioning_sensor_schema(icon, secret)
                for key, (_, icon, secret) in COMMISSIONING_SENSORS.items()
            },
            **{cv.Optional(key): FABRIC_SCHEMA for key in FABRIC_KEYS},
        }
    ),
    cv.has_at_least_one_key(*COMMISSIONING_SENSORS, *FABRIC_KEYS),
)


async def to_code(config):
    parent = await cg.get_variable(config[CONF_ID])

    for key, (sensor_type, _, _) in COMMISSIONING_SENSORS.items():
        if sensor_config := config.get(key):
            sens = await text_sensor.new_text_sensor(sensor_config)
            await cg.register_component(sens, sensor_config)
            cg.add(sens.set_type(sensor_type))

    for slot, key in enumerate(FABRIC_KEYS):
        if fabric_config := config.get(key):
            if sensor_config := fabric_config.get(CONF_COMPRESSED_FABRIC_ID):
                sens = await text_sensor.new_text_sensor(sensor_config)
                cg.add(parent.set_fabric_compressed_id_sensor(slot, sens))
            if sensor_config := fabric_config.get(CONF_FABRIC_ID):
                sens = await text_sensor.new_text_sensor(sensor_config)
                cg.add(parent.set_fabric_id_sensor(slot, sens))
            if sensor_config := fabric_config.get(CONF_LABEL):
                sens = await text_sensor.new_text_sensor(sensor_config)
                cg.add(parent.set_fabric_label_sensor(slot, sens))
            if sensor_config := fabric_config.get(CONF_NODE_ID):
                sens = await text_sensor.new_text_sensor(sensor_config)
                cg.add(parent.set_fabric_node_id_sensor(slot, sens))
            if sensor_config := fabric_config.get(CONF_VENDOR_ID):
                sens = await text_sensor.new_text_sensor(sensor_config)
                cg.add(parent.set_fabric_vendor_id_sensor(slot, sens))
