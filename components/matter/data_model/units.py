import logging
from dataclasses import dataclass

import esphome.config_validation as cv

_LOGGER = logging.getLogger(__name__)


def seconds(multiplier=1):
    def _validate(value):
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            raise cv.Invalid(f"Floats are ambiguous. Use '{value}s' instead.")
        period_ms = cv.positive_time_period_milliseconds(value).total_milliseconds
        scaled = period_ms * multiplier
        if scaled % 1000 != 0:
            raise cv.Invalid(f"Duration must be a multiple of {1000 / multiplier:g}ms")
        return scaled // 1000

    return _validate


def percentage(multiplier=254):
    def _validate(value):
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            raise cv.Invalid(f"Floats are ambiguous. Use '{value}%' instead.")
        return round(cv.percentage(value) * multiplier)

    return _validate


def hue(multiplier):
    def _validate(value):
        if isinstance(value, int):
            _LOGGER.warning(
                "Integer hue value %s is interpreted as a raw Matter value. Degrees values such as '180°' are recommended.",
                value,
            )
            return value
        return round(cv.angle(value) / 360 * multiplier)

    return _validate


def saturation(multiplier=254):
    def _validate(value):
        if isinstance(value, int):
            _LOGGER.warning(
                "Integer saturation value %s is interpreted as a raw Matter value. A value between 0.0 and 1.0 is recommended",
                value,
            )
            return value
        if isinstance(value, float):
            if not 0.0 <= value <= 1.0:
                raise cv.Invalid("Saturation must be between 0.0 and 1.0")
            return round(value * multiplier)
        raise cv.Invalid("Saturation should be a float value between 0.0 and 1.0")

    return _validate


def xy_colour(multiplier=65536):
    def _validate(value):
        if isinstance(value, int):
            _LOGGER.warning(
                "Integer XY colour value %s is interpreted as a raw Matter value. A float is recommended",
                value,
            )
            return value
        if isinstance(value, float):
            if not 0.0 <= value <= 1.0:
                raise cv.Invalid("XY colour value must be in the range [0.0, 1.0]")
            return round(value * multiplier)
        raise cv.Invalid("XY colour value should be a float")

    return _validate


def xy_colour_rate(multiplier=65536):
    def _validate(value):
        if isinstance(value, int):
            _LOGGER.warning(
                "Integer XY colour value %s is interpreted as a raw Matter value. A float is recommended",
                value,
            )
            return value
        if isinstance(value, float):
            if not -0.5 <= value < 0.5:
                raise cv.Invalid("XY colour rate must be in the range [-0.5, 0.5)")
            return round(value * multiplier)
        raise cv.Invalid("XY colour rate should be a float")

    return _validate


def rate(validator_factory):
    def _factory(multiplier=None):
        def _validate(value):
            if isinstance(value, str):
                value = value.removesuffix("/s")
            if multiplier is not None:
                return validator_factory(multiplier)(value)
            return validator_factory()(value)

        return _validate

    return _factory


UNIT_VALIDATORS = {
    "seconds": seconds,
    "percentage": percentage,
    "percentage_rate": rate(percentage),
    "hue": hue,
    "hue_rate": rate(hue),
    "saturation": saturation,
    "saturation_rate": saturation,  # Unitless so no rate.
    "xy_colour": xy_colour,
    "xy_colour_rate": xy_colour_rate,
}


# ---------------------------------------------------------------------------
# ESPHome sensor unit handling
#
# The C++ converters in matter_conversions.cpp expect a fixed *canonical* unit
# per measurement family (e.g. hPa for pressure). The tables below describe how
# to turn the unit_of_measurement of a bound ESPHome sensor into that canonical
# unit: ``canonical = value * scale + offset``.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnitFamily:
    # Unit the C++ converter expects.
    canonical: str
    # ESPHome unit string -> (scale, offset) to canonical.
    units: dict[str, tuple[float, float]]
    # ESPHome device classes that fit this family.
    device_classes: tuple[str, ...]


def _linear(units: dict[str, float]) -> dict[str, tuple[float, float]]:
    return {unit: (scale, 0.0) for unit, scale in units.items()}


