"""Power Source (`battery:` / `wired:`, root node), Power Topology and Electrical Energy modelling.

Pure Python tests work on already validated configuration dicts; the schema tests run
``esphome config`` through the ``run_config`` fixture (see conftest.py).
"""

from __future__ import annotations

import pytest

from components.matter import conformance as c
from components.matter import power_source as ps
from components.matter import power_topology as pt
from tools.conformance.espm import Profile

ENTITIES = """
sensor:
  - platform: template
    id: pct
    unit_of_measurement: "%"
    device_class: battery
  - platform: template
    id: volts
    unit_of_measurement: "V"
    device_class: voltage
  - platform: template
    id: hz
    unit_of_measurement: "Hz"
  - platform: template
    id: amps
    unit_of_measurement: "A"
  - platform: template
    id: watts
    unit_of_measurement: "W"
    device_class: power
  - platform: template
    id: kwh
    unit_of_measurement: "kWh"
    device_class: energy
    state_class: total_increasing
binary_sensor:
  - platform: template
    id: flag_a
  - platform: template
    id: flag_b
  - platform: template
    id: flag_c
  - platform: template
    id: flag_d
"""


def matter(endpoints: str) -> str:
    return f"matter:\n  endpoints:\n{endpoints}\n{ENTITIES}"


def assert_invalid(run_config, body: str, *needles: str):
    result = run_config(body)
    assert not result.ok, result.output[-1500:]
    for needle in needles:
        assert needle in result.output, result.output[-2000:]


# --------------------------------------------------------------------------- #
# Power Source: schema
# --------------------------------------------------------------------------- #


def test_battery_map_on_root_node_and_application_endpoint(run_config):
    result = run_config(
        matter(
            """
    0:
      battery:
        percent: pct
        voltage: volts
        replacement_needed: flag_a
        charging: flag_b
        charged: flag_c
        replaceability: user_replaceable
        replacement_description: CR2032
        quantity: 2
    1:
      on_off_plug_in_unit: {}
      battery:
        low: flag_d
        powers: [1]
"""
        )
    )
    assert result.ok, result.output[-2000:]


def test_wired_source(run_config):
    result = run_config(
        matter(
            """
    0:
      wired:
        current_type: dc
        present: flag_a
        voltage: volts
        frequency: hz
        current: amps
    1:
      on_off_plug_in_unit: {}
"""
        )
    )
    assert result.ok, result.output[-2000:]


def test_battery_shorthand_still_works(run_config):
    result = run_config(
        matter(
            """
    1:
      on_off_plug_in_unit: {}
      battery: pct
"""
        )
    )
    assert result.ok, result.output[-2000:]


def test_battery_and_wired_on_one_endpoint_rejected(run_config):
    assert_invalid(
        run_config,
        matter(
            """
    1:
      on_off_plug_in_unit: {}
      battery: pct
      wired: {}
"""
        ),
        "cannot be on the same endpoint",
    )


def test_user_replaceable_needs_a_description(run_config):
    assert_invalid(
        run_config,
        matter(
            """
    1:
      on_off_plug_in_unit: {}
      battery:
        percent: pct
        replaceability: user_replaceable
"""
        ),
        "replacement_description",
    )


def test_low_flag_cannot_combine_with_percent(run_config):
    assert_invalid(
        run_config,
        matter(
            """
    1:
      on_off_plug_in_unit: {}
      battery:
        percent: pct
        low: flag_a
"""
        ),
        "cannot be combined with 'percent'",
    )


def test_thresholds_need_percent(run_config):
    assert_invalid(
        run_config,
        matter(
            """
    1:
      on_off_plug_in_unit: {}
      battery:
        voltage: volts
        warning_below: 30
"""
        ),
        "need a 'percent' sensor",
    )


def test_percent_must_be_a_percent_sensor(run_config):
    result = run_config(
        matter(
            """
    1:
      on_off_plug_in_unit: {}
      battery:
        percent: volts
"""
        )
    )
    assert not result.ok
    assert "cannot be used for" in result.output


def test_battery_clashes_with_power_source_device_type(run_config):
    assert_invalid(
        run_config,
        matter(
            """
    1:
      power_source:
        battery: pct
      battery: pct
"""
        ),
        "only once",
    )


def test_unknown_powers_endpoint_rejected(run_config):
    assert_invalid(
        run_config,
        matter(
            """
    0:
      wired:
        powers: [7]
    1:
      on_off_plug_in_unit: {}
"""
        ),
        "endpoint 7",
    )


# --------------------------------------------------------------------------- #
# Root node
# --------------------------------------------------------------------------- #


