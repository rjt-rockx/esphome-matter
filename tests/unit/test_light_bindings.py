"""light_id on the plug-in unit and mounted load device types, LevelControl
transition attributes, color loop and trait based color features."""

from __future__ import annotations

import textwrap

import pytest
from conftest import CONFIGS, prepare_config, run_esphome

from components.matter import light_binding
from components.matter.data_model.clusters import CLUSTERS_BY_NAME
from components.matter.data_model.device_types import DEVICE_TYPES_BY_CONF_KEY

LIGHTS = """
output:
  - platform: template
    id: out
    type: float
    write_action: []
  - platform: template
    id: out_red
    type: float
    write_action: []
  - platform: template
    id: out_green
    type: float
    write_action: []
  - platform: template
    id: out_blue
    type: float
    write_action: []
light:
  - platform: monochromatic
    id: lamp
    output: out
    restore_mode: ALWAYS_OFF
  - platform: rgb
    id: strip
    red: out_red
    green: out_green
    blue: out_blue
    restore_mode: ALWAYS_OFF
    effects:
      - lambda:
          name: Rainbow
          lambda: ""
"""
LIGHTS = textwrap.indent(LIGHTS, " " * 8)


def test_light_device_types_have_light_id():
    for name in light_binding.LIGHT_DEVICE_TYPES:
        assert name in DEVICE_TYPES_BY_CONF_KEY, name


@pytest.mark.parametrize(
    "device_type",
    [
        "dimmable_plug_in_unit",
        "mounted_dimmable_load_control",
        "on_off_plug_in_unit",
        "mounted_on_off_control",
    ],
)
def test_light_id_on_plug_and_mounted_types(run_config, device_type):
    result = run_config(
        f"""
        matter:
          endpoints:
            1:
              {device_type}:
                light_id: lamp
        """
        + LIGHTS
    )
    assert result.ok, result.output


def test_min_max_level_on_dimmable_plug(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              dimmable_plug_in_unit:
                light_id: lamp
                min_level: 10%
                max_level: 200
        """
        + LIGHTS
    )
    assert result.ok, result.output


def test_light_id_not_supported_message(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              fan:
                light_id: lamp
        """
        + LIGHTS
    )
    assert not result.ok
    assert "light_id is not supported by fan" in result.output
    assert "dimmable_plug_in_unit" in result.output


def test_on_off_transition_time_needs_light_id(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              dimmable_light:
                on_off_transition_time: 1s
        """
    )
    assert not result.ok
    assert "requires light_id" in result.output


def test_on_off_transition_time_needs_level_control(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              on_off_plug_in_unit:
                light_id: lamp
                on_off_transition_time: 1s
        """
        + LIGHTS
    )
    assert not result.ok
    assert "needs the LevelControl cluster" in result.output


def test_on_off_transition_time_with_level_control_cluster(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              on_off_plug_in_unit:
                light_id: lamp
                on_off_transition_time: 1s
              clusters:
                level_control:
                  with_features: [on_off, lighting]
        """
        + LIGHTS
    )
    assert result.ok, result.output


def test_color_loop_needs_effect(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              extended_color_light:
                light_id: strip
                with_features: [Hue And Saturation, EnhancedHue, ColorLoop]
        """
        + LIGHTS
    )
    assert not result.ok
    assert "needs an ESPHome effect" in result.output
    assert "color_loop_effect" in result.output


def test_color_loop_effect_must_exist_on_light(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              extended_color_light:
                light_id: strip
                with_features: [Hue And Saturation, EnhancedHue, ColorLoop]
                color_loop_effect: Missing
        """
        + LIGHTS
    )
    assert not result.ok
    assert "'Missing' is not an effect of light 'strip'" in result.output
    assert "Rainbow" in result.output


def test_color_loop_effect_requires_feature(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              extended_color_light:
                light_id: strip
                color_loop_effect: Rainbow
        """
        + LIGHTS
    )
    assert not result.ok
    assert "requires the ColorLoop feature" in result.output


def test_color_loop_with_effect_and_spec_spellings(run_config):
    # The spec names ("Enhanced Hue", "Color Loop") work like the key names.
    result = run_config(
        """
        matter:
          endpoints:
            1:
              extended_color_light:
                light_id: strip
                with_features: [Hue And Saturation, Enhanced Hue, Color Loop]
                color_loop_effect: rainbow
        """
        + LIGHTS
    )
    assert result.ok, result.output


