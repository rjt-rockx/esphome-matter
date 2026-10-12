"""Matter bridge: an Aggregator endpoint with Bridged Node endpoints below it. See docs/bridge.md.

Two ways to write it. The first is the `bridge:` block, which lowers into `endpoints:`:

    matter:
      bridge:
        devices:
          kitchen_thermometer:                 # key: stays the same when you rename the device
            name: Kitchen Thermometer          # BDBI NodeLabel
            vendor_name: Xiaomi
            product_name: LYWSD03MMC
            reachable: kitchen_thermometer_seen   # binary_sensor id -> BDBI Reachable
            battery: kitchen_battery            # Power Source on the Bridged Node
            temperature_sensor: kitchen_temp    # any Matter device type, bound as in `endpoints:`
            humidity_sensor: kitchen_humidity   # a second device type gives child endpoints

The second is hand-written `endpoints:` with an `aggregator` endpoint and `bridged_node` endpoints (exactly one
aggregator: the bridged nodes below it need no `parent:`):

    matter:
      endpoints:
        1: {aggregator: {}}
        2:
          bridged_node: {name: Relay 1, serial_number: R1}
          on_off_plug_in_unit: {switch_id: relay1}

Everything here adds only the bridge specific parts. The application device types and clusters of a bridged endpoint
are created by the regular endpoint machinery in endpoints.py. esp-matter creates the Descriptor and Bridged Device
Basic Information (BDBI) clusters with Reachable and UniqueID; this module adds NodeLabel and the other identity
attributes, binds Reachable to a binary sensor and checks the shape of the tree. The Descriptor PartsList is computed
by connectedhomeip from the `parent:` links (matter_conformance.cpp): the Aggregator lists all of its descendants.
"""

import difflib
import hashlib
import logging
import re
from collections.abc import Mapping

import esphome.config_validation as cv
from esphome.components import binary_sensor
from esphome.const import CONF_NAME
from esphome.core import CORE

from .const import (
    CONF_ENDPOINT,
    CONF_ENDPOINTS,
    CONF_HARDWARE_VERSION,
    CONF_HARDWARE_VERSION_STRING,
    CONF_LABEL,
    CONF_MATTER,
    CONF_PRODUCT_ID,
    CONF_PRODUCT_NAME,
    CONF_SERIAL_NUMBER,
    CONF_SOFTWARE_VERSION_STRING,
    CONF_UNIQUE_ID,
    CONF_VENDOR_ID,
    CONF_VENDOR_NAME,
)

_LOGGER = logging.getLogger(__name__)

DEVICE_TYPE_AGGREGATOR = "aggregator"
DEVICE_TYPE_BRIDGED_NODE = "bridged_node"

CONF_BRIDGE = "bridge"
CONF_DEVICES = "devices"
CONF_PARTS = "parts"
CONF_NESTED = "nested"
CONF_PRIMARY = "primary"
CONF_REACHABLE = "reachable"
CONF_SOFTWARE_VERSION = "software_version"
CONF_MANUFACTURING_DATE = "manufacturing_date"
CONF_PART_NUMBER = "part_number"
CONF_PRODUCT_URL = "product_url"
CONF_PRODUCT_LABEL = "product_label"
CONF_PARENT = "parent"

# Bridged Device Basic Information cluster (Matter spec 9.13)
BDBI_CLUSTER_ID = 0x39
BDBI_CLUSTER_NAME = "BridgedDeviceBasicInformation"
BDBI_REACHABLE_ATTRIBUTE_ID = 0x11

# (conf key, attribute name, esp-matter create function, maximum length in bytes)
STRING_ATTRIBUTES = (
    (CONF_VENDOR_NAME, "VendorName", "create_vendor_name", 32),
    (CONF_PRODUCT_NAME, "ProductName", "create_product_name", 32),
    (CONF_HARDWARE_VERSION_STRING, "HardwareVersionString", "create_hardware_version_string", 64),
    (CONF_SOFTWARE_VERSION_STRING, "SoftwareVersionString", "create_software_version_string", 64),
    (CONF_MANUFACTURING_DATE, "ManufacturingDate", "create_manufacturing_date", 16),
    (CONF_PART_NUMBER, "PartNumber", "create_part_number", 32),
    (CONF_PRODUCT_URL, "ProductURL", "create_product_url", 254),
    (CONF_PRODUCT_LABEL, "ProductLabel", "create_product_label", 64),
    (CONF_SERIAL_NUMBER, "SerialNumber", "create_serial_number", 32),
)
# (conf key, attribute name, esp-matter create function, C++ type, minimum, maximum)
NUMBER_ATTRIBUTES = (
    (CONF_VENDOR_ID, "VendorID", "create_vendor_id", "uint16_t", 1, 0xFFFE),
    (CONF_PRODUCT_ID, "ProductID", "create_product_id", "uint16_t", 1, 0xFFFE),
    (CONF_HARDWARE_VERSION, "HardwareVersion", "create_hardware_version", "uint16_t", 0, 0xFFFF),
    (CONF_SOFTWARE_VERSION, "SoftwareVersion", "create_software_version", "uint32_t", 0, 0xFFFFFFFF),
)
IDENTITY_KEYS = tuple(a[0] for a in STRING_ATTRIBUTES) + tuple(a[0] for a in NUMBER_ATTRIBUTES)

