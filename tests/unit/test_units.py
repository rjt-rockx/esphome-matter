"""Matter value encodings in components/matter/data_model/units.py."""

import esphome.config_validation as cv
import pytest

from components.matter.data_model import units


@pytest.mark.parametrize(
    ("value", "multiplier", "expected"),
    [
        ("5s", 1, 5),
        ("2min", 1, 120),
        (7, 1, 7),  # raw ints pass through
        ("1s", 10, 10),  # deciseconds
        ("500ms", 10, 5),
    ],
)
def test_seconds(value, multiplier, expected):
    assert units.seconds(multiplier)(value) == expected


def test_seconds_rejects_floats_and_fractions():
    with pytest.raises(cv.Invalid, match="ambiguous"):
        units.seconds()(1.5)
    with pytest.raises(cv.Invalid, match="multiple"):
        units.seconds()("1500ms")


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0%", 0), ("100%", 254), ("50%", 127), (10, 10)],
)
def test_percentage(value, expected):
    assert units.percentage()(value) == expected


def test_percentage_custom_multiplier_and_float_error():
    assert units.percentage(100)("25%") == 25
    with pytest.raises(cv.Invalid, match="ambiguous"):
        units.percentage()(0.5)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0°", 0), ("180°", 127), ("360°", 254), ("90°", 64)],
)
def test_hue_degrees(value, expected):
    assert units.hue(254)(value) == expected


def test_hue_raw_int_passthrough():
    assert units.hue(254)(42) == 42


def test_saturation():
    assert units.saturation()(0.0) == 0
    assert units.saturation()(1.0) == 254
    assert units.saturation()(0.5) == 127
    assert units.saturation()(10) == 10
    with pytest.raises(cv.Invalid):
        units.saturation()(1.5)
    with pytest.raises(cv.Invalid):
        units.saturation()("half")


def test_xy_colour():
    assert units.xy_colour()(0.0) == 0
    assert units.xy_colour()(0.5) == 32768
    assert units.xy_colour()(1.0) == 65536
    with pytest.raises(cv.Invalid):
        units.xy_colour()(1.1)
    with pytest.raises(cv.Invalid):
        units.xy_colour()("x")


def test_xy_colour_rate_range():
    assert units.xy_colour_rate()(-0.5) == -32768
    assert units.xy_colour_rate()(0.25) == 16384
    with pytest.raises(cv.Invalid):
        units.xy_colour_rate()(0.5)


def test_rate_strips_per_second_suffix():
    assert units.UNIT_VALIDATORS["percentage_rate"](254)("50%/s") == units.percentage(254)("50%")
    assert units.UNIT_VALIDATORS["hue_rate"](254)("180°/s") == units.hue(254)("180°")


def test_unit_validator_table_complete():
    assert set(units.UNIT_VALIDATORS) >= {
        "seconds",
        "percentage",
        "percentage_rate",
        "hue",
        "hue_rate",
        "saturation",
        "xy_colour",
        "xy_colour_rate",
    }
