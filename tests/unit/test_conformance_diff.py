"""Tests for ``python -m tools.conformance diff`` / ``gallery`` and the extra dump flavours.

Fixtures are real Home Assistant node dumps (see tests/fixtures/real_devices/README.md).
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from tools.conformance import diff as D
from tools.conformance.espm import Profile
from tools.conformance.inputs import classify
from tools.conformance.model import from_dump, from_layout, normalise_dump
from tools.conformance.spec import DEFAULT_SPEC_ROOT, Spec

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "real_devices"
LIGHT = FIXTURES / "mock_onoff_light.json"
FAN = FIXTURES / "mock_fan.json"
COVER = FIXTURES / "mock_window_covering_full.json"
CONTACT = FIXTURES / "eve_contact_sensor.json"
BUTTON = FIXTURES / "ikea_bilresa_dual_button.json"


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


def _load(path, spec):
    return from_dump(path, spec)


def _diff(a, b, spec, **kw):
    return D.diff_models(_load(a, spec), _load(b, spec), spec, **kw)


# ------------------------------------------------------------------ dump flavours
def to_harness(ha: dict, spec: Spec) -> dict:
    """Re-encode an HA dump the way tools/harness writes it (hex ids, named FeatureMap)."""
    data: dict = {}
    for path, value in ha["attributes"].items():
        ep, cid, aid = (int(x) for x in path.split("/"))
        if aid == 0xFFFC and isinstance(value, int):
            cspec = spec.clusters.get(cid)
            value = {
                f.name[0].lower() + f.name[1:].replace(" ", ""): bool(
                    value & (1 << f.bit)
                )
                for f in (cspec.features.values() if cspec else [])
            }
        data.setdefault(str(ep), {}).setdefault(f"0x{cid:04X}", {})[f"0x{aid:04X}"] = (
            value
        )
    return {"nodeId": 1, "data": data, "names": {"clusters": {}, "attributes": {}}}


class TestDumpFlavours:
    def test_ha_dump_is_recognised(self):
        assert classify(LIGHT) == "dump"
        assert classify(BUTTON) == "dump"

    def test_ha_dump_models(self, spec):
        model = _load(LIGHT, spec)
        assert sorted(model.endpoints) == [0, 1]
        assert model.endpoints[1].device_types == [(0x0100, 1)]
        assert 0x0006 in model.endpoints[1].servers

    def test_wrapped_dumps_normalise(self):
        flat = {"1/6/0": True}
        assert normalise_dump({"node_id": 1, "attributes": flat}) == {
            "1": {"6": {"0": True}}
        }
        assert normalise_dump({"result": {"attributes": flat}}) == {
            "1": {"6": {"0": True}}
        }
        assert normalise_dump(
            {"nodeId": 1, "data": {"1": {"0x0006": {"0x0000": 1}}}}
        ) == {"1": {"0x0006": {"0x0000": 1}}}

    def test_harness_dump_equals_ha_dump(self, spec, tmp_path):
        ha = json.loads(FAN.read_text())
        harness = tmp_path / "harness.json"
        harness.write_text(json.dumps(to_harness(ha, spec)))
        assert classify(harness) == "dump"
        res = D.diff_models(_load(harness, spec), _load(FAN, spec), spec)
        assert res.differences == [] and res.score == 1.0

    def test_tags_and_labels_parsed(self, spec):
        button = _load(BUTTON, spec)
        assert all(button.endpoints[i].tags for i in (1, 2))
        fan = _load(FAN, spec)
        assert any(ep.labels for ep in fan.endpoints.values())


# ------------------------------------------------------------------ diff
class TestDiff:
    def test_identical(self, spec):
        res = _diff(COVER, COVER, spec)
        assert res.score == 1.0 and not res.differences

    def test_matching_ignores_endpoint_numbers(self, spec, tmp_path):
        ha = json.loads(LIGHT.read_text())
        moved = {}
        for path, value in ha["attributes"].items():
            ep, rest = path.split("/", 1)
            moved[f"{7 if ep == '1' else ep}/{rest}"] = value
        moved["0/29/3"] = [7]  # PartsList of the root node names the endpoint
        ha["attributes"] = moved
        other = tmp_path / "renumbered.json"
        other.write_text(json.dumps(ha))
        res = D.diff_models(_load(other, spec), _load(LIGHT, spec), spec)
        assert res.score == 1.0, [d.as_dict() for d in res.differences]
        assert {(p["ours"], p["reference"]) for p in res.pairs} >= {(7, 1)}

    def test_different_devices_report_gaps(self, spec):
        res = _diff(LIGHT, FAN, spec, ignore_root=True)
        assert res.score < 0.8
        counts = res.gap_counts()
        assert counts["cluster"] or counts["endpoint"]
        assert res.gaps

    def test_feature_attribute_command_diff_decoded(self, spec, tmp_path):
        ha = json.loads(COVER.read_text())
        keys = [k for k in ha["attributes"] if k.endswith("/258/65532")]
        assert keys, "window covering feature map expected"
        base = keys[0].rsplit("/", 1)[0]
        ha["attributes"][base + "/65532"] &= ~1  # drop Lift (bit 0)
        ha["attributes"][base + "/65531"] = [
            a for a in ha["attributes"][base + "/65531"] if a != 8
        ]  # CurrentPositionLiftPercentage
        ha["attributes"][base + "/65529"] = [
            c for c in ha["attributes"][base + "/65529"] if c != 5
        ]  # GoToLiftPercentage
        mod = tmp_path / "cover.json"
        mod.write_text(json.dumps(ha))
        res = D.diff_models(
            _load(mod, spec), _load(COVER, spec), spec, ignore_root=True
        )
        by_kind = {d.kind: d for d in res.differences}
        assert by_kind["feature"].change == "missing"
        assert "Lift" in by_kind["feature"].item
        assert by_kind["attribute"].change == "missing"
        assert "CurrentPositionLiftPercentage" in by_kind["attribute"].item
        assert by_kind["accepted"].change == "missing"

    def test_tag_differences(self, spec, tmp_path):
        ha = json.loads(BUTTON.read_text())
        ha["attributes"]["2/29/4"] = ha["attributes"]["1/29/4"]  # same tag on both
        mod = tmp_path / "button.json"
        mod.write_text(json.dumps(ha))
        res = D.diff_models(
            _load(mod, spec), _load(BUTTON, spec), spec, ignore_root=True
        )
        assert any(d.kind == "tag" for d in res.differences)

    def test_ignore_root_and_noise(self, spec):
        full = _diff(CONTACT, LIGHT, spec)
        no_root = _diff(CONTACT, LIGHT, spec, ignore_root=True)
        assert all(d.endpoint != "root" for d in no_root.differences)
        assert any(d.endpoint == "root" for d in full.differences)
        noisy = _diff(CONTACT, LIGHT, spec, ignore_root=True, ignore_noise=True)
        assert noisy.ignored and len(noisy.differences) <= len(no_root.differences)

    def test_json_and_text(self, spec):
        res = _diff(LIGHT, FAN, spec)
        data = json.loads(json.dumps(res.as_dict()))
        assert data["summary"]["total_gaps"] == len(res.gaps)
        assert "match score" in D.render_diff(res)


# ------------------------------------------------------------------ layout side + catalog
LAYOUT = {
    "format": 1,
    "network": {"wifi": True, "thread": False, "ethernet": False},
    "endpoints": {
        "1": {
            "device_types": [{"name": "on_off_light", "id": 256}],
            "clusters": {},
            "dynamic_features": [],
            "parent": 0,
            "label": None,
            "tags": [],
        }
    },
}


@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    cat = tmp_path / "catalog"
    cat.mkdir()
    entries = [
        {"id": "test-light", "local_dump": str(LIGHT), "endpoints": []},
        {
            "id": "test-summary",
            "endpoints": [
                {
                    "id": 1,
                    "device_types": [["0x0100", "on_off_light", 2]],
                    "parent": 0,
                    "server_clusters": [["0x0006", "OnOff", {"features": ["LT"]}]],
                    "client_clusters": [],
                    "tags": ["free text"],
                }
            ],
        },
    ]
    (cat / "x.jsonl").write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    monkeypatch.setenv(D.CATALOG_ENV, str(cat))
    return cat


class TestLayoutAndCatalog:
    def test_layout_vs_dump_by_catalog_id(self, spec, profile, catalog, tmp_path):
        layout = tmp_path / "layout.json"
        layout.write_text(json.dumps(LAYOUT))
        ours = from_layout(layout, spec, profile)
        ref, label, kind = D.resolve_reference("test-light", spec, profile)
        assert kind == "catalog-dump" and "test-light" in label
        res = D.diff_models(ours, ref, spec, ignore_root=True, ignore_noise=True)
        assert 0.3 < res.score <= 1.0
        assert res.pairs[0]["ours"] == 1 and res.pairs[0]["reference"] == 1

    def test_layout_tags_and_label_reach_the_model(self, spec, profile):
        layout = copy.deepcopy(LAYOUT)
        layout["endpoints"]["1"]["tags"] = [{"namespace": 7, "tag": 1, "label": None}]
        layout["endpoints"]["1"]["label"] = "Lamp"
        ep = from_layout(layout, spec, profile).endpoints[1]
        assert ep.tags == [(7, 1, None)] and ep.labels == [("name", "Lamp")]

    def test_catalog_summary_reference(self, spec, profile, catalog):
        ref, label, kind = D.resolve_reference("test-summary", spec, profile)
        assert kind == "catalog" and "summary" in label
        ours = from_layout(LAYOUT, spec, profile)
        res = D.diff_models(ours, ref, spec, ignore_root=True, reference_label=label)
        assert res.pairs and res.pairs[0]["reference"] == 1

    def test_unknown_catalog_id(self, spec, profile, catalog):
        with pytest.raises(FileNotFoundError, match="test-light"):
            D.resolve_reference("test-ligh", spec, profile)

    def test_cli(self, spec, catalog, tmp_path, capsys):
        layout = tmp_path / "layout.json"
        layout.write_text(json.dumps(LAYOUT))
        out = tmp_path / "out.json"
        from tools.conformance.__main__ import main

        argv = ["diff", str(layout), "test-light", "--ignore-root", "--json", str(out)]
        assert main(argv) == 0
        assert "match score" in capsys.readouterr().out
        assert json.loads(out.read_text())["endpoint_pairs"]
        assert main(["diff", str(layout), "no-such-id"]) == 2


# ------------------------------------------------------------------ gallery
class TestGallery:
    def test_gallery_matrix(self, spec, profile, catalog, tmp_path):
        gal = tmp_path / "gallery"
        (gal / "sub").mkdir(parents=True)
        (gal / "sub" / "test-light-expose.yaml").write_text(
            "# mirrors: test-light\nmatter:\n"
        )
        (gal / "test-light.yaml").write_text("# comment\n# mirrors: test-light\n")
        (gal / "other.yaml").write_text("# no header\n")
        cache = tmp_path / "cache"
        cache.mkdir()
        for stem in ("test-light-expose", "test-light"):
            (cache / f"{stem}.layout.json").write_text(json.dumps(LAYOUT))
        rows = D.gallery(
            gal, spec, profile, cache=cache, ignore_root=True, ignore_noise=True
        )
        assert [(r["device"], r["variant"]) for r in rows] == [
            ("test-light", "base"),
            ("test-light", "expose"),
        ]
        assert all("error" not in r and 0 < r["score"] <= 1 for r in rows)
        text = D.render_matrix(rows)
        assert "test-light" in text and "score" in text
        assert D.render_matrix(rows, markdown=True).startswith("| device")

    def test_gallery_reports_broken_entries(self, spec, profile, catalog, tmp_path):
        gal = tmp_path / "g"
        gal.mkdir()
        (gal / "x.yaml").write_text("# mirrors: not-in-catalog\n")
        cache = tmp_path / "c"
        cache.mkdir()
        (cache / "x.layout.json").write_text(json.dumps(LAYOUT))
        rows = D.gallery(gal, spec, profile, cache=cache)
        assert "not-in-catalog" in rows[0]["error"]

    def test_find_mirrors(self, tmp_path):
        f = tmp_path / "a.yaml"
        f.write_text("# Mirrors: a-b, c-d\nmatter:\n")
        assert D.find_mirrors(f) == ["a-b", "c-d"]
