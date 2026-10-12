"""Matter bridge: `bridge:` block lowering, hand-written Aggregator / Bridged Node topology, BDBI codegen and the
conformance rules for them. See docs/bridge.md."""

from __future__ import annotations

import os
import re
import textwrap
from pathlib import Path

from conftest import run_esphome
import pytest

from components.matter import bridge
from tools.conformance.checks import ERROR, WARNING, check_node
from tools.conformance.espm import Profile
from tools.conformance.model import from_dump, from_layout
from tools.conformance.spec import DEFAULT_SPEC_ROOT, Spec

SENSORS = """
sensor:
  - platform: template
    id: temp
    name: Temperature
    unit_of_measurement: "°C"
    device_class: temperature
  - platform: template
    id: hum
    name: Humidity
    unit_of_measurement: "%"
    device_class: humidity
  - platform: template
    id: batt
    name: Battery
    unit_of_measurement: "%"
    device_class: battery
switch:
  - platform: template
    id: relay
    optimistic: true
    restore_mode: ALWAYS_OFF
binary_sensor:
  - platform: template
    id: seen
"""
# Snippets are appended to test bodies indented by 12 spaces; keep them aligned.
SENSORS = textwrap.indent(SENSORS, " " * 12)


# --------------------------------------------------------------------------- helpers (no esphome run)
class TestHelpers:
    def test_default_unique_id_is_stable_and_fits(self):
        a = bridge.default_unique_id("kitchen")
        assert a == bridge.default_unique_id("kitchen")
        assert len(a.encode()) <= 32
        long = bridge.default_unique_id("x" * 60)
        assert len(long.encode()) == 32 and long != bridge.default_unique_id("y" * 60)

    def test_c_string_escapes_quotes_and_non_ascii(self):
        assert bridge._c_string('a"b\\c') == '"a\\042b\\134c"'
        # UTF-8 bytes as 3-digit octal escapes: a following hex digit cannot be swallowed
        assert bridge._c_string("é1") == '"\\303\\2511"'

    def test_post_create_lines(self):
        lines = bridge.post_create_lines(
            {
                "name": "Node",
                "vendor_name": "ACME",
                "serial_number": "S1",
                "software_version": 7,
            }
        )
        text = "\n".join(lines)
        assert "create_node_label" in text and 'const_cast<char *>("Node"), 4' in text
        assert "create_vendor_name" in text and "create_serial_number" in text
        assert "create_software_version(bdbi_cluster, static_cast<uint32_t>(7U))" in text
        assert "create_product_name" not in text

    def test_config_lines_reachable_starts_false_when_bound(self):
        bound = bridge.config_lines("cfg", 3, {"name": "n", "reachable": object(), "unique_id": "u"})
        unbound = bridge.config_lines("cfg", 3, {"name": "n"})
        assert any(".reachable = false;" in l for l in bound)
        assert any(".reachable = true;" in l for l in unbound)
        assert any('"u"' in l for l in bound)

    def test_attribute_names(self):
        assert bridge.attribute_names({"name": "n"}) == ["NodeLabel"]
        assert bridge.attribute_names({"name": "n", "product_url": "u", "vendor_id": 5}) == [
            "NodeLabel",
            "ProductURL",
            "VendorID",
        ]