def test_color_loop_without_light_id_needs_no_effect(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              extended_color_light:
                with_features: [Hue And Saturation, EnhancedHue, ColorLoop]
        """
    )
    assert result.ok, result.output


@pytest.mark.parametrize(
    "spelling", ["enhanced_hue", "EnhancedHue", "Enhanced Hue", "enhanced hue"]
)
def test_feature_spellings(spelling):
    device_type = DEVICE_TYPES_BY_CONF_KEY["extended_color_light"]
    assert device_type._validate_feature_name(spelling) == "EnhancedHue"


def test_cluster_feature_spellings():
    cluster = CLUSTERS_BY_NAME["LevelControl"]
    assert cluster._validate_feature_name("OnOff") == "on_off"
    assert cluster._validate_feature_name("Lighting") == "lighting"


def test_light_effect_names():
    config = {
        "effects": [
            {"lambda": {"name": "Rainbow"}},
            {"random": {"name": "Random"}},
        ]
    }
    assert light_binding.light_effect_names(config) == ["Rainbow", "Random"]
    assert light_binding.light_effect_names({}) == []


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"restore_mode": "ALWAYS_OFF"}, None),
        ({"restore_mode": "RESTORE_DEFAULT_OFF"}, "restore_mode: RESTORE_DEFAULT_OFF"),
        ({"restore_state": "none"}, None),
        ({}, None),
    ],
)
def test_restore_description(config, expected):
    assert light_binding._restore_description(config) == expected


def test_level_attribute_names():
    with_oo = light_binding.level_attribute_names(True)
    assert "OnOffTransitionTime" in with_oo and "DefaultMoveRate" in with_oo
    assert light_binding.level_attribute_names(False) == ("DefaultMoveRate",)


def test_light_binding_config_generates_expected_code(tmp_path):
    config = prepare_config(CONFIGS / "esp32-light-bindings.yaml", tmp_path)
    result = run_esphome("compile", "--only-generate", str(config), cwd=tmp_path)
    assert result.ok, result.output[-3000:]
    code = next(tmp_path.glob(".esphome/build/*/src/main.cpp")).read_text()
    # light_id on the plug-in unit and the mounted load
    assert "esp_matter::endpoint::dimmable_plug_in_unit::add" in code
    assert "esp_matter::endpoint::mounted_dimmable_load_control::add" in code
    assert "register_light(plug_dimmer, 1)" in code
    assert "register_light(mounted_dimmer, 2)" in code
    # 500 ms is 5 deciseconds
    assert "esp_matter_uint16(5)" in code
    assert "attribute::create_on_transition_time" in code
    assert "attribute::create_off_transition_time" in code
    assert "attribute::create_default_move_rate" in code
    # the RGB only light adds the ColorTemperature feature by its traits
    assert "add_extended_color_light(endpoint, &device_config_0, rgb_strip)" in code
    assert 'register_light(rgb_strip, 6, "Color loop")' in code
    # LevelControl added with `clusters:` has no feature_flags in esp-matter's
    # config; its features are added after the cluster is created.
    assert "level_control_config.feature_flags" not in code
    assert "level_control::feature::on_off::add" in code
    assert "level_control::feature::lighting::add" in code
    # features of an endpoint are added in bit order whatever the YAML order
    hue = code.index("feature::hue_saturation::add")
    enhanced = code.index("feature::enhanced_hue::add")
    loop = code.index("feature::color_loop::add")
    assert hue < enhanced < loop


def test_color_loop_feature_dependencies_are_added(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              extended_color_light:
                light_id: strip
                with_features: [Color Loop]
                color_loop_effect: Rainbow
        """
        + LIGHTS
    )
    assert result.ok, result.output
    assert "HueAndSaturation" in result.output
    assert "EnhancedHue" in result.output


def test_dependency_table_has_color_chain():
    from components.matter.data_model.device_types import FEATURE_DEPENDENCIES

    assert FEATURE_DEPENDENCIES["ColorLoop"] == (("EnhancedHue",),)
    assert FEATURE_DEPENDENCIES["EnhancedHue"] == (("HueAndSaturation",),)


def test_standalone_color_control_config_and_commands():
    from components.matter.endpoints import Endpoint

    cluster = CLUSTERS_BY_NAME["ColorControl"]

    def features(*codes):
        return [f for f in cluster.features if f.code in codes]

    lines = Endpoint._standalone_cluster_config_lines(
        cluster, "cfg", features("HS", "EHUE")
    )
    assert "cfg.color_mode = 0;" in lines
    assert "cfg.enhanced_color_mode = 3;" in lines
    lines = Endpoint._standalone_cluster_config_lines(
        cluster, "cfg", features("XY", "CT")
    )
    assert "cfg.color_mode = 1;" in lines and "cfg.enhanced_color_mode = 1;" in lines
    lines = Endpoint._standalone_cluster_config_lines(cluster, "cfg", features("CT"))
    assert "cfg.color_mode = 2;" in lines
    extra = "\n".join(Endpoint._standalone_cluster_extra_lines(cluster, "cc"))
    assert "create_stop_move_step(cc)" in extra
    assert "create_remaining_time(cc, 0)" in extra
    on_off = "\n".join(
        Endpoint._standalone_cluster_extra_lines(CLUSTERS_BY_NAME["OnOff"], "oo")
    )
    assert "create_on(oo)" in on_off and "create_toggle(oo)" in on_off


