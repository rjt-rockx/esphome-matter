import asyncio

import esphome.codegen as cg
from esphome.core import CORE

from components.matter.const import CONF_CLUSTERS, CONF_WITH_FEATURES
from components.matter.data_model.clusters import CLUSTERS_BY_ID, CLUSTERS_BY_NAME
from components.matter.data_model.conformance import resolve_feature_requirements
from components.matter.endpoints import Endpoint


def test_events_enable_features() -> None:
    """The switch.initial_press requires the momentary_switch feature and should automatically enable the momentary_switch_release feature."""
    cluster = CLUSTERS_BY_NAME["Switch"]
    event = cluster.get_event("initial_press")

    inferred_features = resolve_feature_requirements(
        [event.conformance],
        {feature.code: feature.conformance for feature in cluster.features},
        set(),
    )

    assert inferred_features == {"MS"}


def test_feature_cascading() -> None:
    """The switch.short_release requires the momentary_switch_release feature, which in turn requires the momentary_switch feature, so both should be enabled."""
    cluster = CLUSTERS_BY_NAME["Switch"]
    event = cluster.get_event("short_release")

    inferred_features = resolve_feature_requirements(
        [event.conformance],
        {feature.code: feature.conformance for feature in cluster.features},
        set(),
    )

    assert inferred_features == {"MS", "MSR"}


def test_Endpoint_feature_registration() -> None:
    """Test if features are actually registered on an endpoint."""
    cluster = CLUSTERS_BY_NAME["Switch"]
    event = cluster.get_event("short_release")
    assert event is not None

    endpoint = Endpoint(
        1,
        {
            CONF_CLUSTERS: {},
            "generic_switch": {CONF_WITH_FEATURES: []},
        },
    )
    endpoint.register_usage(cluster, event)
    endpoint.resolve()

    assert endpoint._cluster_configs["Switch"].enabled_features == {
        "MomentarySwitch": True,
        "MomentarySwitchRelease": True,
    }

    # "simulate" an esphome var with MockObj.
    asyncio.run(endpoint.register(cg.MockObj("matter", "->")))

    # The Switch cluster is "flag driven" in esphome-matter: esp-matter evaluates every
    # feature in the feature_flags (needed for per-feature config such as MultiPressMax),
    # so all features, choice or not, end up in the feature_flags of the device config.
    assert "switch_cluster.feature_flags = " in CORE.cpp_main_section
    assert "feature::momentary_switch::get_id()" in CORE.cpp_main_section
    assert "feature::momentary_switch_release::get_id()" in CORE.cpp_main_section


# --- `described` / `condition` conformance -------------------------------------
import pytest

from components.matter.data_model.conformance import (
    Conformance,
    ConformanceDisposition,
    ConformanceError,
)


def _resolve(rule, features=None, context=""):
    return resolve_feature_requirements(
        [Conformance(rule)], {}, set(features or ()), context=context
    )


def test_described_rule_is_optional_with_no_requirements() -> None:
    assert _resolve({"described": True}) == set()
    c = Conformance({"described": True})
    assert c.disposition(set()) is ConformanceDisposition.OPTIONAL
    assert Conformance(
        {"otherwise": [{"provisional": True}, {"described": True}]}
    ).alternatives()


def test_matter_condition_is_true() -> None:
    rule = {"mandatory": {"term": {"and": [{"condition": "Matter"}, {"feature": "X"}]}}}
    assert _resolve(rule) == {"X"}
    c = Conformance({"mandatory": {"term": {"condition": "Matter"}}})
    assert c.disposition(set()) is ConformanceDisposition.MANDATORY


def test_zigbee_and_unknown_conditions_are_false() -> None:
    for name in ("Zigbee", "SomethingUnknown"):
        c = Conformance({"mandatory": {"term": {"condition": name}}})
        assert c.disposition(set()) is ConformanceDisposition.OPTIONAL
        assert c.alternatives() == ()
    negated = Conformance({"mandatory": {"term": {"not": {"condition": "Zigbee"}}}})
    assert len(negated.alternatives()) == 1


def test_resolver_errors_name_context() -> None:
    with pytest.raises(ConformanceError, match="Door Lock cluster on endpoint 3"):
        _resolve({"bogus": True}, context="Door Lock cluster on endpoint 3")


def test_valve_time_sync_resolves() -> None:
    c = CLUSTERS_BY_ID[0x0081]
    conf = {f.code: f.conformance for f in c.features}
    assert resolve_feature_requirements([], conf, {"TS"}) == set()


def test_optional_cluster_feature_error_is_actionable() -> None:
    import esphome.config_validation as cv

    endpoint = Endpoint(
        1,
        {CONF_CLUSTERS: {}, "room_air_conditioner": {CONF_WITH_FEATURES: ["Rocking"]}},
    )
    with pytest.raises(cv.Invalid) as exc:
        endpoint.resolve()
    message = str(exc.value)
    assert "required by: )" not in message and "required by: ." not in message
    assert "with_features" in message and "room_air_conditioner" in message
    assert "clusters:" in message


def test_satisfied_alternatives_do_not_need_a_choice() -> None:
    """Door Lock User (USR) is mandatory if PIN|RID|...; with several enabled nothing is added."""
    c = CLUSTERS_BY_ID[0x0101]
    conf = {f.code: f.conformance for f in c.features}
    assert resolve_feature_requirements([], conf, {"PIN", "RID", "USR"}) == set()
    assert resolve_feature_requirements([], conf, {"PIN", "USR", "COTA"}) == set()
