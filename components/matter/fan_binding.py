"""Codegen helpers for binding the Matter Fan Control cluster to an ESPHome entity.

A Fan Control cluster can be bound to

* an ESPHome fan (``fan_id``): the fan traits select the MultiSpeed, Rocking and
  AirflowDirection features; Wind maps to fan preset modes and Step to the speed;
* an ESPHome climate (``climate_id`` in ``clusters: fan_control:``): the climate fan
  modes drive FanMode / FanModeSequence / PercentSetting and the swing modes drive
  Rocking. Used for room air conditioners that have no separate fan entity.

``fan_id`` is accepted in every device type config that can contain the Fan Control
server cluster (fan, air_purifier, extractor_hood, room_air_conditioner,
microwave_oven) and in ``clusters: fan_control:`` on any endpoint. ``climate_id`` is
only accepted in ``clusters: fan_control:``.
"""

import logging
from dataclasses import dataclass

import esphome.config_validation as cv
from esphome.components import climate, fan
from esphome.config import Config
from esphome.const import CONF_RESTORE_MODE

from .const import CONF_CLUSTERS, CONF_ENDPOINTS, CONF_WITH_FEATURES

_LOGGER = logging.getLogger(__name__)

CONF_FAN_ID = "fan_id"
CONF_CLIMATE_ID = "climate_id"
CONF_WIND_PRESETS = "wind_presets"
CONF_NATURAL = "natural"
CONF_SLEEP = "sleep"

FAN_CONTROL = "FanControl"
FAN_CONTROL_KEY = "fan_control"

DEFAULT_NATURAL_PRESET = "natural"
DEFAULT_SLEEP_PRESET = "sleep"

# Fan Control features that are selected from the ESPHome fan traits.
TRAIT_FEATURES = ("MultiSpeed", "Rocking", "AirflowDirection")
# Features that are derived from the climate fan and swing modes.
CLIMATE_FEATURES = ("Auto", "MultiSpeed", "Rocking", "AirflowDirection", "Wind")
# Features that only work with a bound entity: nothing would handle them otherwise.
BOUND_ONLY_FEATURES = ("Wind", "Step")


def fan_id_schema():
    return cv.use_id(fan.Fan)


def _wind_presets_schema():
    return cv.All(
        cv.Schema(
            {
                cv.Optional(CONF_NATURAL): cv.string_strict,
                cv.Optional(CONF_SLEEP): cv.string_strict,
            }
        ),
        cv.has_at_least_one_key(CONF_NATURAL, CONF_SLEEP),
    )


def device_options() -> dict:
    """Schema keys added to every device type that can contain Fan Control."""
    return {
        cv.Optional(CONF_FAN_ID): fan_id_schema(),
        cv.Optional(CONF_WIND_PRESETS): _wind_presets_schema(),
    }


def cluster_options() -> dict:
    """Schema keys of `clusters: fan_control:`."""
    return device_options() | {cv.Optional(CONF_CLIMATE_ID): cv.use_id(climate.Climate)}


def _normalized(features) -> set[str]:
    return {
        "".join(part[:1].upper() + part[1:] for part in str(name).split("_"))
        for name in features
    }


def validate_fan_features(config):
    """The trait based features can't be selected for a bound fan."""
    if CONF_FAN_ID in config:
        trait_features = sorted(
            _normalized(config.get(CONF_WITH_FEATURES, ())) & set(TRAIT_FEATURES)
        )
        if trait_features:
            raise cv.Invalid(
                f"The features {', '.join(trait_features)} follow the capabilities of "
                f"the fan and can't be set together with {CONF_FAN_ID}"
            )
    return config


def validate_cluster_options(config):
    """Validation of the `clusters: fan_control:` mapping."""
    if CONF_FAN_ID in config and CONF_CLIMATE_ID in config:
        raise cv.Invalid(
            f"{CONF_FAN_ID} and {CONF_CLIMATE_ID} can't be used together: the Fan "
            "Control cluster is bound to either a fan or the fan modes of a climate"
        )
    features = _normalized(config.get(CONF_WITH_FEATURES, ()))
    if CONF_CLIMATE_ID in config:
        derived = sorted(features & set(CLIMATE_FEATURES))
        if derived:
            raise cv.Invalid(
                f"The features {', '.join(derived)} are derived from the fan and "
                f"swing modes of the climate when {CONF_CLIMATE_ID} is set and must "
                "not be configured"
            )
        if CONF_WIND_PRESETS in config:
            raise cv.Invalid(
                f"{CONF_WIND_PRESETS} needs {CONF_FAN_ID}: a climate has no preset "
                "modes to map the Wind feature to"
            )
    return validate_fan_features(config)


