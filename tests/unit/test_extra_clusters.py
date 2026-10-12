"""Extra/optional clusters configured with `clusters:` on an endpoint."""

from __future__ import annotations

import pytest
from conftest import CONFIGS, prepare_config, run_esphome

from components.matter.data_model.clusters import (
    CLUSTERS,
    CLUSTERS_BY_CONF_KEY,
    CLUSTERS_BY_NAME,
)


def test_conf_keys_map_to_clusters():
    # `clusters:` keys are snake_case conf keys, not the CamelCase cluster names.
    assert CLUSTERS_BY_CONF_KEY["fan_control"] is CLUSTERS_BY_NAME["FanControl"]
    assert len(CLUSTERS_BY_CONF_KEY) == len(CLUSTERS)


LIGHT = """
output:
  - platform: template
    id: out
    type: binary
    write_action: []
light:
  - platform: binary
    id: lamp
    output: out
"""


@pytest.mark.parametrize(
    ("device", "cluster"),
    [
        ("room_air_conditioner", "fan_control"),
        ("air_purifier", "hepa_filter_monitoring"),
        ("thermostat", "thermostat_user_interface_configuration"),
    ],
)
def test_extra_cluster_validates(run_config, device, cluster):
    features = "with_features: [Heating]" if "air_purifier" != device else ""
    result = run_config(
        f"""
        matter:
          endpoints:
            1:
              {device}:
                {features}
              clusters:
                {cluster}:
        """
    )
    assert result.ok, result.output


def test_extra_cluster_with_features(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              room_air_conditioner:
                with_features: [Heating]
              clusters:
                fan_control:
                  with_features: [multi_speed, auto]
        """
    )
    assert result.ok, result.output


def test_extra_binding_cluster_on_light(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              on_off_light:
                light_id: lamp
              clusters:
                binding:
        """
        + LIGHT.replace("\n", "\n        ")
    )
    assert result.ok, result.output


def test_unknown_extra_cluster_rejected(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              on_off_light:
              clusters:
                FanControl:
        """
    )
    assert not result.ok


@pytest.mark.slow
def test_extra_clusters_generate(tmp_path):
    prepared = prepare_config(CONFIGS / "esp32-extra-clusters.yaml", tmp_path)
    result = run_esphome("compile", "--only-generate", str(prepared), cwd=tmp_path)
    assert result.ok, result.output[-3000:]
    code = next(tmp_path.glob(".esphome/build/*/src/main.cpp")).read_text()
    for name in (
        "fan_control",
        "hepa_filter_monitoring",
        "thermostat_user_interface_configuration",
        "binding",
    ):
        assert f"esp_matter::cluster::{name}::create(endpoint" in code, name
    sdkconfig = next(tmp_path.glob(".esphome/build/*/sdkconfig.*[!l]")).read_text()
    assert "CONFIG_SUPPORT_HEPA_FILTER_MONITORING_CLUSTER=y" in sdkconfig