# BDBI NodeLabel and UniqueID are limited to 32 bytes.
MAX_LABEL_LENGTH = 32

# Controllers list only so many devices of one bridge. Alexa about 50 (reported, unverified), Apple Home 150.
ALEXA_NODE_WARNING = 50
APPLE_NODE_LIMIT = 150

_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_MANUFACTURING_DATE_RE = re.compile(r"^\d{8}")

# Keys of an endpoint that a `bridge: devices:` entry passes through to its endpoint(s).
_PASSTHROUGH_KEYS = ("battery", "wired", CONF_LABEL, "tags", "clusters", "on_attribute")


# esp-matter's generated data model (experimental) has no cluster::...::attribute::create_*() API.
DATA_MODEL_GUARD = (
    '#include "sdkconfig.h"\n'
    "#ifdef CONFIG_ESP_MATTER_ENABLE_GENERATED_DATA_MODEL\n"
    '#error "bridge: needs the legacy esp-matter data model; disable CONFIG_ESP_MATTER_ENABLE_GENERATED_DATA_MODEL"\n'
    "#endif"
)


def _utf8_string(max_bytes: int, min_bytes: int = 1):
    def validator(value):
        value = cv.string_strict(value)
        size = len(value.encode())
        if size < min_bytes or size > max_bytes:
            raise cv.Invalid(
                f"Must be {min_bytes} to {max_bytes} UTF-8 bytes long, got {size} bytes: '{value}'"
            )
        return value

    return validator


def _manufacturing_date(value):
    value = _utf8_string(16)(value)
    if not _MANUFACTURING_DATE_RE.match(value):
        raise cv.Invalid(
            "ManufacturingDate must start with the date as YYYYMMDD, for example '20250131'"
        )
    return value


def identity_options(reachable: bool = True) -> dict:
    """The BDBI identity attributes that can be set on a Bridged Node (static strings and numbers)."""
    options: dict = {}
    for conf_key, _name, _function, max_bytes in STRING_ATTRIBUTES:
        options[cv.Optional(conf_key)] = (
            _manufacturing_date
            if conf_key == CONF_MANUFACTURING_DATE
            else _utf8_string(max_bytes)
        )
    for conf_key, _name, _function, _ctype, low, high in NUMBER_ATTRIBUTES:
        options[cv.Optional(conf_key)] = cv.int_range(min=low, max=high)
    options[cv.Optional(CONF_UNIQUE_ID)] = _utf8_string(MAX_LABEL_LENGTH)
    if reachable:
        # Binary sensor that drives BDBI Reachable (ON = reachable). The node is not reachable until it has a state.
        options[cv.Optional(CONF_REACHABLE)] = cv.use_id(binary_sensor.BinarySensor)
    return options


def extend_schema(device_type_name: str, schema: dict) -> dict:
    """Add the bridge specific options to the schema of the bridged_node device type."""
    if device_type_name != DEVICE_TYPE_BRIDGED_NODE:
        return schema
    schema = dict(schema)
    # NodeLabel: the name controllers show for the bridged device.
    schema[cv.Required(CONF_NAME)] = _utf8_string(MAX_LABEL_LENGTH)
    schema.update(identity_options())
    return schema


# --------------------------------------------------------------------------- #
# The `bridge:` block
# --------------------------------------------------------------------------- #


def _device_key(value):
    value = cv.string_strict(value)
    if not _KEY_RE.match(value) or len(value) > 48:
        raise cv.Invalid(
            f"Bridge device key '{value}' must be 1 to 48 letters, digits, '_' or '-'. "
            "It names the device in the stable endpoint number; use `name:` for the label"
        )
    return value


def _endpoint_number(value):
    return cv.int_range(min=1, max=65534)(value)