# --------------------------------------------------------------------------- bridge: block
class TestBridgeBlock:
    def test_lowers_into_aggregator_and_nodes(self, run_config):
        result = run_config(
            """
            matter:
              bridge:
                devices:
                  thermo:
                    name: Thermometer
                    reachable: seen
                    battery: batt
                    temperature_sensor: temp
                    humidity_sensor: hum
                  plug:
                    endpoint: 100
                    on_off_plug_in_unit:
                      switch_id: relay
            """
            + SENSORS
        )
        assert result.ok, result.output
        out = result.output
        assert "aggregator: {}" in out
        assert out.count("bridged_node:") == 2
        assert "100:" in out  # pinned
        # three application devices: temp + humidity child endpoints below the node, plug in the node endpoint
        assert out.count("temperature_sensor:") == 1 and out.count("humidity_sensor:") == 1
        assert "unique_id: matter-unit-plug" in out or "unique_id:" in out

    def test_primary_shares_the_bridged_node_endpoint(self, run_config):
        result = run_config(
            """
            matter:
              bridge:
                endpoint: 1
                devices:
                  heater:
                    endpoint: 2
                    primary: on_off_plug_in_unit
                    on_off_plug_in_unit:
                      switch_id: relay
                    temperature_sensor: temp
            """
            + SENSORS
        )
        assert result.ok, result.output
        node = re.split(r"\n    \d+:\n", result.output.split("    2:\n", 1)[1])[0]
        assert "bridged_node:" in node and "on_off_plug_in_unit:" in node
        assert "temperature_sensor:" not in node
        assert result.output.count("temperature_sensor:") == 1

    def test_primary_must_be_a_device_type_of_the_device(self, run_config):
        result = run_config(
            """
            matter:
              bridge:
                devices:
                  a:
                    primary: humidity_sensor
                    temperature_sensor: temp
            """
            + SENSORS
        )
        assert not result.ok and "`primary: humidity_sensor` is not" in result.output

    def test_endpoint_numbers_are_stable(self, run_config):
        body = """
            matter:
              bridge:
                devices:
                  thermo:
                    temperature_sensor: temp
            """ + SENSORS
        first = run_config(body)
        second = run_config(body)
        assert first.ok, first.output
        numbers = lambda r: [l for l in r.output.splitlines() if l.startswith("    ") and l.rstrip().endswith(":") and l.strip()[:-1].isdigit()]
        assert numbers(first) == numbers(second) and len(numbers(first)) == 2

    def test_pin_collision_is_a_clear_error(self, run_config):
        result = run_config(
            """
            matter:
              bridge:
                endpoint: 7
                devices:
                  a:
                    endpoint: 7
                    temperature_sensor: temp
            """
            + SENSORS
        )
        assert not result.ok
        assert "pinned twice" in result.output

    def test_device_without_application_type(self, run_config):
        result = run_config(
            """
            matter:
              bridge:
                devices:
                  a:
                    name: Empty
                    battery: batt
            """
            + SENSORS
        )
        assert not result.ok
        assert "no application device type" in result.output

    def test_unknown_option_suggests_a_key(self, run_config):
        result = run_config(
            """
            matter:
              bridge:
                devices:
                  a:
                    temperature_senso: temp
            """
            + SENSORS
        )
        assert not result.ok
        assert "Did you mean 'temperature_sensor'" in result.output

    def test_reserved_keys_are_rejected(self, run_config):
        for key in ("bridged_node: x", "aggregator: {}", "parent: 1"):
            result = run_config(
                textwrap.dedent(
                f"""
                matter:
                  bridge:
                    devices:
                      a:
                        {key}
                        temperature_sensor: temp
                """
                )
                + textwrap.dedent(SENSORS)
            )
            assert not result.ok and "is not allowed in a bridge device" in result.output, key

    def test_nested_false_conflicts_with_several_types(self, run_config):
        result = run_config(
            """
            matter:
              bridge:
                devices:
                  a:
                    nested: false
                    temperature_sensor: temp
                    humidity_sensor: hum
            """
            + SENSORS
        )
        assert not result.ok and "need child endpoints" in result.output

    def test_bad_identity_values(self, run_config):
        for line, needle in (
            ("vendor_name: " + "x" * 33, "UTF-8 bytes"),
            ("manufacturing_date: yesterday", "YYYYMMDD"),
            ("vendor_id: 0", "vendor_id"),
        ):
            result = run_config(
                textwrap.dedent(
                f"""
                matter:
                  bridge:
                    devices:
                      a:
                        {line}
                        temperature_sensor: temp
                """
                )
                + textwrap.dedent(SENSORS)
            )
            assert not result.ok and needle in result.output, line

    def test_unknown_entity_id_is_reported_with_the_device(self, run_config):
        result = run_config(
            """
            matter:
              bridge:
                devices:
                  a:
                    temperature_sensor: nope
            """
            + SENSORS
        )
        assert not result.ok and "nope" in result.output

    def test_mixes_with_hand_written_endpoints(self, run_config):
        result = run_config(
            """
            matter:
              bridge:
                endpoint: 5
                devices:
                  a:
                    temperature_sensor: temp
              endpoints:
                1:
                  on_off_plug_in_unit:
                    switch_id: relay
            """
            + SENSORS
        )
        assert result.ok, result.output


