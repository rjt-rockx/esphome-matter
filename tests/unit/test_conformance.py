"""Tests for tools/conformance (the spec conformance verifier).

The evaluator tests use inline XML and need nothing else. The rule tests need the
Matter data model XML (``MATTER_DATA_MODEL`` / the esp-matter checkout) and are
skipped when it is missing, unless ``MATTER_CONFORMANCE_REQUIRE=1`` (CI).
"""

from __future__ import annotations

import copy
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tools.conformance.checks import ERROR, check_node
from tools.conformance.conformance import Context, evaluate
from tools.conformance.espm import Profile
from tools.conformance.model import from_dump, from_layout, to_dump
from tools.conformance.spec import DEFAULT_SPEC_ROOT, Spec


def _conf(xml: str):
    return ET.fromstring(xml)


# --------------------------------------------------------------------------- evaluator
class TestEvaluator:
    def test_plain_kinds(self):
        ctx = Context()
        assert evaluate(_conf("<mandatoryConform/>"), ctx).kind == "M"
        assert evaluate(_conf("<optionalConform/>"), ctx).kind == "O"
        assert evaluate(_conf("<provisionalConform/>"), ctx).kind == "P"
        assert evaluate(_conf("<deprecateConform/>"), ctx).kind == "D"
        assert evaluate(_conf("<disallowConform/>"), ctx).kind == "X"

    def test_feature_guard(self):
        el = _conf('<mandatoryConform><feature name="LT"/></mandatoryConform>')
        assert evaluate(el, Context(features={"LT"})).kind == "M"
        assert evaluate(el, Context(features={"OO"})).kind == "N"

    def test_boolean_terms(self):
        el = _conf(
            '<mandatoryConform><andTerm><feature name="A"/><notTerm><feature name="B"/></notTerm></andTerm></mandatoryConform>'
        )
        assert evaluate(el, Context(features={"A"})).kind == "M"
        assert evaluate(el, Context(features={"A", "B"})).kind == "N"
        el = _conf(
            '<optionalConform><orTerm><feature name="A"/><feature name="B"/></orTerm></optionalConform>'
        )
        assert evaluate(el, Context(features={"B"})).kind == "O"
        assert evaluate(el, Context()).kind == "N"
        el = _conf(
            '<mandatoryConform><xorTerm><feature name="A"/><feature name="B"/></xorTerm></mandatoryConform>'
        )
        assert evaluate(el, Context(features={"A", "B"})).kind == "N"
        assert evaluate(el, Context(features={"A"})).kind == "M"

    def test_otherwise_takes_first_applicable(self):
        el = _conf(
            """<otherwiseConform>
                 <mandatoryConform><feature name="A"/></mandatoryConform>
                 <optionalConform><feature name="B"/></optionalConform>
                 <disallowConform/>
               </otherwiseConform>"""
        )
        assert evaluate(el, Context(features={"A"})).kind == "M"
        assert evaluate(el, Context(features={"B"})).kind == "O"
        assert evaluate(el, Context()).kind == "X"

    def test_unknown_condition_is_unknown_not_false(self):
        el = _conf('<mandatoryConform><condition name="Mystery"/></mandatoryConform>')
        assert evaluate(el, Context()).kind == "?"
        assert evaluate(el, Context(conditions={"Mystery": True})).kind == "M"
        assert evaluate(el, Context(conditions={"Mystery": False})).kind == "N"
        # unknown AND false is false, unknown OR true is true
        el = _conf(
            '<mandatoryConform><andTerm><condition name="Mystery"/><feature name="A"/></andTerm></mandatoryConform>'
        )
        assert evaluate(el, Context()).kind == "N"
        el = _conf(
            '<mandatoryConform><orTerm><condition name="Mystery"/><feature name="A"/></orTerm></mandatoryConform>'
        )
        assert evaluate(el, Context(features={"A"})).kind == "M"

    def test_attribute_and_command_terms(self):
        el = _conf('<mandatoryConform><attribute name="Foo"/></mandatoryConform>')
        assert evaluate(el, Context(attributes={"foo"})).kind == "M"
        assert evaluate(el, Context(attributes=set())).kind == "N"
        assert evaluate(el, Context(attributes=None)).kind == "?"
        el = _conf('<optionalConform><command name="Bar"/></optionalConform>')
        assert evaluate(el, Context(commands={"bar"})).kind == "O"

    def test_revision_and_value_comparison(self):
        el = _conf(
            '<mandatoryConform><greaterOrEqualTerm><revision value="current"/><revision value="3"/></greaterOrEqualTerm></mandatoryConform>'
        )
        assert evaluate(el, Context(revision=3)).kind == "M"
        assert evaluate(el, Context(revision=2)).kind == "N"
        assert evaluate(el, Context()).kind == "?"
        el = _conf(
            '<mandatoryConform><greaterTerm><attribute name="NumberOfPrimaries"/><literal value="0"/></greaterTerm></mandatoryConform>'
        )
        assert evaluate(el, Context(values={"numberofprimaries": 3})).kind == "M"
        assert evaluate(el, Context(values={"numberofprimaries": 0})).kind == "N"
        assert evaluate(el, Context()).kind == "?"

    def test_choice_metadata(self):
        out = evaluate(
            _conf('<optionalConform choice="a" more="true" min="1"/>'), Context()
        )
        assert (out.choice, out.min, out.more, out.max) == ("a", 1, True, None)
        out = evaluate(_conf('<optionalConform choice="b"/>'), Context())
        assert (out.min, out.max) == (1, 1)  # exactly one
        out = evaluate(_conf('<optionalConform choice="c" max="1"/>'), Context())
        assert out.max == 1