_DEVICE_SCHEMA = cv.Schema(
    {
        cv.Optional(CONF_NAME): _utf8_string(MAX_LABEL_LENGTH),
        # Pin the endpoint of the Bridged Node instead of hashing it from the key.
        cv.Optional(CONF_ENDPOINT): _endpoint_number,
        # Pin the child endpoints (device type key -> endpoint) of a device with several device types.
        cv.Optional(CONF_PARTS): cv.Schema({cv.string_strict: _endpoint_number}),
        # One endpoint with the Bridged Node and its application device type (False) or a child endpoint per device
        # type (True). Default: child endpoints when the device has more than one application device type.
        cv.Optional(CONF_NESTED): cv.boolean,
        # The application device type that shares the Bridged Node endpoint; the others become child endpoints.
        cv.Optional(CONF_PRIMARY): cv.string_strict,
        # `reachable:` and the device types stay raw here and are validated once, by ENDPOINT_SCHEMA.
        **identity_options(reachable=False),
    },
    extra=cv.ALLOW_EXTRA,
)

BRIDGE_SCHEMA = cv.Schema(
    {
        # Pin the endpoint of the Aggregator instead of hashing it.
        cv.Optional(CONF_ENDPOINT): _endpoint_number,
        cv.Required(CONF_DEVICES): cv.All(
            cv.Schema({_device_key: lambda v: {} if v is None else v}),
            cv.Length(min=1),
        ),
    }
)


def _known_device_keys() -> set[str]:
    from .data_model.device_types import DEVICE_TYPES_BY_CONF_KEY

    return set(DEVICE_TYPES_BY_CONF_KEY)


def _is_application_device_type(conf_key: str) -> bool:
    from .conformance import UTILITY_DEVICE_TYPE_IDS
    from .data_model.device_types import DEVICE_TYPES_BY_CONF_KEY

    device_type = DEVICE_TYPES_BY_CONF_KEY.get(conf_key)
    return device_type is not None and device_type.id not in UTILITY_DEVICE_TYPE_IDS


def _validate_device(key: str, raw) -> dict:
    """Validate the bridge options of one device; the other keys stay raw for ENDPOINT_SCHEMA."""
    if not isinstance(raw, Mapping):
        raise cv.Invalid(
            f"Bridge device '{key}' must be a mapping with a device type, for example `temperature_sensor: <sensor id>`"
        )
    device = _DEVICE_SCHEMA(dict(raw))
    allowed = _known_device_keys() | set(_PASSTHROUGH_KEYS)
    reserved = {
        DEVICE_TYPE_BRIDGED_NODE: "the Bridged Node is created from the bridge device itself",
        DEVICE_TYPE_AGGREGATOR: "the Aggregator is created from the `bridge:` block",
        CONF_PARENT: "devices always sit directly below the Aggregator",
    }
    handled = set(IDENTITY_KEYS) | {
        CONF_NAME,
        CONF_ENDPOINT,
        CONF_PARTS,
        CONF_NESTED,
        CONF_PRIMARY,
        CONF_UNIQUE_ID,
        CONF_REACHABLE,
    }
    for extra in device:
        if extra in handled:
            continue
        if extra in reserved:
            raise cv.Invalid(f"'{extra}' is not allowed in a bridge device: {reserved[extra]}", path=[extra])
        if extra not in allowed:
            hint = difflib.get_close_matches(str(extra), sorted(allowed), n=1)
            raise cv.Invalid(
                f"'{extra}' is not a bridge device option or Matter device type"
                + (f". Did you mean '{hint[0]}'?" if hint else ""),
                path=[extra],
            )
    application = [k for k in device if _is_application_device_type(k)]
    if not application:
        raise cv.Invalid(
            f"Bridge device '{key}' has no application device type. Add one, for example "
            "`temperature_sensor: <sensor id>` or `on_off_plug_in_unit: {switch_id: <id>}`"
        )
    primary = device.get(CONF_PRIMARY)
    if primary is not None and primary not in application:
        raise cv.Invalid(
            f"`primary: {primary}` is not an application device type of '{key}' ({', '.join(application)})",
            path=[CONF_PRIMARY],
        )
    nested = device.get(CONF_NESTED)
    if nested is None:
        nested = len(application) > 1
    if nested is False and len(application) > 1 and primary is None:
        raise cv.Invalid(
            f"Bridge device '{key}' has {len(application)} application device types ({', '.join(application)}); "
            "they need child endpoints. Remove `nested: false`, or name the device type that shares the Bridged "
            "Node endpoint with `primary:`",
            path=[CONF_NESTED],
        )
    device = dict(device)
    device[CONF_NESTED] = nested
    node_types, child_types = split_device_types(device, application)
    if not node_types:
        for option in ("clusters", "on_attribute"):
            if option in device:
                raise cv.Invalid(
                    f"'{option}' is ambiguous when every device type has its own child endpoint. Name the device "
                    "type that shares the Bridged Node endpoint with `primary:`, or use hand-written `endpoints:`",
                    path=[option],
                )
    unknown = set(device.get(CONF_PARTS, ())) - set(child_types)
    if unknown:
        raise cv.Invalid(
            f"`parts:` names {', '.join(sorted(unknown))}, which {'has' if len(unknown) == 1 else 'have'} no child "
            f"endpoint in '{key}' (child endpoints: {', '.join(child_types) or 'none'})",
            path=[CONF_PARTS],
        )
    return device