# --------------------------------------------------------------------------- hand-written endpoints
class TestHandWritten:
    def test_single_aggregator_gives_the_parent(self, run_config):
        result = run_config(
            """
            matter:
              endpoints:
                1:
                  aggregator: {}
                2:
                  bridged_node: Relay
                  on_off_plug_in_unit:
                    switch_id: relay
            """
            + SENSORS
        )
        assert result.ok, result.output
        assert "parent: 1" in result.output

    def test_bridged_node_without_aggregator(self, run_config):
        result = run_config(
            """
            matter:
              endpoints:
                2:
                  bridged_node: Relay
                  on_off_plug_in_unit:
                    switch_id: relay
            """
            + SENSORS
        )
        assert not result.ok and "there is no aggregator" in result.output

    def test_two_aggregators_need_a_parent(self, run_config):
        result = run_config(
            """
            matter:
              endpoints:
                1:
                  aggregator: {}
                2:
                  aggregator: {}
                3:
                  bridged_node: Relay
                  on_off_plug_in_unit:
                    switch_id: relay
            """
            + SENSORS
        )
        assert not result.ok and "several aggregators" in result.output

    def test_parent_must_be_an_aggregator(self, run_config):
        result = run_config(
            """
            matter:
              endpoints:
                1:
                  aggregator: {}
                2:
                  on_off_plug_in_unit:
                    switch_id: relay
                3:
                  parent: 2
                  bridged_node: Relay
                  temperature_sensor: temp
            """
            + SENSORS
        )
        assert not result.ok and "not an aggregator endpoint" in result.output

    def test_bridged_node_under_the_root_is_rejected(self, run_config):
        result = run_config(
            """
            matter:
              endpoints:
                1:
                  aggregator: {}
                2:
                  parent: 0
                  bridged_node: Relay
                  temperature_sensor: temp
            """
            + SENSORS
        )
        assert not result.ok and "direct child of the Aggregator" in result.output

    def test_aggregator_with_application_type(self, run_config):
        result = run_config(
            """
            matter:
              endpoints:
                1:
                  aggregator: {}
                  temperature_sensor: temp
            """
            + SENSORS
        )
        assert not result.ok and "cannot also be temperature_sensor" in result.output

    def test_nested_aggregator(self, run_config):
        result = run_config(
            """
            matter:
              endpoints:
                1:
                  aggregator: {}
                2:
                  parent: 1
                  aggregator: {}
            """
        )
        assert not result.ok and "direct child of the root node" in result.output

    def test_duplicate_unique_id(self, run_config):
        result = run_config(
            """
            matter:
              endpoints:
                1:
                  aggregator: {}
                2:
                  bridged_node: {name: A, unique_id: same}
                  temperature_sensor: temp
                3:
                  bridged_node: {name: B, unique_id: same}
                  humidity_sensor: hum
            """
            + SENSORS
        )
        assert not result.ok and "same UniqueID" in result.output

    def test_name_is_required(self, run_config):
        result = run_config(
            """
            matter:
              endpoints:
                1:
                  aggregator: {}
                2:
                  bridged_node: {}
                  temperature_sensor: temp
            """
            + SENSORS
        )
        assert not result.ok and "name" in result.output


# --------------------------------------------------------------------------- generated code
@pytest.mark.slow
class TestCodegen:
    def _generate(self, config: str, tmp_path):
        from conftest import CONFIGS, prepare_config

        prepared = prepare_config(CONFIGS / config, tmp_path)
        result = run_esphome("compile", "--only-generate", str(prepared), cwd=tmp_path)
        assert result.ok, result.output[-3000:]
        (main,) = list(tmp_path.glob(".esphome/build/*/src/main.cpp"))
        return main.read_text(), tmp_path

    def test_hand_written_bridge(self, tmp_path):
        code, _ = self._generate("esp32-bridge.yaml", tmp_path)
        assert "create_node_label" in code
        assert 'create_vendor_name(bdbi_cluster, const_cast<char *>("ACME"), 4)' in code
        assert 'create_serial_number(bdbi_cluster, const_cast<char *>("R2-0001"), 7)' in code
        assert "create_hardware_version(bdbi_cluster, static_cast<uint16_t>(1U))" in code
        assert "register_binary_sensor_attribute(garage_online, 4, 57, 17" in code
        assert "register_endpoint_parent(2, 1)" in code
        assert "register_endpoint_parent(5, 4)" in code
        assert 'unique_id, "relay-two"' in code
        # no explicit unique_id: <esphome name>-<endpoint>
        assert '"matter-ci-bridge-2"' in code
        # the Bridged Node device type is added before the application device type
        endpoint_2 = code[code.index("register_endpoint(2,") :]
        assert endpoint_2.index("bridged_node::add") < endpoint_2.index("on_off_plug_in_unit::add")

    def test_bridge_block(self, tmp_path):
        code, _ = self._generate("esp32-bridge-block.yaml", tmp_path)
        assert "register_endpoint(100," in code  # pinned
        assert "register_binary_sensor_attribute(kitchen_seen," in code
        assert code.count("bridged_node::add") == 4
        assert code.count("aggregator::add") == 1
        # Power Source EndpointList names the Bridged Node and its child endpoints
        assert "MatterPowerSourceEndpointList(" in code
        assert "CONFIG_SUPPORT_BRIDGED_DEVICE_BASIC_INFORMATION_CLUSTER=y" in next(
            tmp_path.glob(".esphome/build/*/sdkconfig.*")
        ).read_text()