SENSOR_FAMILIES: dict[str, UnitFamily] = {
    "temperature": UnitFamily(
        "°C",
        {"°C": (1.0, 0.0), "°F": (5.0 / 9.0, -160.0 / 9.0), "K": (1.0, -273.15)},
        ("temperature",),
    ),
    # Matter PressureMeasurement::MeasuredValue is in 0.1 kPa, which is numerically hPa.
    "pressure": UnitFamily(
        "hPa",
        _linear(
            {
                "hPa": 1.0,
                "mbar": 1.0,
                "mb": 1.0,
                "Pa": 0.01,
                "kPa": 10.0,
                "bar": 1000.0,
                "mmHg": 1.333224,
                "inHg": 33.863886,
                "psi": 68.947573,
            }
        ),
        ("pressure", "atmospheric_pressure"),
    ),
    "humidity": UnitFamily("%", {"%": (1.0, 0.0)}, ("humidity", "moisture")),
    "illuminance": UnitFamily("lx", {"lx": (1.0, 0.0)}, ("illuminance",)),
    # Matter FlowMeasurement::MeasuredValue is 10 x m³/h.
    "flow": UnitFamily(
        "m³/h",
        _linear(
            {
                "m³/h": 1.0,
                "m3/h": 1.0,
                "L/h": 0.001,
                "L/min": 0.06,
                "L/s": 3.6,
                "m³/min": 60.0,
                "m³/s": 3600.0,
            }
        ),
        ("volume_flow_rate",),
    ),
    "voltage": UnitFamily(
        "V", _linear({"V": 1.0, "mV": 0.001, "kV": 1000.0}), ("voltage",)
    ),
    "current": UnitFamily(
        "A", _linear({"A": 1.0, "mA": 0.001, "kA": 1000.0}), ("current",)
    ),
    "power": UnitFamily(
        "W", _linear({"W": 1.0, "mW": 0.001, "kW": 1000.0}), ("power",)
    ),
    "apparent_power": UnitFamily(
        "VA", _linear({"VA": 1.0, "kVA": 1000.0}), ("apparent_power",)
    ),
    "reactive_power": UnitFamily(
        "var", _linear({"var": 1.0, "kvar": 1000.0}), ("reactive_power",)
    ),
    "frequency": UnitFamily(
        "Hz", _linear({"Hz": 1.0, "mHz": 0.001, "kHz": 1000.0}), ("frequency",)
    ),
    # Canonical power factor is the plain ratio (-1.0 .. 1.0).
    "power_factor": UnitFamily(
        "", {"": (1.0, 0.0), "%": (0.01, 0.0)}, ("power_factor",)
    ),
    "energy": UnitFamily(
        "kWh", _linear({"kWh": 1.0, "Wh": 0.001, "MWh": 1000.0}), ("energy",)
    ),
    "battery": UnitFamily("%", {"%": (1.0, 0.0)}, ("battery",)),
}

# Matter MeasurementUnitEnum (shared by all concentration measurement clusters).
MEASUREMENT_UNITS = {
    "ppm": 0,
    "ppb": 1,
    "ppt": 2,
    "mgm3": 3,
    "ugm3": 4,
    "ngm3": 5,
    "pm3": 6,
    "bqm3": 7,
}

# ESPHome unit string -> (MeasurementUnit name, scale to that unit).
# Units that are not listed here have no Matter MeasurementUnit equivalent.
CONCENTRATION_UNITS: dict[str, tuple[str, float]] = {
    "ppm": ("ppm", 1.0),
    "ppb": ("ppb", 1.0),
    "ppt": ("ppt", 1.0),
    "mg/m³": ("mgm3", 1.0),
    "mg/m3": ("mgm3", 1.0),
    "µg/m³": ("ugm3", 1.0),
    "μg/m³": ("ugm3", 1.0),  # Greek mu
    "ug/m3": ("ugm3", 1.0),
    "ng/m³": ("ngm3", 1.0),
    "Bq/m³": ("bqm3", 1.0),
    "Bq/m3": ("bqm3", 1.0),
    # Converted so the value is correct for the advertised unit.
    "pCi/L": ("bqm3", 37.0),
    "#/m³": ("pm3", 1.0),
    "#/cm³": ("pm3", 1_000_000.0),
}

# Units of the same kind can be converted into each other.
CONCENTRATION_KINDS: dict[str, str] = {
    "ppm": "ratio",
    "ppb": "ratio",
    "ppt": "ratio",
    "mgm3": "mass",
    "ugm3": "mass",
    "ngm3": "mass",
    "pm3": "count",
    "bqm3": "activity",
}

# Power of ten of a unit relative to the first unit of its kind.
_CONCENTRATION_UNIT_EXPONENT = {
    "ppm": 0,
    "ppb": -3,
    "ppt": -6,
    "mgm3": 0,
    "ugm3": -3,
    "ngm3": -6,
    "pm3": 0,
    "bqm3": 0,
}

# Default MeasurementUnit when the bound sensor does not report a usable unit.
# Keyed by SensorAttribute.conf_key; everything else defaults to ppm.
CONCENTRATION_DEFAULT_UNITS = {
    "pm_1": "ugm3",
    "pm_2_5": "ugm3",
    "pm_10": "ugm3",
    "radon": "bqm3",
    "smoke_concentration": "ugm3",
}


def concentration_default_unit(conf_key: str) -> str:
    return CONCENTRATION_DEFAULT_UNITS.get(conf_key, "ppm")


def concentration_scale(from_unit: str, to_unit: str) -> float | None:
    """Scale to convert a value from one MeasurementUnit name to another.

    Returns None when the units are of a different kind and cannot be converted.
    """
    if CONCENTRATION_KINDS[from_unit] != CONCENTRATION_KINDS[to_unit]:
        return None
    return 10.0 ** (
        _CONCENTRATION_UNIT_EXPONENT[from_unit] - _CONCENTRATION_UNIT_EXPONENT[to_unit]
    )