def split_device_types(device: dict, application: list[str]) -> tuple[list[str], list[str]]:
    """(device types on the Bridged Node endpoint, device types with a child endpoint)."""
    primary = device.get(CONF_PRIMARY)
    if primary is not None:
        return [primary], [k for k in application if k != primary]
    if device.get(CONF_NESTED):
        return [], list(application)
    return list(application), []


def key_token(seed: str) -> str:
    """8 hex digits that identify a bridged device in its default UniqueID (a hash of the device key)."""
    return hashlib.sha1(seed.encode()).hexdigest()[:8]


def node_seed(endpoint_id: int) -> str:
    """Identity of a Bridged Node for the default UniqueID: the `bridge:` device key, else its endpoint number."""
    keys = CORE.data.get(CONF_MATTER, {}).get("bridge_keys", {})
    return keys.get(endpoint_id) or f"endpoint-{endpoint_id}"


def _humanize(key: str) -> str:
    return re.sub(r"[_-]+", " ", key).strip().title()


def _power_with_endpoints(kind: str, value, endpoints: list[int]):
    """Raw `battery:` / `wired:` value with `powers:` defaulting to the endpoints of the bridged device."""
    if kind == "battery" and not isinstance(value, Mapping):
        value = {"percent": value}
    elif not isinstance(value, Mapping):
        value = {}
    value = dict(value)
    value.setdefault("powers", list(endpoints))
    return value


def _describe(key: str, number: int | str = "N") -> tuple[str, str]:
    """(what a numbering key is, the `bridge:` YAML that pins it) for error messages."""
    if key == "bridge":
        return "the Aggregator", f"bridge: {{endpoint: {number}}}"
    rest = key[len("bridge:") :]
    if "/" in rest:
        device, app = rest.split("/", 1)
        return (
            f"the {app} endpoint of device '{device}'",
            f"bridge: {{devices: {{{device}: {{parts: {{{app}: {number}}}}}}}}}",
        )
    return f"device '{rest}'", f"bridge: {{devices: {{{rest}: {{endpoint: {number}}}}}}}"


def _resolve_numbers(keys: list[str], pins: dict[str, int], reserved: set[int]) -> dict[str, int]:
    """Endpoint number per key: pinned, else hashed. Collisions are errors that say what to pin."""
    from .expose.numbering import _free_suggestion, endpoint_for_key

    def invalid(message):
        return cv.Invalid(f"matter.bridge: {message}", path=[CONF_BRIDGE])

    pinned: dict[int, str] = {}
    for key, number in pins.items():
        what, _ = _describe(key)
        if number in reserved:
            raise invalid(f"endpoint {number} is pinned for {what}, but `endpoints:` already uses it")
        if number in pinned:
            raise invalid(f"endpoint {number} is pinned for both {_describe(pinned[number])[0]} and {what}")
        pinned[number] = key
    hashed: dict[int, list[str]] = {}
    for key in keys:  # YAML order: the last key of a collision is the one added last
        if key not in pins:
            hashed.setdefault(endpoint_for_key(key), []).append(key)
    taken = set(pinned) | reserved | set(hashed)
    for number, who in sorted(hashed.items()):
        if len(who) == 1 and number not in pinned and number not in reserved:
            continue
        newest = who[-1]
        free = _free_suggestion(newest, taken)
        what, pin = _describe(newest, free)
        if number in pinned or number in reserved:
            holder = (
                _describe(pinned[number])[0] if number in pinned else "an endpoint under `endpoints:`"
            )
            raise invalid(
                f"{what} hashes to endpoint {number}, which {holder} already uses. Pin {what} to a free "
                f"number, for example {free}: `{pin}`"
            )
        first = _describe(who[0])[0]
        raise invalid(
            f"{first} and {what} hash to the same endpoint {number}. Pin {what}, the one added last, to a free "
            f"number, for example {free}: `{pin}`. {first[0].upper() + first[1:]} keeps {number}, so nothing "
            "that is already commissioned moves"
        )
    result = dict(pins)
    result.update({who[0]: number for number, who in hashed.items()})
    return result