# --------------------------------------------------------------------------- rules
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


LIGHT_LAYOUT = {
    "format": 1,
    "network": {"wifi": True, "thread": False, "ethernet": False},
    "endpoints": {
        "1": {
            "device_types": [{"name": "dimmable_light", "id": 257}],
            "clusters": {},
            "dynamic_features": [],
        }
    },
}


def _errors(rep):
    return [f for f in rep.findings if f.level == ERROR]


def _codes(rep, where_sub=""):
    return {(f.code, f.where) for f in _errors(rep) if where_sub in f.where}


class TestSpecLoader:
    def test_loads_everything(self, spec):
        assert len(spec.clusters) > 100
        assert len(spec.device_types) > 80
        assert spec.base_device_type is not None
        assert 0x0101 in spec.device_types
        # derived mode clusters inherit the base attributes/commands
        assert 0x0059 in spec.clusters and spec.clusters[0x0059].attributes
        assert any(
            c.name == "ChangeToMode" for c in spec.clusters[0x0059].commands.values()
        )

    def test_every_conformance_expression_evaluates(self, spec):
        """No cluster, device type or global conformance may crash the evaluator."""
        from tools.conformance.conformance import evaluate

        for cspec in spec.clusters.values():
            all_features = set(cspec.features)
            for ctx in (
                Context(),
                Context(
                    features=all_features,
                    attributes=set(),
                    commands=set(),
                    events=set(),
                    revision=cspec.revision,
                ),
            ):
                for f in cspec.features.values():
                    evaluate(f.conform, ctx)
                for table in (cspec.attributes, cspec.commands, cspec.events):
                    for e in table.values():
                        evaluate(e.conform, ctx)
        for dt in [*spec.device_types.values(), spec.base_device_type]:
            for req in dt.clusters:
                for conf in [req.conform, *req.features.values()]:
                    if conf is not None:
                        evaluate(conf, Context())


class TestLayoutMode:
    def test_dimmable_light_is_clean_apart_from_known_sdk_gaps(self, spec, profile):
        rep = check_node(from_layout(LIGHT_LAYOUT, spec, profile), spec)
        errs = _codes(rep)
        # DeviceLoadStatus is an esp-matter 1.6 gap (see data/known_gaps.json); nothing else.
        assert {c for c, _ in errs} <= {"CL-ATTR-MISSING"}, errs
        assert all("General Diagnostics" in w for _, w in errs)

    def test_duplicate_device_types_require_tag_list(self, spec, profile):
        layout = copy.deepcopy(LIGHT_LAYOUT)
        layout["endpoints"]["2"] = copy.deepcopy(layout["endpoints"]["1"])
        rep = check_node(from_layout(layout, spec, profile), spec)
        tag = [
            f
            for f in _errors(rep)
            if f.code == "DT-FEATURE-MISSING" and "TagList" in f.message
        ]
        assert {f.where.split(" /")[0] for f in tag} == {"endpoint 1", "endpoint 2"}

    def test_electrical_sensor_needs_a_measurement_cluster(self, spec, profile):
        layout = copy.deepcopy(LIGHT_LAYOUT)
        layout["endpoints"] = {
            "1": {
                "device_types": [{"name": "electrical_sensor", "id": 0x510}],
                "clusters": {},
                "dynamic_features": [],
            }
        }
        rep = check_node(from_layout(layout, spec, profile), spec)
        assert any(f.code == "EP-ELECTRICAL" for f in _errors(rep))

    def test_network_features_follow_the_radio(self, spec, profile):
        for net, code in (
            ({"wifi": True}, "WI"),
            ({"thread": True}, "TH"),
            ({"ethernet": True}, "ET"),
        ):
            layout = copy.deepcopy(LIGHT_LAYOUT)
            layout["network"] = {
                "wifi": False,
                "thread": False,
                "ethernet": False,
                **net,
            }
            model = from_layout(layout, spec, profile)
            assert model.endpoints[0].servers[0x31].features == {code}


