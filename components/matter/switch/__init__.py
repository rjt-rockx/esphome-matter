"""switch platform backed by Matter.

Usage:

    switch:
      - platform: matter
        matter_id: matter1
        name: "Desk Plug"
        type: on_off_plug_in_unit   # optional
        endpoint: 1                  # optional; auto-allocated otherwise

The Matter component exposes this as an On/Off Plug-in Unit endpoint by
default. The ESPHome side behaves like a virtual switch whose state is the
Matter cluster value; bind it to your own GPIO output with an `on_turn_on` /
`on_turn_off` automation, or use a separate GPIO switch and link them.
"""

import esphome.codegen as cg
from esphome.components import switch as switch_
import esphome.config_validation as cv
from esphome.const import CONF_ID

from .. import (
    CONF_DEVICE_TYPE,
    CONF_ENDPOINT,
    CONF_MATTER_ID,
    DEVICE_TYPE_NAMES,
    MatterComponent,
    matter_ns,
)

DEPENDENCIES = ["matter"]

MatterSwitch = matter_ns.class_("MatterSwitch", switch_.Switch, cg.Component)

CONFIG_SCHEMA = (
    switch_.switch_schema(MatterSwitch)
    .extend(
        {
            cv.GenerateID(CONF_ID): cv.declare_id(MatterSwitch),
            cv.GenerateID(CONF_MATTER_ID): cv.use_id(MatterComponent),
            cv.Optional(CONF_DEVICE_TYPE, default="on_off_plug_in_unit"): cv.enum(
                DEVICE_TYPE_NAMES, lower=True
            ),
            cv.Optional(CONF_ENDPOINT): cv.int_range(min=1, max=65534),
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await switch_.register_switch(var, config)
    await cg.register_component(var, config)

    parent = await cg.get_variable(config[CONF_MATTER_ID])
    cg.add(var.set_parent(parent))
    cg.add(var.set_device_type(config[CONF_DEVICE_TYPE]))
    if CONF_ENDPOINT in config:
        cg.add(var.set_requested_endpoint_id(config[CONF_ENDPOINT]))
    cg.add(parent.register_entity(var))
