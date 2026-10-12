"""The esp-matter identifier table covers every name the code generation can emit.

C++ identifiers (cluster/feature/endpoint namespaces, create_* functions,
config_t members) come from data_model/espmatter_names.json, which is generated
from the esp-matter headers by tools/gen_espmatter_names.py. These tests catch
names that would break compilation without running a compiler.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import COMPONENTS, DATA_MODEL_DIR, REPO_ROOT

from components.matter import binary_sensors, fan_binding
from components.matter.data_model import device_types, espmatter_names
from components.matter.data_model.attributes import SENSOR_ATTRIBUTES
from components.matter.data_model.clusters import CLUSTERS, CLUSTERS_BY_NAME
from components.matter.data_model.device_types import DEVICE_TYPES

TABLE = espmatter_names.TABLE

# Features esp-matter 1.6 doesn't have; config validation must reject them.
UNSUPPORTED_FEATURES = {
    ("DoorLock", "Logging"),
    ("DoorLock", "Notification"),
    ("WindowCovering", "AbsolutePosition"),
    ("Thermostat", "ThermostatSuggestions"),
    ("Thermostat", "ScheduleConfiguration"),
    ("Thermostat", "Events"),
    ("OccupancySensing", "Prediction"),
    ("OccupancySensing", "OccupancyEvent"),
}


def test_table_covers_data_model():
    clusters = json.loads((DATA_MODEL_DIR / "clusters.json").read_text())
    for cluster in CLUSTERS:
        assert cluster.name in TABLE["clusters"], cluster.name
        entry = TABLE["clusters"][cluster.name]
        for feature in cluster.features:
            assert feature.name in entry["features"], (cluster.name, feature.name)
        for attribute in cluster.server_attributes:
            if attribute.name:
                assert attribute.name in entry["attributes"], (
                    cluster.name,
                    attribute.name,
                )
    assert len(TABLE["clusters"]) == len(clusters)
    for device_type in DEVICE_TYPES:
        assert device_type.name in TABLE["device_types"], device_type.name


@pytest.mark.parametrize(
    "cluster, namespace",
    [
        ("Switch", "switch_cluster"),
        ("OnOff", "on_off"),
        ("PM25ConcentrationMeasurement", "pm25_concentration_measurement"),
        ("DishwasherAlarm", "dish_washer_alarm"),
        ("SmokeCOAlarm", "smoke_co_alarm"),
        ("ValveConfigurationandControl", "valve_configuration_and_control"),
    ],
)
def test_cluster_namespaces(cluster, namespace):
    assert CLUSTERS_BY_NAME[cluster].espm_namespace == namespace


@pytest.mark.parametrize(
    "cluster, feature, namespace",
    [
        ("FanControl", "Auto", "fan_auto"),
        ("DoorLock", "Unbolt", "unbolting"),
        ("DoorLock", "WeekDayAccessSchedules", "weekday_access_schedules"),
        ("DoorLock", "CredentialsOverTheAirAccess", "credential_over_the_air_access"),
        ("ColorControl", "Hue And Saturation", "hue_saturation"),
        ("Thermostat", "MatterScheduleConfiguration", "matter_schedule_configuration"),
        # Shared by all concentration measurement clusters.
        ("PM25ConcentrationMeasurement", "NumericMeasurement", "numeric_measurement"),
    ],
)
def test_feature_namespaces(cluster, feature, namespace):
    features = {f.name: f for f in CLUSTERS_BY_NAME[cluster].features}
    assert features[feature].namespace == namespace


def test_unsupported_features_are_unavailable():
    for cluster_name, feature_name in UNSUPPORTED_FEATURES:
        features = {f.name: f for f in CLUSTERS_BY_NAME[cluster_name].features}
        feature = features[feature_name]
        assert not feature.espm_available
        with pytest.raises(ValueError):
            _ = feature.namespace


def test_unsupported_feature_rejected_at_config_time(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              window_covering:
                features: [Lift, AbsolutePosition]
        """
    )
    assert not result.ok
    assert "AbsolutePosition" in result.output
    assert "not supported by esp-matter" in result.output