@dataclass(frozen=True)
class FanBinding:
    kind: str  # "fan" or "climate"
    entity_id: object  # the ESPHome ID
    # Preset names of the Wind feature (None = feature off).
    wind_natural: str | None = None
    wind_sleep: str | None = None
    step: bool = False

    @property
    def wind(self) -> bool:
        return self.wind_natural is not None or self.wind_sleep is not None


def normalize_endpoint(config: dict, device_types_by_key: dict) -> dict:
    """Validates the Fan Control binding of one endpoint and normalizes its config.

    * at most one binding (device level `fan_id` or `clusters: fan_control:`);
    * Wind and Step need a bound entity (nothing would handle them otherwise);
    * a `fan_id` on a device type with an optional Fan Control cluster adds the
      cluster to the endpoint (as if `clusters: fan_control: {}` was configured).

    Returns the (possibly copied) config.
    """
    bindings: list[tuple[str, str, object, dict]] = []  # where, key, id, holder
    features: set[str] = set()
    holders: list[dict] = []

    cluster_conf = (config.get(CONF_CLUSTERS) or {}).get(FAN_CONTROL_KEY)
    if cluster_conf is not None:
        holders.append(cluster_conf)
        for key in (CONF_FAN_ID, CONF_CLIMATE_ID):
            if key in cluster_conf:
                bindings.append(
                    ("clusters: fan_control", key, cluster_conf[key], cluster_conf)
                )
    optional_cluster_needed = False
    for conf_key, device_config in config.items():
        device_type = device_types_by_key.get(conf_key)
        if device_type is None or not isinstance(device_config, dict):
            continue
        if not device_type.has_fan_control_server():
            continue
        holders.append(device_config)
        if CONF_FAN_ID in device_config:
            bindings.append(
                (conf_key, CONF_FAN_ID, device_config[CONF_FAN_ID], device_config)
            )
            if not any(
                c.name == FAN_CONTROL and c.required
                for c in device_type.server_clusters
            ):
                optional_cluster_needed = True
    for holder in holders:
        features |= _normalized(holder.get(CONF_WITH_FEATURES, ()))
        if CONF_WIND_PRESETS in holder and CONF_FAN_ID not in holder:
            raise cv.Invalid(f"{CONF_WIND_PRESETS} needs {CONF_FAN_ID}")

    if len(bindings) > 1:
        places = ", ".join(f"{where}.{key}" for where, key, *_ in bindings)
        raise cv.Invalid(
            f"Only one Fan Control binding is possible per endpoint, got {places}"
        )

    if not bindings:
        unbound = sorted(features & set(BOUND_ONLY_FEATURES))
        if unbound:
            raise cv.Invalid(
                f"The Fan Control feature(s) {', '.join(unbound)} need {CONF_FAN_ID} "
                f"(or {CONF_CLIMATE_ID} for Step): without a bound entity nothing "
                "would handle them"
            )
        return config

    _, key, _, holder = bindings[0]
    if key == CONF_FAN_ID and "Auto" in features:
        raise cv.Invalid(
            "The Auto fan feature can't be used with fan_id: an ESPHome fan has no "
            "automatic mode to map it to. Remove it, or drop fan_id and handle "
            "FanMode with on_attribute"
        )
    if key == CONF_CLIMATE_ID and "Wind" in features:
        raise cv.Invalid(
            "The Wind feature needs a fan with preset modes (fan_id); a climate "
            "has no equivalent"
        )
    if holder.get(CONF_WIND_PRESETS) and "Wind" not in _normalized(
        holder.get(CONF_WITH_FEATURES, ())
    ):
        # wind_presets implies the feature
        holder[CONF_WITH_FEATURES] = [*holder.get(CONF_WITH_FEATURES, ()), "Wind"]

    if optional_cluster_needed and cluster_conf is None:
        config = dict(config)
        clusters = dict(config.get(CONF_CLUSTERS) or {})
        clusters[FAN_CONTROL_KEY] = {CONF_WITH_FEATURES: []}
        config[CONF_CLUSTERS] = clusters
    return config