def lower_bridge(config: dict) -> dict:
    """Lower the `bridge:` block into `endpoints:`. Runs at the tail of CONFIG_SCHEMA, before `expose`."""
    if CONF_BRIDGE not in config:
        return config
    from .endpoints import ENDPOINT_SCHEMA  # avoid an import cycle at module import

    config = dict(config)
    block = config.pop(CONF_BRIDGE)
    devices: dict[str, dict] = {}
    for key, raw in block[CONF_DEVICES].items():
        try:
            devices[key] = _validate_device(key, raw)
        except cv.Invalid as exc:
            raise cv.Invalid(
                f"matter.bridge: device '{key}': {exc.msg}",
                path=[CONF_BRIDGE, CONF_DEVICES, key, *exc.path],
            ) from None

    explicit = dict(config.get(CONF_ENDPOINTS) or {})
    aggregator_key = "bridge"
    keys = [aggregator_key]
    pins: dict[str, int] = {}
    if CONF_ENDPOINT in block:
        pins[aggregator_key] = block[CONF_ENDPOINT]
    layout: dict[str, dict] = {}
    for key, device in devices.items():
        node_key = f"{aggregator_key}:{key}"
        application = [k for k in device if _is_application_device_type(k)]
        keys.append(node_key)
        if CONF_ENDPOINT in device:
            pins[node_key] = device[CONF_ENDPOINT]
        node_types, child_types = split_device_types(device, application)
        part_keys = {}
        for app_key in child_types:
            part_key = f"{node_key}/{app_key}"
            part_keys[app_key] = part_key
            keys.append(part_key)
            if app_key in device.get(CONF_PARTS, {}):
                pins[part_key] = device[CONF_PARTS][app_key]
        layout[key] = {"node": node_key, "parts": part_keys, "node_types": node_types}
    numbers = _resolve_numbers(keys, pins, set(explicit) | {0})

    aggregator = numbers[aggregator_key]
    endpoints = dict(explicit)
    bridge_keys: dict[int, str] = {}
    owners: dict[int, tuple[int, str]] = {}  # endpoint -> (device index, tag key)
    endpoints[aggregator] = ENDPOINT_SCHEMA({DEVICE_TYPE_AGGREGATOR: {}})
    plan_lines = [f"endpoint {aggregator}: Aggregator"]
    for index, (key, device) in enumerate(devices.items()):
        info = layout[key]
        node_number = numbers[info["node"]]
        part_numbers = {k: numbers[v] for k, v in info["parts"].items()}
        node = {
            CONF_NAME: device.get(CONF_NAME) or _humanize(key),
        }
        # Without `unique_id:` the UniqueID is "<node id>-<hash of key>" (matter_conformance.cpp).
        bridge_keys[node_number] = key
        for option in IDENTITY_KEYS + (CONF_REACHABLE,):
            if option in device:
                node[option] = device[option]
        node_config: dict = {DEVICE_TYPE_BRIDGED_NODE: node, CONF_PARENT: aggregator}
        powered = [node_number, *part_numbers.values()]
        for option in _PASSTHROUGH_KEYS:
            if option not in device:
                continue
            value = device[option]
            if option in ("battery", "wired"):
                value = _power_with_endpoints(option, value, powered)
            node_config[option] = value
        # Utility device types (electrical_sensor, ...) stay on the Bridged Node endpoint.
        for conf_key, value in device.items():
            if conf_key in _known_device_keys() and not _is_application_device_type(conf_key):
                node_config[conf_key] = value
        children: dict[int, dict] = {
            number: {app_key: device[app_key], CONF_PARENT: node_number}
            for app_key, number in part_numbers.items()
        }
        for app_key in info["node_types"]:
            node_config[app_key] = device[app_key]
        owners[node_number] = (index, key)
        for app_key, number in part_numbers.items():
            owners[number] = (index, f"{key}/{app_key}")
        try:
            endpoints[node_number] = ENDPOINT_SCHEMA(node_config)
            for number, child in children.items():
                endpoints[number] = ENDPOINT_SCHEMA(child)
        except cv.Invalid as exc:
            where = ".".join(str(p) for p in exc.path)
            raise cv.Invalid(
                f"matter.bridge: device '{key}' failed validation{f' at {where}' if where else ''}: {exc.msg}",
                path=[CONF_BRIDGE, CONF_DEVICES, key],
            ) from None
        plan_lines.append(
            f"endpoint {node_number}: Bridged Node '{node[CONF_NAME]}' ({key})"
            + "".join(f" + {k}" for k in info["node_types"])
            + "".join(f"; {k} on endpoint {n}" for k, n in part_numbers.items())
        )
    _assign_tags(endpoints, owners)
    config[CONF_ENDPOINTS] = dict(sorted(endpoints.items()))
    CORE.data.setdefault(CONF_MATTER, {})["bridge_plan"] = plan_lines
    CORE.data[CONF_MATTER]["bridge_keys"] = bridge_keys
    _LOGGER.info("matter.bridge: %d device(s)\n  %s", len(devices), "\n  ".join(plan_lines))
    return config