class TestDumpMode:
    @pytest.fixture()
    def good_dump(self, spec, profile):
        return to_dump(from_layout(LIGHT_LAYOUT, spec, profile), spec)

    def test_round_trip_matches_layout_mode(self, spec, profile, good_dump):
        layout_rep = check_node(from_layout(LIGHT_LAYOUT, spec, profile), spec)
        dump_rep = check_node(from_dump(good_dump, spec), spec)
        assert _codes(layout_rep) == _codes(dump_rep)

    def test_flat_path_map_is_accepted(self, spec, profile, good_dump):
        flat = {
            f"{ep}/{cl}/{at}": v
            for ep, cls in good_dump.items()
            for cl, ats in cls.items()
            for at, v in ats.items()
        }
        assert _codes(check_node(from_dump(flat, spec), spec)) == _codes(
            check_node(from_dump(good_dump, spec), spec)
        )

    def test_missing_mandatory_attribute(self, spec, profile, good_dump):
        d = copy.deepcopy(good_dump)
        attrs = d["1"]["6"]  # OnOff
        del attrs["0"]
        attrs["65531"].remove(0)
        rep = check_node(from_dump(d, spec), spec)
        assert any(
            f.code == "CL-ATTR-MISSING" and "OnOff" in f.message and "On/Off" in f.where
            for f in _errors(rep)
        )

    def test_missing_mandatory_cluster(self, spec, profile, good_dump):
        d = copy.deepcopy(good_dump)
        del d["1"]["3"]  # Identify
        d["1"]["29"]["1"].remove(3)
        rep = check_node(from_dump(d, spec), spec)
        assert any(
            f.code == "DT-CLUSTER-MISSING" and "Identify" in f.message
            for f in _errors(rep)
        )

    def test_feature_required_by_device_type(self, spec, profile, good_dump):
        d = copy.deepcopy(good_dump)
        d["1"]["8"]["65532"] = 1  # LevelControl: OnOff only, no Lighting
        rep = check_node(from_dump(d, spec), spec)
        assert any(
            f.code == "DT-FEATURE-MISSING" and "Lighting" in f.message
            for f in _errors(rep)
        )

    def test_feature_gated_attribute_missing(self, spec, profile, good_dump):
        d = copy.deepcopy(good_dump)
        d["1"]["6"]["65532"] = 0  # OnOff without Lighting
        rep = check_node(from_dump(d, spec), spec)
        # Lighting attributes (OnTime ...) now present although the feature is off
        assert any(f.code == "CL-ATTR-NA" for f in rep.findings)

    def test_undefined_feature_bit_and_cluster_revision(self, spec, profile, good_dump):
        d = copy.deepcopy(good_dump)
        d["1"]["6"]["65532"] = 1 << 12
        d["1"]["8"]["65533"] = 99
        rep = check_node(from_dump(d, spec), spec)
        codes = {f.code for f in _errors(rep)}
        assert {"CL-FEATURE-UNDEFINED", "CL-REVISION"} <= codes

    def test_partslist_and_serverlist_consistency(self, spec, profile, good_dump):
        d = copy.deepcopy(good_dump)
        d["0"]["29"]["3"] = []  # endpoint 1 unreachable
        d["1"]["29"]["1"].append(0x9999)  # ServerList lists an unread cluster
        rep = check_node(from_dump(d, spec), spec)
        codes = {f.code for f in _errors(rep)}
        assert {"EP-PARTS", "EP-SERVERLIST"} <= codes

    def test_root_node_rules(self, spec, profile, good_dump):
        d = copy.deepcopy(good_dump)
        d["0"]["29"]["0"] = [{"deviceType": 0x0101, "revision": 3}]
        rep = check_node(from_dump(d, spec), spec)
        assert any(f.code == "NODE-ROOT" for f in _errors(rep))
        assert any(f.code == "EP-DEVTYPE-CLASS" for f in _errors(rep))

    def test_choice_group_violation(self, spec, profile, good_dump):
        d = copy.deepcopy(good_dump)
        d["0"]["49"]["65532"] = 0  # Network Commissioning without any interface feature
        rep = check_node(from_dump(d, spec), spec)
        assert any(f.code == "CL-CHOICE" for f in _errors(rep))


@pytest.mark.slow
class TestRealConfigs:
    """End-to-end: codegen -> layout -> rules. Needs esphome; a few seconds each."""

    def test_codegen_layout_has_expected_shape(self):
        from tools.conformance.layout import extract_layout

        repo = Path(__file__).resolve().parents[2]
        layout = extract_layout(repo / "tests" / "configs" / "esp32-lights.yaml")
        assert layout["format"] == 1
        types = {e["device_types"][0]["name"] for e in layout["endpoints"].values()}
        assert {"on_off_light", "dimmable_light"} <= types

    @pytest.mark.parametrize("name", ["esp32-wifi", "esp32-lights", "esp32-sensors"])
    def test_configs_only_have_known_gaps(self, spec, profile, name):
        from tools.conformance import baseline
        from tools.conformance.__main__ import run_one

        repo = Path(__file__).resolve().parents[2]
        rep = run_one(
            repo / "tests" / "configs" / f"{name}.yaml",
            spec,
            profile,
            True,
            baseline.load(),
        )
        assert rep.count(ERROR) == 0, [
            f.as_dict() for f in rep.findings if f.level == ERROR
        ]
