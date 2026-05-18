import esphome.codegen as cg

CODEOWNERS = ["@rjt-rockx"]

matter_ns = cg.esphome_ns.namespace("matter")
MatterComponent = matter_ns.class_("MatterComponent", cg.Component)
MatterEntity = matter_ns.class_("MatterEntity")
MatterSwitch = matter_ns.class_("MatterSwitch", MatterEntity)

KEY_MATTER = "matter"
KEY_ENDPOINTS = "endpoints"

CONF_MATTER = "matter"
CONF_MATTER_ID = "matter_id"
CONF_VENDOR_ID = "vendor_id"
CONF_PRODUCT_ID = "product_id"
CONF_DISCRIMINATOR = "discriminator"
CONF_PASSCODE = "passcode"
CONF_ENDPOINT = "endpoint"
CONF_DEVICE_TYPE = "type"
CONF_ON_COMMISSION = "on_commission"

# CSA-issued test credentials. Triggers an "uncertified accessory" warning
# in Apple Home but works for development.
DEFAULT_VENDOR_ID = 0xFFF2
DEFAULT_PRODUCT_ID = 0x8001
DEFAULT_DISCRIMINATOR = 3840
DEFAULT_PASSCODE = 20202021

# Matter device type IDs (subset for v0)
DEVICE_TYPE_ON_OFF_PLUG_IN_UNIT = 0x010A
DEVICE_TYPE_ON_OFF_LIGHT = 0x0100
DEVICE_TYPE_DIMMABLE_LIGHT = 0x0101
DEVICE_TYPE_COLOR_TEMPERATURE_LIGHT = 0x010C
DEVICE_TYPE_EXTENDED_COLOR_LIGHT = 0x010D
DEVICE_TYPE_CONTACT_SENSOR = 0x0015
DEVICE_TYPE_OCCUPANCY_SENSOR = 0x0107
DEVICE_TYPE_WATER_LEAK_DETECTOR = 0x0043
DEVICE_TYPE_GENERIC_SWITCH = 0x000F
DEVICE_TYPE_TEMPERATURE_SENSOR = 0x0302
DEVICE_TYPE_HUMIDITY_SENSOR = 0x0307
DEVICE_TYPE_LIGHT_SENSOR = 0x0106
DEVICE_TYPE_PRESSURE_SENSOR = 0x0305

# String names accepted in the YAML `matter: {type: ...}` override
DEVICE_TYPE_NAMES = {
    "on_off_plug_in_unit": DEVICE_TYPE_ON_OFF_PLUG_IN_UNIT,
    "on_off_light": DEVICE_TYPE_ON_OFF_LIGHT,
    "dimmable_light": DEVICE_TYPE_DIMMABLE_LIGHT,
    "color_temperature_light": DEVICE_TYPE_COLOR_TEMPERATURE_LIGHT,
    "extended_color_light": DEVICE_TYPE_EXTENDED_COLOR_LIGHT,
    "contact_sensor": DEVICE_TYPE_CONTACT_SENSOR,
    "occupancy_sensor": DEVICE_TYPE_OCCUPANCY_SENSOR,
    "water_leak_detector": DEVICE_TYPE_WATER_LEAK_DETECTOR,
    "generic_switch": DEVICE_TYPE_GENERIC_SWITCH,
    "temperature_sensor": DEVICE_TYPE_TEMPERATURE_SENSOR,
    "humidity_sensor": DEVICE_TYPE_HUMIDITY_SENSOR,
    "light_sensor": DEVICE_TYPE_LIGHT_SENSOR,
    "pressure_sensor": DEVICE_TYPE_PRESSURE_SENSOR,
}