def _assign_tags(endpoints: dict[int, dict], owners: dict[int, tuple[int, str]]) -> None:
    """Common Number tags for bridged devices that share an application device type (Duplicate condition).

    The base engine numbers siblings in endpoint-number order, which is a hash here: adding a device would
    renumber the others. The tags are numbered from the device keys instead: a key ending in a number keeps
    that number (relay_2 -> Two), the others take the lowest free numbers in YAML order, and numbers that
    explicit `tags:` use are skipped. Endpoints that have `tags:` are not touched.
    """
    from .conformance import (
        CONF_TAGS,
        NAMESPACE_COMMON_NUMBER,
        NUMBER_TAG_MAX,
        _duplicate_components,
    )
    from .expose.tags import _assign_numbers, _number_tags

    for component in _duplicate_components(endpoints):
        used: set[int] = set()
        for number in component:
            used |= _number_tags(endpoints[number].get(CONF_TAGS))
        mine = sorted(
            (n for n in component if n in owners and CONF_TAGS not in endpoints[n]),
            key=lambda n: owners[n],
        )
        if not mine:
            continue
        names = [owners[n][1] for n in mine]
        assigned, _by_position = _assign_numbers(names, used)
        if max(assigned.values()) > NUMBER_TAG_MAX:
            raise cv.Invalid(
                f"matter.bridge: {len(component)} endpoints ({', '.join(names)}) share a device type and need "
                f"more Common Number tags than the {NUMBER_TAG_MAX} that exist. Give some of the devices "
                "`nested: true` (their Bridged Node endpoints then have no application device type) or `tags:`",
                path=[CONF_BRIDGE],
            )
        for number, name in zip(mine, names):
            endpoints[number][CONF_TAGS] = [
                {"namespace": NAMESPACE_COMMON_NUMBER, "tag": assigned[name]}
            ]
        _LOGGER.info(
            "matter.bridge: %s share a device type: tagged Common Number %s. A key that ends in a number keeps "
            "it; add devices at the end (or end the key with a number) to keep the other tags",
            ", ".join(names),
            ", ".join(str(assigned[n]) for n in names),
        )


def bridge_subtree(endpoints: dict) -> set[int]:
    """The Aggregator endpoints and every endpoint below them."""
    parents = {e: c.get(CONF_PARENT, 0) for e, c in endpoints.items()}
    roots = {e for e, c in endpoints.items() if DEVICE_TYPE_AGGREGATOR in c}
    found = set(roots)
    changed = True
    while changed:
        changed = False
        for endpoint_id, parent in parents.items():
            if parent in found and endpoint_id not in found:
                found.add(endpoint_id)
                changed = True
    return found


# --------------------------------------------------------------------------- #
# Topology
# --------------------------------------------------------------------------- #