def test_root_node_rejects_application_device_types(run_config):
    assert_invalid(
        run_config,
        matter(
            """
    0:
      on_off_plug_in_unit: {}
"""
        ),
        "Endpoint 0 is the root node",
    )


def test_root_node_rejects_composition_options(run_config):
    assert_invalid(
        run_config,
        matter(
            """
    0:
      label: Root
      wired: {}
"""
        ),
        "not available on endpoint 0",
    )


def test_empty_root_node_rejected(run_config):
    assert_invalid(
        run_config,
        matter(
            """
    0: {}
    1:
      on_off_plug_in_unit: {}
"""
        ),
        "Endpoint 0 is the root node",
    )


# --------------------------------------------------------------------------- #
# Power Topology
# --------------------------------------------------------------------------- #


def test_topologies_validate(run_config):
    result = run_config(
        matter(
            """
    0:
      electrical_sensor:
        active_power: watts
    1:
      parent: 2
      electrical_sensor:
        topology: tree
        active_power: watts
    2:
      electrical_sensor:
        topology: tree
        active_power: watts
    3:
      electrical_sensor:
        topology: set
        available_endpoints: [1, 2]
        active_endpoints: [2]
        active_power: watts
"""
        )
    )
    assert result.ok, result.output[-2000:]


def test_set_topology_needs_available_endpoints(run_config):
    assert_invalid(
        run_config,
        matter(
            """
    1:
      electrical_sensor:
        topology: set
        active_power: watts
"""
        ),
        "available_endpoints",
    )


def test_available_endpoints_need_set_topology(run_config):
    assert_invalid(
        run_config,
        matter(
            """
    1:
      electrical_sensor:
        available_endpoints: [1]
        active_power: watts
"""
        ),
        "requires 'topology: set'",
    )


def test_active_endpoints_must_be_available(run_config):
    assert_invalid(
        run_config,
        matter(
            """
    1:
      electrical_sensor:
        topology: set
        available_endpoints: [1]
        active_endpoints: [2]
        active_power: watts
"""
        ),
        "not in 'available_endpoints'",
    )


def test_set_topology_endpoints_must_exist(run_config):
    assert_invalid(
        run_config,
        matter(
            """
    1:
      electrical_sensor:
        topology: set
        available_endpoints: [9]
        active_power: watts
"""
        ),
        "not a configured endpoint",
    )


def test_topology_features_are_not_selectable_by_hand(run_config):
    assert_invalid(
        run_config,
        matter(
            """
    1:
      electrical_sensor:
        with_features: [tree_topology]
        active_power: watts
"""
        ),
        "'topology' option",
    )


def test_several_node_topologies_warn(run_config):
    result = run_config(
        matter(
            """
    1:
      electrical_sensor:
        active_power: watts
    2:
      electrical_sensor:
        active_power: watts
"""
        )
    )
    assert result.ok, result.output[-2000:]
    assert "topology: node" in result.output


def test_electrical_sensor_next_to_a_cover(run_config):
    result = run_config(
        matter(
            """
    1:
      window_covering:
        cover_id: shade
      electrical_sensor:
        topology: tree
        active_power: watts

cover:
  - platform: template
    id: shade
    optimistic: true
"""
        )
    )
    assert result.ok, result.output[-2000:]


# --------------------------------------------------------------------------- #
# Pure Python helpers on validated configs
# --------------------------------------------------------------------------- #


def test_battery_features_follow_the_options():
    assert ps.features({"battery": {"percent": "x"}}) == ["Battery"]
    assert ps.features({"battery": {"charging": "x"}}) == ["Battery", "Rechargeable"]
    assert ps.features({"battery": {"rechargeable": True}}) == [
        "Battery",
        "Rechargeable",
    ]
    assert ps.features(
        {"battery": {"replaceability": "user_replaceable", "charged": "x"}}
    ) == ["Battery", "Rechargeable", "Replaceable"]
    assert ps.features({"battery": {"replaceability": "factory_replaceable"}}) == [
        "Battery"
    ]
    assert ps.features({"wired": {}}) == ["Wired"]
    assert ps.features({"on_off_light": {}}) == []


def test_config_lines_battery():
    options = {
        "order": 1,
        "description": 'Pack "A"',
        "replaceability": "user_replaceable",
        "replacement_description": "AAA",
        "quantity": 2,
        "charging": "x",
        "functional_while_charging": False,
    }
    lines = "\n".join(ps.config_lines("cfg", {"battery": options}))
    assert "cfg.status = 1;" in lines
    assert "cfg.order = 1;" in lines
    assert r'"Pack \"A\""' in lines
    assert "cfg.features.battery.bat_replaceability = 2;" in lines
    assert "cfg.features.rechargeable.bat_functional_while_charging = false;" in lines
    assert "cfg.features.rechargeable.bat_charge_state = 3;" in lines
    assert "cfg.features.replaceable.bat_quantity = 2;" in lines
    assert '"AAA"' in lines


