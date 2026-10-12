"""Static consistency checks of the generated data model JSON."""

import json

from conftest import DATA_MODEL_DIR
import pytest


@pytest.fixture(scope="module")
def model():
    clusters = json.loads((DATA_MODEL_DIR / "clusters.json").read_text())
    device_types = json.loads((DATA_MODEL_DIR / "device_types.json").read_text())
    return clusters, device_types


def test_device_type_cluster_references(model):
    clusters, device_types = model
    cluster_ids = {c["id"] for c in clusters}
    for dt in device_types:
        for ref in dt["server_clusters"] + dt["client_clusters"]:
            assert ref["id"] in cluster_ids, f"{dt['name']} -> unknown cluster {ref['name']} ({ref['id']})"


def test_names_and_ids_unique(model):
    clusters, device_types = model
    for items in (clusters, device_types):
        assert items
        assert all(i.get("name") for i in items)
        assert len({i["id"] for i in items}) == len(items)
    assert len({d["name"] for d in device_types}) == len(device_types)


def test_expected_device_types_present(model):
    _, device_types = model
    names = {d["name"] for d in device_types}
    assert {
        "on_off_light",
        "on_off_plug_in_unit",
        "mounted_on_off_control",
        "fan",
        "window_covering",
        "contact_sensor",
        "occupancy_sensor",
        "generic_switch",
        "temperature_sensor",
    } <= names