def find_binding(config: dict, device_types_by_key: dict) -> FanBinding | None:
    """The Fan Control binding of a (normalized) endpoint config."""
    holders = []
    cluster_conf = (config.get(CONF_CLUSTERS) or {}).get(FAN_CONTROL_KEY)
    if cluster_conf is not None:
        holders.append(cluster_conf)
    for conf_key, device_config in config.items():
        device_type = device_types_by_key.get(conf_key)
        if device_type is not None and isinstance(device_config, dict):
            holders.append(device_config)
    features: set[str] = set()
    for holder in holders:
        features |= _normalized(holder.get(CONF_WITH_FEATURES, ()))
    for holder in holders:
        # climate_id of a device type config binds its Thermostat, not the fan.
        if CONF_FAN_ID in holder:
            presets = holder.get(CONF_WIND_PRESETS) or {}
            wind = "Wind" in features
            return FanBinding(
                "fan",
                holder[CONF_FAN_ID],
                presets.get(CONF_NATURAL, DEFAULT_NATURAL_PRESET) if wind else None,
                presets.get(CONF_SLEEP, DEFAULT_SLEEP_PRESET) if wind else None,
                "Step" in features,
            )
    if cluster_conf is not None and CONF_CLIMATE_ID in cluster_conf:
        return FanBinding(
            "climate", cluster_conf[CONF_CLIMATE_ID], step="Step" in features
        )
    return None


def config_lines(
    config_var: str, binding: FanBinding, var, enabled_features: dict[str, bool]
):
    """Lines for the Fan Control member of a device type config in the build callback."""
    if binding.kind == "climate":
        return [
            (
                f"{config_var}.fan_control.fan_mode_sequence = "
                f"esphome::matter::climate_fan_mode_sequence({var});"
            )
        ]
    with_auto = "true" if enabled_features.get("Auto") else "false"
    return [
        (
            f"{config_var}.fan_control.fan_mode_sequence = "
            f"esphome::matter::fan_mode_sequence({var}->get_traits(), {with_auto});"
        )
    ]


def cluster_config_lines(binding: FanBinding, var, enabled_features: dict[str, bool]):
    """Lines that set the mode sequence of an explicitly created Fan Control cluster."""
    if binding.kind == "climate":
        sequence = f"esphome::matter::climate_fan_mode_sequence({var})"
    else:
        with_auto = "true" if enabled_features.get("Auto") else "false"
        sequence = (
            f"esphome::matter::fan_mode_sequence({var}->get_traits(), {with_auto})"
        )
    return [f"fan_control_config.fan_mode_sequence = {sequence};"]


def _cpp_string(value: str | None) -> str:
    if value is None:
        return "nullptr"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def feature_lines(cluster_var: str, binding: FanBinding, var):
    """Lines that add the bound features to the created Fan Control cluster."""
    if binding.kind == "climate":
        return [
            (
                f"if (esphome::matter::add_climate_fan_features({cluster_var}, {var}, "
                f"{str(binding.step).lower()}) != ESP_OK)"
            ),
            "  return false;",
        ]
    options = ""
    if binding.step or binding.wind:
        options = (
            f", esphome::matter::FanFeatureOptions{{{str(binding.step).lower()}, "
            f"{_cpp_string(binding.wind_natural)}, {_cpp_string(binding.wind_sleep)}}}"
        )
    return [
        f"if (esphome::matter::add_fan_features({cluster_var}, {var}{options}) != ESP_OK)",
        "  return false;",
    ]


def _entity_config(full_config: Config, entity_id):
    try:
        path = full_config.get_path_for_id(entity_id)[:-1]
        return full_config.get_config_for_path(path)
    except KeyError:
        return None


