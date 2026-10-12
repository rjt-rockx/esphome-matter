"""Network robustness settings: reconnect speed after restarts and diagnostics.

See docs/troubleshooting-network.md for the reasoning behind these values.
"""

from pathlib import Path

import esphome.codegen as cg
from esphome.components.esp32 import add_idf_sdkconfig_option
from esphome.core import CORE

# CHIP project configuration header, included into every CHIP translation unit.
# It lowers the subscription resumption retry timers (CHIP defaults: 300 s first
# retry, then 300 s * Fibonacci, capped at 6 h).
CHIP_PROJECT_CONFIG_HEADER = Path(__file__).parent / "matter_chip_config.h"


def configure_network(*, debug: bool) -> None:
    from esphome.components.esp32.const import KEY_SDKCONFIG_OPTIONS
    from esphome.const import KEY_ESP32

    sdkconfig = CORE.data[KEY_ESP32][KEY_SDKCONFIG_OPTIONS]
    # Do not clobber a project config header chosen by the user.
    if not sdkconfig.get("CONFIG_CHIP_PROJECT_CONFIG"):
        add_idf_sdkconfig_option(
            "CONFIG_CHIP_PROJECT_CONFIG", str(CHIP_PROJECT_CONFIG_HEADER.resolve())
        )

    # CHIP default (y): persist subscriptions so controllers do not have to
    # re-subscribe after a reboot. Set explicitly so it cannot silently change.
    add_idf_sdkconfig_option("CONFIG_ENABLE_PERSIST_SUBSCRIPTIONS", True)

    if debug:
        cg.add_define("MATTER_DEBUG_NETWORK")