def validate_topology(matter_config: dict) -> dict:
    """Check the Aggregator / Bridged Node tree and give Bridged Nodes their parent. Runs on the complete config."""
    endpoints = matter_config.get(CONF_ENDPOINTS, {})
    aggregators = [e for e, c in endpoints.items() if DEVICE_TYPE_AGGREGATOR in c]
    nodes = [e for e, c in endpoints.items() if DEVICE_TYPE_BRIDGED_NODE in c]
    if not aggregators and not nodes:
        return matter_config
    path = [CONF_ENDPOINTS]
    device_type_keys = _known_device_keys()

    for endpoint_id in aggregators:
        config = endpoints[endpoint_id]
        if DEVICE_TYPE_BRIDGED_NODE in config:
            raise cv.Invalid(
                f"Endpoint {endpoint_id} cannot be both an aggregator and a bridged_node",
                path=[*path, endpoint_id],
            )
        others = sorted(
            k
            for k in config
            if k != DEVICE_TYPE_AGGREGATOR and k in device_type_keys and _is_application_device_type(k)
        )
        if others:
            raise cv.Invalid(
                f"Endpoint {endpoint_id} is an aggregator and cannot also be {', '.join(others)}. "
                "Put application device types on a bridged_node endpoint below it.",
                path=[*path, endpoint_id],
            )
        if config.get(CONF_PARENT, 0) != 0:
            raise cv.Invalid(
                f"Aggregator endpoint {endpoint_id} must be a direct child of the root node (no `parent:`). "
                "Nested aggregators are not supported by controllers",
                path=[*path, endpoint_id, CONF_PARENT],
            )

    labels: dict[tuple[int, str], int] = {}
    unique_ids: dict[str, int] = {}
    defaulted: list[int] = []
    for endpoint_id in nodes:
        config = endpoints[endpoint_id]
        node = config[DEVICE_TYPE_BRIDGED_NODE]
        parent = config.get(CONF_PARENT)
        if parent is None:
            if len(aggregators) != 1:
                raise cv.Invalid(
                    f"Bridged node endpoint {endpoint_id} needs `parent:` (an aggregator endpoint) because "
                    f"{'there is no aggregator' if not aggregators else 'there are several aggregators'}. "
                    "Add `aggregator: {}` on another endpoint or use the `bridge:` block",
                    path=[*path, endpoint_id],
                )
            parent = aggregators[0]
            config[CONF_PARENT] = parent
        elif parent not in aggregators:
            raise cv.Invalid(
                f"Bridged node endpoint {endpoint_id} has parent {parent}, which is not an aggregator endpoint. "
                "A Bridged Node must be a direct child of the Aggregator",
                path=[*path, endpoint_id, CONF_PARENT],
            )
        unique_id = unique_id_identity(endpoint_id, node)
        if CONF_UNIQUE_ID not in node:
            defaulted.append(endpoint_id)
        if unique_id in unique_ids:
            raise cv.Invalid(
                f"Bridged nodes on endpoints {unique_ids[unique_id]} and {endpoint_id} have the same "
                f"UniqueID '{unique_id}'. Set `unique_id:` on one of them",
                path=[*path, endpoint_id, DEVICE_TYPE_BRIDGED_NODE, CONF_UNIQUE_ID],
            )
        unique_ids[unique_id] = endpoint_id
        key = (parent, node[CONF_NAME])
        if key in labels:
            _LOGGER.warning(
                "Bridged nodes on endpoints %s and %s both have the name '%s'; controllers will show two "
                "devices with the same name",
                labels[key],
                endpoint_id,
                node[CONF_NAME],
            )
        labels[key] = endpoint_id
        has_application = any(
            _is_application_device_type(k) for k in config if k in device_type_keys
        )
        has_children = any(c.get(CONF_PARENT) == endpoint_id for c in endpoints.values())
        if not has_application and not has_children:
            _LOGGER.warning(
                "Bridged node endpoint %s ('%s') has no application device type and no child endpoints",
                endpoint_id,
                node[CONF_NAME],
            )
    # A bridged node below a bridged node is nonsense; the parent check above already rejects it.

    for endpoint_id in aggregators:
        children = [e for e, c in endpoints.items() if c.get(CONF_PARENT, 0) == endpoint_id]
        if not any(DEVICE_TYPE_BRIDGED_NODE in endpoints[c] for c in children):
            _LOGGER.warning("Aggregator endpoint %s has no bridged_node endpoints", endpoint_id)
        for child in children:
            if DEVICE_TYPE_BRIDGED_NODE not in endpoints[child]:
                _LOGGER.warning(
                    "Endpoint %s sits below aggregator %s but is not a bridged_node; controllers expect "
                    "only Bridged Nodes there and may ignore it",
                    child,
                    endpoint_id,
                )
    if defaulted:
        _LOGGER.warning(
            "Bridged nodes on endpoints %s have no `unique_id:`. Their UniqueID is '<node id>-<hash of %s>': the "
            "node id is created on first boot and kept in flash (erasing flash gives new ids, which controllers "
            "see as new devices). Set `unique_id:` where the devices must survive that%s",
            ", ".join(map(str, defaulted)),
            "the bridge: device key",
            "; hand-written endpoints also change their id when renumbered"
            if any(node_seed(e).startswith("endpoint-") for e in defaulted)
            else "",
        )
    if len(nodes) > ALEXA_NODE_WARNING:
        _LOGGER.warning(
            "%d bridged nodes: Alexa reportedly lists only about %d devices per bridge and Apple Home allows %d "
            "accessories per bridge. Each endpoint also costs RAM; measure on your chip",
            len(nodes),
            ALEXA_NODE_WARNING,
            APPLE_NODE_LIMIT,
        )
    return matter_config