def _holders(endpoint_config: dict):
    cluster_conf = (endpoint_config.get(CONF_CLUSTERS) or {}).get(FAN_CONTROL_KEY)
    if isinstance(cluster_conf, dict):
        yield FAN_CONTROL_KEY, cluster_conf
    for key, value in endpoint_config.items():
        if isinstance(value, dict) and CONF_FAN_ID in value:
            yield key, value


def _fan_has_speed(fan_config: dict):
    """True / False when the fan config says so, None when it can't be told."""
    platform = fan_config.get("platform")
    if platform == "binary":
        return False
    if platform == "template":
        return "speed_count" in fan_config
    if platform in ("speed", "hbridge"):
        return True
    return None


def final_validate(matter_config: dict, full_config: Config, device_types_by_key: dict):
    """Checks that need the configuration of the bound fan.

    Features are the union over all configs of the endpoint (device type configs and
    `clusters: fan_control:`), like find_binding() sees them.
    """
    for endpoint_id, endpoint_config in matter_config.get(CONF_ENDPOINTS, {}).items():
        binding = find_binding(endpoint_config, device_types_by_key)
        if binding is None or binding.kind != "fan":
            continue
        fan_id = binding.entity_id
        fan_config = _entity_config(full_config, fan_id)
        if fan_config is None:
            continue
        prefix = f"Matter endpoint {endpoint_id}"
        if binding.step and _fan_has_speed(fan_config) is False:
            raise cv.Invalid(
                f"{prefix}: the Step feature needs a fan with speed control, fan "
                f"'{fan_id}' ({fan_config.get('platform')}) has none"
                + (
                    " (set speed_count)"
                    if fan_config.get("platform") == "template"
                    else ""
                )
            )
        if binding.wind:
            presets = {}
            for _, holder in _holders(endpoint_config):
                if holder.get(CONF_FAN_ID) is not None:
                    presets = holder.get(CONF_WIND_PRESETS) or {}
            _check_wind_presets(prefix, fan_id, fan_config, presets)


def _check_wind_presets(prefix: str, fan_id, fan_config: dict, presets: dict):
    configured = fan_config.get("preset_modes")
    if configured is None:
        raise cv.Invalid(
            f"{prefix}: the Wind feature maps to the preset modes of fan '{fan_id}' "
            f"but it has no `preset_modes`. Add presets named "
            f'"{DEFAULT_NATURAL_PRESET}" and/or "{DEFAULT_SLEEP_PRESET}" '
            f"(or name them with {CONF_WIND_PRESETS})"
        )
    names = {str(name).lower() for name in configured}
    found = False
    for key, default in (
        (CONF_NATURAL, DEFAULT_NATURAL_PRESET),
        (CONF_SLEEP, DEFAULT_SLEEP_PRESET),
    ):
        name = presets.get(key, default)
        if name.lower() in names:
            found = True
        elif key in presets:
            raise cv.Invalid(
                f"{prefix}: {CONF_WIND_PRESETS}.{key} is '{name}', which is not one of "
                f"the preset_modes of fan '{fan_id}' ({', '.join(map(str, configured))})"
            )
    if not found:
        raise cv.Invalid(
            f"{prefix}: none of the preset_modes of fan '{fan_id}' "
            f"({', '.join(map(str, configured))}) is named "
            f'"{DEFAULT_NATURAL_PRESET}" or "{DEFAULT_SLEEP_PRESET}"; name them with '
            f"{CONF_WIND_PRESETS}"
        )


def fan_restore_warning(matter_config: dict, full_config: Config):
    for endpoint_config in matter_config.get(CONF_ENDPOINTS, {}).values():
        for _, holder in _holders(endpoint_config):
            fan_id = holder.get(CONF_FAN_ID)
            if fan_id is None:
                continue
            fan_config = _entity_config(full_config, fan_id)
            if fan_config is None:
                continue
            restore_mode = fan_config.get(CONF_RESTORE_MODE, "ALWAYS_OFF")
            if restore_mode != "ALWAYS_OFF":
                _LOGGER.warning(
                    "Fan '%s' is exposed through Matter with restore mode %s and should use "
                    "restore_mode: ALWAYS_OFF so Matter can restore its power-on state",
                    fan_id,
                    restore_mode,
                )