# --------------------------------------------------------------------------- conformance checker
@pytest.fixture(scope="module")
def spec():
    root = Path(os.environ.get("MATTER_DATA_MODEL") or DEFAULT_SPEC_ROOT)
    if not (root / "1.6").is_dir() and not (root / "device_types").is_dir():
        if os.environ.get("MATTER_CONFORMANCE_REQUIRE") == "1":
            pytest.fail(f"Matter data model not found at {root}")
        pytest.skip("Matter data model XML not available (set MATTER_DATA_MODEL)")
    return Spec()


@pytest.fixture(scope="module")
def profile():
    return Profile()


def _bdbi(unique_id="n-1", label="Node", attrs=()):
    return {
        "BridgedDeviceBasicInformation": {
            "id": 0x39,
            "source": "device_type",
            "features": [],
            "optional_attributes": ["NodeLabel", *attrs],
        }
    }


def _layout(**endpoints):
    return {
        "format": 1,
        "network": {"wifi": True, "thread": False, "ethernet": False},
        "endpoints": {str(k): v for k, v in endpoints.items()},
    }


def _agg(parent=0):
    return {"device_types": [{"name": "aggregator", "id": 0x0E}], "clusters": {}, "dynamic_features": [], "parent": parent}


def _node(parent, unique_id="n-1", label="Node", app=True, name="temperature_sensor", did=0x302):
    types = [{"name": "bridged_node", "id": 0x13}]
    if app:
        types.append({"name": name, "id": did})
    return {
        "device_types": types,
        "clusters": _bdbi(),
        "dynamic_features": [],
        "parent": parent,
        "bridge": {"unique_id": unique_id, "node_label": label},
    }


def _codes(rep, level=ERROR):
    return sorted({(f.code, f.where) for f in rep.findings if f.level == level and f.code.startswith("BR-")})


def _check(spec, profile, layout):
    return check_node(from_layout(layout, spec, profile), spec)