def test_unsupported_extra_cluster_rejected_at_config_time(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              on_off_light: {}
              extra_clusters: [Channel]
        """
    )
    assert not result.ok
    assert "not supported by esp-matter" in result.output


def test_every_device_type_has_endpoint_namespace():
    missing = [d.name for d in DEVICE_TYPES if not d.espm_available]
    assert not missing, f"Device types without esp-matter endpoint: {missing}"


def test_device_type_clusters_and_features_exist():
    """Everything a device type enables on its own must exist in esp-matter."""
    problems = []
    for device_type in DEVICE_TYPES:
        for cluster in device_type.server_clusters:
            if not cluster.required:
                continue
            if cluster.espm_cluster_namespace is None:
                # esp-matter creates its own required clusters; nothing is emitted.
                continue
            for feature in cluster.features:
                if feature.enabled and not feature.espm_available:
                    problems.append((device_type.name, cluster.name, feature.name))
    assert not problems


def test_feature_flag_config_members_exist():
    """`<config>.<member>.feature_flags` is emitted for choice-feature clusters."""
    for device_type in DEVICE_TYPES:
        for cluster in device_type.server_clusters:
            if not cluster.required or not cluster.espm_available:
                continue
            if not cluster.choice_features and cluster.name != "Switch":
                continue
            entry = TABLE["device_types"][device_type.name]
            if not entry["config_members"]:
                continue
            member = device_type.config_member(cluster)
            assert member in entry["config_members"], (device_type.name, cluster.name)


def _feature_names_emitted_by_code() -> set[tuple[str, str]]:
    names = {("OccupancySensing", n) for n in binary_sensors.OCCUPANCY_CHOICE_FEATURES}
    names |= {("FanControl", n) for n in fan_binding.TRAIT_FEATURES}
    names |= {("FanControl", "Auto")}
    names |= {
        ("Switch", n)
        for n in (
            "LatchingSwitch",
            "MomentarySwitch",
            "MomentarySwitchRelease",
            "MomentarySwitchLongPress",
            "MomentarySwitchMultiPress",
            "ActionSwitch",
        )
    }
    names |= {("Thermostat", n) for n in ("Heating", "Cooling", "AutoMode")}
    names |= {("ElectricalPowerMeasurement", "PolyphasePower")}
    names |= {
        ("ElectricalPowerMeasurement", "DirectCurrent"),
        ("ElectricalPowerMeasurement", "AlternatingCurrent"),
    }
    names |= {("PowerTopology", "NodeTopology")}
    used = {sa.cluster.name for dt in DEVICE_TYPES for sa in dt.sensor_attributes}
    for cluster_name, sensors in SENSOR_ATTRIBUTES.items():
        if cluster_name not in used:
            continue  # e.g. SmokeConcentrationMeasurement: no device type has it
        for sensor_attribute in sensors.values():
            for feature in sensor_attribute.features:
                # Sensor attribute features name a feature of the sensor's cluster.
                names.add((cluster_name, feature))
    return names


def test_features_emitted_by_code_exist():
    problems = []
    for cluster_name, feature_name in sorted(_feature_names_emitted_by_code()):
        ns = espmatter_names.feature_namespace(cluster_name, feature_name)
        if ns is None:
            problems.append((cluster_name, feature_name))
    assert not problems, f"features without esp-matter namespace: {problems}"


def test_default_and_dependency_features_exist():
    names = set(device_types.DEFAULT_CHOICE_FEATURES.values())
    for name, alternatives in device_types.FEATURE_DEPENDENCIES.items():
        names.add(name)
        names.update(n for alternative in alternatives for n in alternative)
    all_features = {f.name: f for c in CLUSTERS for f in c.features}
    for name in names:
        assert name in all_features, name
        assert all_features[name].espm_available, name


def test_sensor_attribute_create_functions_exist():
    """Optional sensor attributes are created with esp_matter::...::create_*()."""
    for dt in DEVICE_TYPES:
        for sensor_attribute in dt.sensor_attributes:
            cluster = sensor_attribute.cluster
            assert cluster.espm_available, (dt.name, cluster.name)
            create = espmatter_names.attribute_create_function(
                cluster.name, sensor_attribute.attribute.name
            )
            assert create, (dt.name, cluster.name, sensor_attribute.attribute.name)


def test_hardcoded_config_members_exist():
    """`{config_var}.<member>` written literally in the code generators."""
    members = set()
    for entry in TABLE["device_types"].values():
        members.update(entry["config_members"])
    pattern = re.compile(r"\{config_var\}\.([a-z_0-9]+)")
    allowed = {"feature_flags"}  # cluster configs, not endpoint configs
    problems = []
    for path in sorted((COMPONENTS / "matter").glob("*.py")):
        for name in pattern.findall(path.read_text()):
            if name not in members and name not in allowed:
                problems.append((path.name, name))
    assert not problems, f"unknown esp-matter endpoint config members: {problems}"


def test_table_is_up_to_date():
    """Re-running the generator against the esp-matter headers changes nothing."""
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import gen_espmatter_names as gen
    finally:
        sys.path.pop(0)
    try:
        esp_matter = gen._find_esp_matter()
    except SystemExit:
        pytest.skip("esp-matter headers not available")
    if "refs" in Path(esp_matter).parts:
        pytest.skip("only the pinned davidvtwout/esp_matter headers are authoritative")
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "gen_espmatter_names.py"),
            "--esp-matter",
            str(esp_matter),
            "--check",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