@pytest.mark.parametrize(
    "name",
    [
        "Groups",
        "FanControl",
        "ScenesManagement",
        "ModeSelect",
        "DoorLock",
        "BooleanStateConfiguration",
        "ValveConfigurationandControl",
        "OnOff",
        "LevelControl",
        "ColorControl",
    ],
)
def test_clusters_without_feature_flags(name):
    from components.matter.data_model import espmatter_names

    assert not espmatter_names.cluster_has_feature_flags(name)


def test_feature_flags_table_matches_sdk_headers():
    """Whether config_t has feature_flags is read from the esp-matter headers."""
    import importlib.util

    from conftest import REPO_ROOT

    from components.matter.data_model import espmatter_names

    spec = importlib.util.spec_from_file_location(
        "gen_espmatter_names", REPO_ROOT / "tools" / "gen_espmatter_names.py"
    )
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    try:
        headers = gen.Headers(gen._find_esp_matter())
    except (SystemExit, FileNotFoundError, OSError):
        pytest.skip("esp-matter headers not available")
    for name, entry in espmatter_names.TABLE["clusters"].items():
        namespace = entry["namespace"]
        if namespace is None:
            continue
        expected = headers.clusters.get(namespace, {}).get("feature_flags", False)
        assert entry["feature_flags"] == expected, name
        assert espmatter_names.cluster_has_feature_flags(name) == expected


def test_transition_time_rounding_warns(caplog):
    from esphome.core import TimePeriod

    with caplog.at_level("WARNING"):
        light_binding._warn_transition_rounding(TimePeriod(milliseconds=50))
        light_binding._warn_transition_rounding(TimePeriod(milliseconds=250))
        light_binding._warn_transition_rounding(TimePeriod(milliseconds=500))
    assert "becomes 0" in caplog.text
    assert "rounded down" in caplog.text
    assert caplog.text.count("on_off_transition_time") == 2


def test_light_has_color_temperature():
    assert light_binding.light_has_color_temperature({"platform": "rgbww"})
    assert light_binding.light_has_color_temperature(
        {"platform": "x", "cold_white_color_temperature": 1}
    )
    assert not light_binding.light_has_color_temperature({"platform": "rgb"})


def test_rgb_only_light_warns_about_non_conformance(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              extended_color_light:
                light_id: strip
        """
        + LIGHTS
    )
    assert result.ok, result.output
    assert "doesn't advertise the ColorTemperature feature" in result.output


def _created_elements(body: str) -> list[str]:
    """Ordered cluster/feature/attribute/command creation calls of a function body."""
    import re

    calls = re.findall(
        r"((?:\w+::)*(?:create\w*|add|add_device_type))\(\s*(?:endpoint|\w+_cluster)",
        body,
    )
    return [
        c.replace("esp_matter::", "").replace("cluster::", "").replace("endpoint::", "")
        for c in calls
    ]


def test_add_extended_color_light_matches_sdk():
    """Our copy of extended_color_light::add() creates what the SDK's does."""
    import importlib.util

    from conftest import COMPONENTS, REPO_ROOT

    spec = importlib.util.spec_from_file_location(
        "gen_espmatter_names", REPO_ROOT / "tools" / "gen_espmatter_names.py"
    )
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    try:
        sdk = (
            gen._find_esp_matter() / "data_model" / "legacy" / "esp_matter_endpoint.cpp"
        ).read_text()
    except (SystemExit, FileNotFoundError, OSError):
        pytest.skip("esp-matter sources not available")
    namespace = sdk.index("namespace extended_color_light {")
    start = sdk.index("esp_err_t add(endpoint_t *endpoint", namespace)
    start = sdk.index("{", start)
    sdk_body = sdk[start : sdk.index("return ESP_OK;", start)]
    ours = (COMPONENTS / "matter" / "matter_lights.cpp").read_text()
    start = ours.index("esp_err_t add_extended_color_light(")
    start = ours.index("{", start)
    our_body = ours[start : ours.index("return ESP_OK;", start)]
    assert _created_elements(our_body) == _created_elements(sdk_body)
