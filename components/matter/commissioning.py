"""Commissioning UX: passcode storage, automations and actions.

See docs/commissioning.md.
"""

import logging

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome import automation
from esphome.components.esp32 import add_idf_sdkconfig_option
from esphome.const import CONF_TIMEOUT, CONF_TRIGGER_ID

from .const import CONF_PASSCODE
from .types import MatterComponent, matter_ns

_LOGGER = logging.getLogger(__name__)

CONF_PERSIST_PASSCODE = "persist_passcode"
PERSIST_UNTIL_COMMISSIONED = "until_commissioned"
CONF_ON_COMMISSIONED = "on_commissioned"
CONF_ON_DECOMMISSIONED = "on_decommissioned"
CONF_FABRIC_INDEX = "fabric_index"

MatterCommissionedTrigger = matter_ns.class_(
    "MatterCommissionedTrigger", automation.Trigger.template()
)
MatterDecommissionedTrigger = matter_ns.class_(
    "MatterDecommissionedTrigger", automation.Trigger.template()
)
MatterOpenCommissioningWindowAction = matter_ns.class_(
    "MatterOpenCommissioningWindowAction", automation.Action
)
MatterRemoveFabricAction = matter_ns.class_(
    "MatterRemoveFabricAction", automation.Action
)
MatterResetIdentityAction = matter_ns.class_(
    "MatterResetIdentityAction", automation.Action
)

# Values of the MATTER_PERSIST_PASSCODE_MODE define (matter_commissioning.h).
_PERSIST_MODE = {False: 0, True: 1, PERSIST_UNTIL_COMMISSIONED: 2}

_validate_persist_passcode = cv.Any(
    cv.one_of(PERSIST_UNTIL_COMMISSIONED, lower=True), cv.boolean
)

# Extends the top level `matter:` schema.
COMMISSIONING_SCHEMA = cv.Schema(
    {
        # Where the plaintext passcode lives:
        #   until_commissioned (default): in flash while the device has no
        #     fabric, so the printed code survives power cycles. Erased on the
        #     first commissioning; a new one is generated when the last fabric
        #     is removed or on factory reset.
        #   true: always in flash (a sticker that stays valid forever).
        #   false: RAM only; every boot of an uncommissioned device shows a new
        #     code.
        # Only the SPAKE2+ verifier is stored once the passcode is erased.
        cv.Optional(
            CONF_PERSIST_PASSCODE, default=PERSIST_UNTIL_COMMISSIONED
        ): _validate_persist_passcode,
        cv.Optional(CONF_ON_COMMISSIONED): automation.validate_automation(
            {cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(MatterCommissionedTrigger)}
        ),
        cv.Optional(CONF_ON_DECOMMISSIONED): automation.validate_automation(
            {
                cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(
                    MatterDecommissionedTrigger
                )
            }
        ),
    }
)


async def commissioning_to_code(config):
    # The passcode is served by a custom CommissionableDataProvider
    # (matter_commissioning.cpp) instead of CHIP's legacy NVS based provider.
    add_idf_sdkconfig_option("CONFIG_CUSTOM_COMMISSIONABLE_DATA_PROVIDER", True)
    cg.add_define(
        "MATTER_PERSIST_PASSCODE_MODE", _PERSIST_MODE[config[CONF_PERSIST_PASSCODE]]
    )
    if CONF_PASSCODE in config:
        _LOGGER.warning(
            "matter: `passcode` is set in the YAML. It is compiled into the firmware and "
            "shared by every device built from this config. Omit it to get a random "
            "per-device passcode."
        )
    for conf in config.get(CONF_ON_COMMISSIONED, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID])
        await automation.build_automation(trigger, [], conf)
    for conf in config.get(CONF_ON_DECOMMISSIONED, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID])
        await automation.build_automation(trigger, [], conf)


@automation.register_action(
    "matter.open_commissioning_window",
    MatterOpenCommissioningWindowAction,
    cv.Schema(
        {
            cv.GenerateID(): cv.use_id(MatterComponent),
            # Matter allows 3 to 15 minutes. Values from lambdas are clamped.
            cv.Optional(CONF_TIMEOUT, default="5min"): cv.templatable(
                cv.All(
                    cv.positive_time_period_seconds,
                    cv.Range(
                        min=cv.TimePeriod(minutes=3), max=cv.TimePeriod(minutes=15)
                    ),
                )
            ),
        }
    ),
    synchronous=True,
)
async def matter_open_commissioning_window_to_code(
    config, action_id, template_arg, args
):
    var = cg.new_Pvariable(action_id, template_arg)
    timeout = await cg.templatable(config[CONF_TIMEOUT], args, cg.uint32)
    if isinstance(timeout, cv.TimePeriod):
        timeout = int(timeout.total_seconds)
    cg.add(var.set_timeout(timeout))
    return var


@automation.register_action(
    "matter.reset_identity",
    MatterResetIdentityAction,
    cv.Schema({cv.GenerateID(): cv.use_id(MatterComponent)}),
    synchronous=True,
)
async def matter_reset_identity_to_code(config, action_id, template_arg, args):
    return cg.new_Pvariable(action_id, template_arg)


@automation.register_action(
    "matter.remove_fabric",
    MatterRemoveFabricAction,
    cv.Schema(
        {
            cv.GenerateID(): cv.use_id(MatterComponent),
            # FabricIndex is 1..254; the `controllers` text sensor lists them.
            cv.Required(CONF_FABRIC_INDEX): cv.templatable(cv.int_range(1, 254)),
        }
    ),
    synchronous=True,
)
async def matter_remove_fabric_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    cg.add(
        var.set_fabric_index(
            await cg.templatable(config[CONF_FABRIC_INDEX], args, cg.uint8)
        )
    )
    return var
