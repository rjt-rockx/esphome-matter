"""Binding of ESPHome locks and valves to the Matter Door Lock and Water Valve device types.

Kept in a separate module to keep the changes in the shared endpoint code small. See docs/lock-valve.md.
"""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import lock, valve

CONF_LOCK_ID = "lock_id"
CONF_LOCK_TYPE = "lock_type"
CONF_VALVE_ID = "valve_id"

DEVICE_TYPE_DOOR_LOCK = "door_lock"
DEVICE_TYPE_WATER_VALVE = "water_valve"

# DlLockType enum of the Door Lock cluster.
LOCK_TYPES = {
    "DEAD_BOLT": 0,
    "MAGNETIC": 1,
    "OTHER": 2,
    "MORTISE": 3,
    "RIM": 4,
    "LATCH_BOLT": 5,
    "CYLINDRICAL_LOCK": 6,
    "TUBULAR_LOCK": 7,
    "INTERCONNECTED_LOCK": 8,
    "DEAD_LATCH": 9,
    "DOOR_FURNITURE": 10,
    "EUROCYLINDER": 11,
}


def extend_device_type_schema(device_type_name: str, schema: dict) -> None:
    """Add the entity binding options of the door lock and water valve device types to a device type schema."""
    if device_type_name == DEVICE_TYPE_DOOR_LOCK:
        # Without a lock the door lock cannot answer any command (and the Door Lock server callbacks, which are
        # needed to link the Door Lock cluster, are only provided together with a lock binding).
        schema[cv.Required(CONF_LOCK_ID)] = cv.use_id(lock.Lock)
        schema[cv.Optional(CONF_LOCK_TYPE, default="DEAD_BOLT")] = cv.one_of(
            *LOCK_TYPES, upper=True
        )
    elif device_type_name == DEVICE_TYPE_WATER_VALVE:
        schema[cv.Optional(CONF_VALVE_ID)] = cv.use_id(valve.Valve)


async def register_entity(
    var, endpoint_id: int, device_type_name: str, device_config: dict
) -> None:
    """Register the ESPHome entity bound to an endpoint."""
    if device_type_name == DEVICE_TYPE_DOOR_LOCK and CONF_LOCK_ID in device_config:
        lock_ = await cg.get_variable(device_config[CONF_LOCK_ID])
        # Compiles matter_lock.cpp which defines the Door Lock server callbacks.
        cg.add_define("USE_MATTER_LOCK")
        cg.add(var.register_lock(lock_, endpoint_id))
    elif device_type_name == DEVICE_TYPE_WATER_VALVE and CONF_VALVE_ID in device_config:
        valve_ = await cg.get_variable(device_config[CONF_VALVE_ID])
        cg.add_define("USE_MATTER_VALVE")
        cg.add(var.register_valve(valve_, endpoint_id))


def device_config_lines(
    device_type_name: str,
    config_var: str,
    endpoint_id: int,
    device_config: dict,
) -> list[str]:
    """C++ lines that fill the esp_matter device type config of the door lock and water valve."""
    lines = []
    if device_type_name == DEVICE_TYPE_DOOR_LOCK:
        lines.append(f"{config_var}.door_lock.actuator_enabled = true;")
        lines.append(
            f"{config_var}.door_lock.lock_type = {LOCK_TYPES[device_config[CONF_LOCK_TYPE]]};"
        )
    elif device_type_name == DEVICE_TYPE_WATER_VALVE:
        valve_config = f"{config_var}.valve_configuration_and_control"
        # esp-matter defaults DefaultOpenDuration to 1 second, which would close the valve one second after every
        # Open command that has no explicit duration. Null means "stay open until closed".
        lines.append(f"{valve_config}.default_open_duration = ::nullable<uint32_t>();")
        if CONF_VALVE_ID in device_config:
            lines.append(
                f"{valve_config}.delegate = esphome::matter::get_valve_delegate({endpoint_id});"
            )
    return lines