def test_config_lines_wired():
    lines = "\n".join(
        ps.config_lines(
            "cfg", {"wired": {"order": 0, "description": "Mains", "current_type": "dc"}}
        )
    )
    assert "cfg.features.wired.wired_current_type = 1;" in lines
    assert "battery" not in lines


def test_endpoint_list_defaults():
    root = {"battery": {"description": "B", "order": 0}}
    assert ps.endpoint_list(0, root, (0, 3, 1, 2)) == [1, 2, 3]
    assert ps.endpoint_list(0, {"battery": {**root["battery"], "powers": [2]}}, (0, 1, 2)) == [2]
    # An application endpoint lists nothing unless asked to.
    assert ps.endpoint_list(1, root, (0, 1)) == []
    assert ps.endpoint_list(1, {"on_off_light": {}}, (0, 1)) == []


def test_max_device_types_counts_the_root_node_additions():
    assert c.max_device_types({0: {"battery": {}}, 1: {"on_off_light": {}}}) == 2
    assert c.max_device_types({0: {"battery": {}, "electrical_sensor": {}}}) == 3
    assert c.max_device_types({0: {"electrical_sensor": {}}}) == 2


def test_root_node_is_not_a_sibling():
    plans = c.plan_endpoints(
        {0: {"wired": {}}, 1: {"on_off_plug_in_unit": {}}, 2: {"on_off_plug_in_unit": {}}},
        log=False,
    )
    assert not plans[0].tags
    assert plans[1].tags and plans[2].tags


def test_topology_features():
    assert pt.feature_names({}) == ["NodeTopology"]
    assert pt.feature_names({"topology": "tree"}) == ["TreeTopology"]
    assert pt.feature_names({"topology": "set", "available_endpoints": [1]}) == [
        "SetTopology"
    ]
    assert pt.feature_names(
        {"topology": "set", "available_endpoints": [1], "active_endpoints": [1]}
    ) == ["SetTopology", "DynamicPowerFlow"]


def test_set_delegate_lines():
    lines = pt.delegate_lines(
        "cfg", "power_topology", {"topology": "set", "available_endpoints": [1, 2]}
    )
    assert lines == [
        "cfg.power_topology.delegate = esphome::matter::power_topology_delegate({1, 2}, {1, 2});"
    ]
    assert pt.delegate_lines("cfg", "power_topology", {"topology": "tree"}) == []


# --------------------------------------------------------------------------- #
# esp-matter creation model of Electrical Energy Measurement
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def profile():
    return Profile()


def _energy_attributes(profile, features):
    attributes, _commands, events = profile.predict(
        "ElectricalEnergyMeasurement", features
    )
    return attributes, events


def test_imported_only_energy_has_no_exported_attributes(profile):
    attributes, events = _energy_attributes(
        profile, ["ImportedEnergy", "CumulativeEnergy"]
    )
    assert "cumulativeenergyimported" in attributes
    assert "cumulativeenergyexported" not in attributes
    assert "periodicenergyimported" not in attributes
    assert "cumulativeenergymeasured" in events


def test_exported_only_energy_has_no_imported_attributes(profile):
    attributes, _ = _energy_attributes(profile, ["ExportedEnergy", "CumulativeEnergy"])
    assert "cumulativeenergyexported" in attributes
    assert "cumulativeenergyimported" not in attributes


def test_both_directions_and_periodic(profile):
    attributes, events = _energy_attributes(
        profile,
        ["ImportedEnergy", "ExportedEnergy", "CumulativeEnergy", "PeriodicEnergy"],
    )
    assert {
        "cumulativeenergyimported",
        "cumulativeenergyexported",
        "periodicenergyimported",
        "periodicenergyexported",
    } <= attributes
    assert {"cumulativeenergymeasured", "periodicenergymeasured"} <= events


def test_feature_dependencies_keep_their_conditional_attributes(profile):
    # Features that need another feature are conjunctions, other clusters stay unchanged.
    attributes, _commands, _events = profile.predict(
        "ElectricalPowerMeasurement", ["AlternatingCurrent", "Harmonics"]
    )
    assert "harmoniccurrents" in attributes
    attributes, _commands, _events = profile.predict(
        "ElectricalPowerMeasurement", ["Harmonics"]
    )
    assert "harmoniccurrents" not in attributes
