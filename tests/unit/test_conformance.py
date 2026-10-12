"""Endpoint composition conformance: parents, semantic tags, labels, counts."""

from __future__ import annotations

from conftest import CONFIGS, prepare_config, run_esphome
import pytest

from components.matter import conformance as c


def plugs(*ids):
    return {i: {"on_off_plug_in_unit": {}} for i in ids}


def test_sibling_duplicates_get_number_tags():
    plans = c.plan_endpoints(plugs(1, 2, 3), log=False)
    assert [plans[i].auto_tag.tag for i in (1, 2, 3)] == [1, 2, 3]
    assert all(
        plans[i].auto_tag.namespace == c.NAMESPACE_COMMON_NUMBER for i in (1, 2, 3)
    )


def test_lone_endpoint_gets_no_tag():
    plans = c.plan_endpoints(
        {1: {"on_off_plug_in_unit": {}}, 2: {"on_off_light": {}}}, log=False
    )
    assert plans[1].tags == [] and plans[2].tags == []


def test_utility_device_types_do_not_count():
    if "power_source" not in c.DEVICE_TYPES_BY_CONF_KEY:
        pytest.skip("power_source device type not available")
    eps = {1: {"power_source": {}}, 2: {"power_source": {}}}
    assert all(not p.tags for p in c.plan_endpoints(eps, log=False).values())


def test_different_parents_are_not_siblings():
    eps = {1: {"on_off_plug_in_unit": {}}, 2: {"on_off_plug_in_unit": {}, "parent": 1}}
    assert all(not p.tags for p in c.plan_endpoints(eps, log=False).values())


def test_label_is_copied_to_tag():
    eps = {
        1: {"on_off_plug_in_unit": {}, "label": "A"},
        2: {"on_off_plug_in_unit": {}},
    }
    plans = c.plan_endpoints(eps, log=False)
    assert plans[1].tags[0].label == "A"
    assert plans[2].tags[0].label is None


def test_explicit_tags_are_kept_and_used_numbers_skipped():
    eps = {
        1: {"on_off_plug_in_unit": {}, "tags": [{"namespace": 7, "tag": 1}]},
        2: {"on_off_plug_in_unit": {}},
    }
    plans = c.plan_endpoints(eps, log=False)
    assert plans[1].auto_tag is None
    assert plans[2].auto_tag.tag == 2


def test_identical_explicit_tags_rejected():
    tags = [{"namespace": 8, "tag": 0}]
    eps = {
        1: {"on_off_plug_in_unit": {}, "tags": tags},
        2: {"on_off_plug_in_unit": {}, "tags": tags},
    }
    with pytest.raises(Exception, match="identical"):
        c.plan_endpoints(eps, log=False)


def test_parent_validation():
    with pytest.raises(Exception, match="not a configured endpoint"):
        c.validate_composition({1: {"on_off_light": {}, "parent": 9}})
    with pytest.raises(Exception, match="cycle"):
        c.validate_composition(
            {1: {"on_off_light": {}, "parent": 2}, 2: {"on_off_light": {}, "parent": 1}}
        )


def test_hash_changes_with_structure():
    eps = plugs(1, 2)
    plans = c.plan_endpoints(eps, log=False)
    base = c.structure_hash(eps, plans, {1: "a", 2: "a"})
    assert base == c.structure_hash(eps, plans, {1: "a", 2: "a"})
    assert base != c.structure_hash(eps, plans, {1: "a", 2: "b"})


def test_max_device_types():
    assert c.max_device_types({}) == 1
    assert c.max_device_types({1: {"on_off_light": {}, "label": "x"}}) == 1


def test_multi_relay_config_generates(tmp_path):
    prepared = prepare_config(CONFIGS / "esp32-multi-relay.yaml", tmp_path)
    result = run_esphome("compile", "--only-generate", str(prepared), cwd=tmp_path)
    assert result.ok, result.output[-3000:]
    main = next(tmp_path.glob(".esphome/build/*/src/main.cpp")).read_text()
    assert "register_endpoint_tag(1, 7, 1" in main
    assert 'register_endpoint_label(1, "Relay 1")' in main
    assert "register_endpoint_parent(31, 30)" in main
    assert "set_structure_hash(" in main
    sdkconfig = next(
        tmp_path.glob(".esphome/build/*/sdkconfig.*.esphomeinternal")
    ).read_text()
    # 10 configured endpoints plus the root node endpoint.
    assert "CONFIG_ESP_MATTER_MAX_DYNAMIC_ENDPOINT_COUNT=11" in sdkconfig


def test_schema_rejects_bad_label(run_config):
    body = """
        matter:
          endpoints:
            1:
              label: "this label is far too long"
              on_off_light:
    """
    assert not run_config(body).ok


def test_schema_rejects_unknown_tag(run_config):
    body = """
        matter:
          endpoints:
            1:
              tags:
                - namespace: common_position
                  tag: nonsense
              on_off_light:
    """
    assert not run_config(body).ok


def test_default_label_truncates_on_utf8_boundary(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        out = c.default_label("Küche Deckenlampe links")
    assert len(out.encode()) <= c.LABEL_MAX
    assert out.startswith("Küche")
    assert "truncated" in caplog.text
    # multi-byte characters are never split
    assert c.default_label("é" * 20) == "é" * 8


def test_default_label_short_is_unchanged(caplog):
    assert c.default_label("Relay 1") == "Relay 1"
    assert "truncated" not in caplog.text


def test_explicit_label_over_limit_is_error():
    import esphome.config_validation as cv

    with pytest.raises(cv.Invalid, match="17 UTF-8 bytes"):
        c._label("x" * 17)
    assert c._label("x" * 16) == "x" * 16