# --------------------------------------------------------------------------- #
# Code generation
# --------------------------------------------------------------------------- #


def unique_id_identity(endpoint_id: int, node: dict) -> str:
    """What identifies the UniqueID at build time: the explicit value, or a placeholder node id plus key hash."""
    return node.get(CONF_UNIQUE_ID) or f"########-{key_token(node_seed(endpoint_id))}"


def _c_string(value: str) -> str:
    out = []
    for byte in value.encode():
        if byte in (0x22, 0x5C, 0x3F):  # " \ ? (trigraphs)
            out.append(f"\\{byte:03o}")
        elif 0x20 <= byte < 0x7F:
            out.append(chr(byte))
        else:
            out.append(f"\\{byte:03o}")
    return '"' + "".join(out) + '"'


def attribute_names(node: dict) -> list[str]:
    """Names of the optional BDBI attributes this module creates on the endpoint."""
    names = ["NodeLabel"]
    for conf_key, name, *_ in STRING_ATTRIBUTES:
        if conf_key in node:
            names.append(name)
    for conf_key, name, *_ in NUMBER_ATTRIBUTES:
        if conf_key in node:
            names.append(name)
    return names


def config_lines(config_var: str, endpoint_id: int, node: dict) -> list[str]:
    """Fill the BDBI part of the esp-matter bridged_node config (UniqueID and the initial Reachable)."""
    bdbi = f"{config_var}.bridged_device_basic_information"
    if CONF_UNIQUE_ID in node:
        unique_id = (
            f"strncpy({bdbi}.unique_id, {_c_string(node[CONF_UNIQUE_ID])}, sizeof({bdbi}.unique_id) - 1);"
        )
    else:
        unique_id = (
            f"esphome::matter::format_bridge_unique_id({bdbi}.unique_id, sizeof({bdbi}.unique_id), "
            f'"{key_token(node_seed(endpoint_id))}");'
        )
    return [
        # Reachable is driven by a binary sensor once it has a state; until then the node is not reachable.
        f"{bdbi}.reachable = {'false' if CONF_REACHABLE in node else 'true'};",
        unique_id,
    ]


def post_create_lines(node: dict) -> list[str]:
    """Create NodeLabel and the identity attributes, which esp-matter's bridged_node::add() leaves out."""
    attribute = "esp_matter::cluster::bridged_device_basic_information::attribute"
    lines = [
        "{",
        f"  auto *bdbi_cluster = esp_matter::cluster::get(endpoint, {BDBI_CLUSTER_ID});",
        "  if (bdbi_cluster == nullptr)",
        "    return false;",
    ]

    def add_string(function: str, value: str):
        literal = _c_string(value)
        # esp-matter copies the value; the length excludes the terminator.
        lines.append(
            f"  if ({attribute}::{function}(bdbi_cluster, const_cast<char *>({literal}), "
            f"{len(value.encode())}) == nullptr)"
        )
        lines.append("    return false;")

    # A NodeLabel that a controller wrote stays in flash and wins over the YAML; a changed `name:` resets it.
    lines.append(
        "  esphome::matter::reset_changed_node_label(esp_matter::endpoint::get_id(endpoint), "
        f"{_c_string(node[CONF_NAME])});"
    )
    add_string("create_node_label", node[CONF_NAME])
    for conf_key, _name, function, _max in STRING_ATTRIBUTES:
        if conf_key in node:
            add_string(function, node[conf_key])
    for conf_key, _name, function, ctype, _low, _high in NUMBER_ATTRIBUTES:
        if conf_key in node:
            lines.append(
                f"  if ({attribute}::{function}(bdbi_cluster, static_cast<{ctype}>({node[conf_key]}U)) == nullptr)"
            )
            lines.append("    return false;")
    lines.append("}")
    return lines


def layout_info(endpoint_id: int, config: dict) -> dict | None:
    """Bridge details of an endpoint for the conformance layout export (layout_dump.py)."""
    node = config.get(DEVICE_TYPE_BRIDGED_NODE)
    if not isinstance(node, Mapping):
        return None
    return {
        "node_label": node[CONF_NAME],
        "unique_id": unique_id_identity(endpoint_id, node),
        "attributes": attribute_names(node),
        "reachable_bound": CONF_REACHABLE in node,
    }
