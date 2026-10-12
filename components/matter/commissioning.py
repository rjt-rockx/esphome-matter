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
CONF_ON_COMMISSIONED = "on_commissioned"
CONF_ON_DECOMMISSIONED = "on_decommissioned"

MatterCommissionedTrigger = matter_ns.class_(
    "MatterCommissionedTrigger", automation.Trigger.template()
)
MatterDecommissionedTrigger = matter_ns.class_(
    "MatterDecommissionedTrigger", automation.Trigger.template()
)
MatterOpenCommissioningWindowAction = matter_ns.class_(
    "MatterOpenCommissioningWindowAction", automation.Action
)
MatterResetIdentityAction = matter_ns.class_(
    "MatterResetIdentityAction", automation.Action
)

# Extends the top level `matter:` schema.
COMMISSIONING_SCHEMA = cv.Schema(
    {
        # Keep the plaintext passcode in flash (like earlier versions did). Only
        # useful if the device must be able to show its original pairing code
        # at any time. Default: only the SPAKE2+ verifier is stored.
        cv.Optional(CONF_PERSIST_PASSCODE, default=False): cv.boolean,
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
    if config[CONF_PERSIST_PASSCODE]:
        cg.add_define("MATTER_PERSIST_PASSCODE")
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
