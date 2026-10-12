"""tools/conformance changes for the delegate layer (docs/delegates.md)."""

from __future__ import annotations

import os

import pytest

from tools.conformance import espm
from tools.conformance.espm import Profile
from tools.conformance.model import from_layout
from tools.conformance.spec import DEFAULT_SPEC_ROOT, Spec

needs_spec = pytest.mark.skipif(
    not DEFAULT_SPEC_ROOT.exists() and not os.environ.get("MATTER_DATA_MODEL"),
    reason="Matter data model XML not available",
)


def test_scanner_sees_base_cluster_calls():
    """The mode clusters call `mode_base::...::create_x`, RVC calls `operational_state::attribute::create_x`."""
    assert espm._ATTR_RE.search("        mode_base::attribute::create_supported_modes(cluster, NULL, 0, 0);")
    assert espm._ATTR_RE.search("    operational_state::attribute::create_phase_list(cluster, NULL, 0, 0);")
    assert espm._CMD_RE.search("    mode_base::command::create_change_to_mode(cluster);")
    match = espm._ATTR_RE.search("        global::attribute::create_feature_map(cluster, 0);")
    assert match and match.group(1) and match.group(2) == "feature_map"
    assert espm._EVENT_RE.search("    event::create_operational_error(cluster);")


def test_profile_knows_missing_mode_commands():
    """The SDK creates ChangeToMode for RVC Run Mode but not for Laundry Washer Mode: that is what the delegate adds."""
    profile = Profile()
    _, rvc_cmds, _ = profile.predict("RVC Run Mode", [])
    assert "changetomode" in rvc_cmds
    _, washer_cmds, _ = profile.predict("Laundry Washer Mode", [])
    assert "changetomode" not in washer_cmds
    rvc_attrs, _, _ = profile.predict("RVC Operational State", [])
    assert "phaselist" in {a.replace("_", "") for a in rvc_attrs}


@needs_spec
def test_rvc_operational_state_inherits_events():
    spec = Spec()
    events = {e.key for e in spec.clusters[0x61].events.values()}
    assert {"operationalerror", "operationcompletion"} <= events


@needs_spec
def test_delegate_layout_adds_commands():
    spec = Spec()
    layout = {
        "network": {"wifi": True, "thread": False, "ethernet": False},
        "endpoints": {
            "1": {
                "device_types": [{"name": "dishwasher", "id": 117}],
                "clusters": {
                    "OperationalState": {
                        "id": 0x60,
                        "source": "device_type",
                        "features": [],
                        "delegate": {
                            "attrs": [],
                            "cmds": ["Pause", "Resume", "OperationalCommandResponse"],
                            "events": ["OperationalError", "OperationCompletion"],
                        },
                    },
                },
                "parent": 0,
                "tags": [],
                "label": None,
            }
        },
    }
    model = from_layout(layout, spec)
    state = model.endpoints[1].servers[0x60]
    # Pause (0), Resume (3) and the response; Start/Stop were not configured
    assert {0, 3} <= set(state.accepted)
    assert not {1, 2} & set(state.accepted)
    assert state.generated, "OperationalCommandResponse"
    # the dishwasher device type is mapped to the esp-matter namespace `dish_washer`
    assert not any("not known to the esp-matter profile" in note for note in model.notes)