class TestCheckerLayout:
    def test_valid_bridge_is_clean(self, spec, profile):
        layout = _layout(**{"1": _agg(), "2": _node(1), "3": _node(1, "n-2", "Other")})
        rep = _check(spec, profile, layout)
        assert not _codes(rep) and not _codes(rep, WARNING)

    def test_composed_bridged_node(self, spec, profile):
        child = {"device_types": [{"name": "temperature_sensor", "id": 0x302}], "clusters": {}, "dynamic_features": [], "parent": 2}
        layout = _layout(**{"1": _agg(), "2": _node(1, app=False), "3": child})
        rep = _check(spec, profile, layout)
        assert not _codes(rep) and not _codes(rep, WARNING)

    def test_bridged_node_under_root(self, spec, profile):
        rep = _check(spec, profile, _layout(**{"1": _agg(), "2": _node(0)}))
        assert ("BR-NODE-NOAGG", "endpoint 2") in _codes(rep)

    def test_bridged_node_under_application_endpoint(self, spec, profile):
        other = {"device_types": [{"name": "temperature_sensor", "id": 0x302}], "clusters": {}, "dynamic_features": [], "parent": 0}
        rep = _check(spec, profile, _layout(**{"1": _agg(), "2": other, "3": _node(2)}))
        assert ("BR-NODE-NOAGG", "endpoint 3") in _codes(rep)

    def test_nested_aggregator(self, spec, profile):
        rep = _check(spec, profile, _layout(**{"1": _agg(), "2": _agg(1), "3": _node(2)}))
        assert ("BR-AGG-PARENT", "endpoint 2") in _codes(rep)

    def test_aggregator_with_application_type(self, spec, profile):
        agg = _agg()
        agg["device_types"].append({"name": "temperature_sensor", "id": 0x302})
        rep = _check(spec, profile, _layout(**{"1": agg, "2": _node(1)}))
        assert ("BR-AGG-APP", "endpoint 1") in _codes(rep)

    def test_duplicate_unique_id_and_label(self, spec, profile):
        layout = _layout(**{"1": _agg(), "2": _node(1), "3": _node(1, name="humidity_sensor", did=0x307)})
        rep = _check(spec, profile, layout)
        assert ("BR-UNIQUEID", "endpoint 3") in _codes(rep)
        assert ("BR-NODELABEL", "endpoint 3") in _codes(rep, WARNING)

    def test_missing_and_overlong_unique_id(self, spec, profile):
        layout = _layout(**{"1": _agg(), "2": _node(1, unique_id=""), "3": _node(1, "x" * 33, "Other", name="humidity_sensor", did=0x307)})
        rep = _check(spec, profile, layout)
        assert ("BR-UNIQUEID", "endpoint 2") in _codes(rep)
        assert ("BR-UNIQUEID", "endpoint 3") in _codes(rep)

    def test_empty_bridge_and_empty_node(self, spec, profile):
        rep = _check(spec, profile, _layout(**{"1": _agg()}))
        assert ("BR-AGG-EMPTY", "endpoint 1") in _codes(rep, WARNING)
        rep = _check(spec, profile, _layout(**{"1": _agg(), "2": _node(1, app=False)}))
        assert ("BR-NODE-EMPTY", "endpoint 2") in _codes(rep, WARNING)

    def test_non_bridged_child_of_aggregator(self, spec, profile):
        other = {"device_types": [{"name": "temperature_sensor", "id": 0x302}], "clusters": {}, "dynamic_features": [], "parent": 1}
        rep = _check(spec, profile, _layout(**{"1": _agg(), "2": _node(1), "3": other}))
        assert ("BR-AGG-CHILD", "endpoint 3") in _codes(rep, WARNING)

    def test_siblings_below_different_parents_need_no_tag_list(self, spec, profile):
        # Same application device type, different parents: not siblings, so no Duplicate condition.
        child = lambda parent: {"device_types": [{"name": "temperature_sensor", "id": 0x302}], "clusters": {}, "dynamic_features": [], "parent": parent}
        layout = _layout(**{"1": _agg(), "2": _node(1, app=False), "3": _node(1, "n-2", "Other", app=False), "4": child(2), "5": child(3)})
        rep = _check(spec, profile, layout)
        assert not [f for f in rep.findings if f.level == ERROR and "TagList" in f.message]


class TestCheckerDump:
    def _dump(self, parts_aggregator):
        def ep(device_types, parts, bdbi=None):
            out = {
                "29": {"0": [{"0": d, "1": 1} for d in device_types], "1": [29], "2": [], "3": parts, "65528": [], "65529": [], "65531": [0, 1, 2, 3, 65528, 65529, 65531, 65532, 65533], "65532": 0, "65533": 2},
            }
            if bdbi:
                out["57"] = bdbi
            return out

        bdbi = {"18": "uid-1", "5": "Node", "17": True, "65532": 0, "65533": 4, "65531": [5, 17, 18], "65528": [], "65529": []}
        return {
            "0": ep([0x16], [1, 2, 3]),
            "1": ep([0x0E], parts_aggregator),
            "2": ep([0x13], [3], bdbi),
            "3": ep([0x302], []),
        }

    def test_aggregator_lists_all_descendants(self, spec):
        rep = check_node(from_dump(self._dump([2, 3]), spec), spec)
        assert not [f for f in rep.findings if f.code.startswith("BR-") and f.level == ERROR]

    def test_missing_descendant_in_parts_list(self, spec):
        rep = check_node(from_dump(self._dump([2]), spec), spec)
        assert any(f.code == "BR-PARTS" for f in rep.findings if f.level == ERROR)
