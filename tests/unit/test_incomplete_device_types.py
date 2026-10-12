"""Device types without a working delegate: chime is refused unless allowed, camera / evse warn (incomplete.py)."""

from __future__ import annotations

from tools.conformance.espm import GUARDED_AWAY, Profile


def test_chime_is_refused_without_opt_in(run_config):
    result = run_config("matter:\n  endpoints:\n    1:\n      chime: {}\n")
    assert not result.ok
    assert "allow_incomplete_device_types" in result.output
    assert "Chime delegate" in result.output


def test_chime_builds_with_opt_in_and_warns(run_config):
    result = run_config(
        "matter:\n  allow_incomplete_device_types: [chime]\n  endpoints:\n    1:\n      chime: {}\n"
    )
    assert result.ok, result.output[-2000:]
    assert "chime device type is incomplete" in result.output


def test_camera_and_evse_only_warn(run_config):
    result = run_config(
        "matter:\n  endpoints:\n    1:\n      camera: {with_features: [Video]}\n    2:\n      evse: {}\n"
    )
    assert result.ok, result.output[-2000:]
    assert "camera device type is incomplete" in result.output
    assert "evse device type is incomplete" in result.output


def test_unknown_opt_in_is_rejected(run_config):
    result = run_config("matter:\n  allow_incomplete_device_types: [lamp]\n")
    assert not result.ok


# --- the checker models what esp-matter does for these cases --------------------------------


def test_action_switch_implies_multi_press_without_ongoing_event():
    profile = Profile()
    assert "momentary_switch_multi_press" in profile.implied_features(
        "Switch", ["ActionSwitch"]
    )
    _attrs, _cmds, events = profile.predict("Switch", ["ActionSwitch"])
    assert "multipresscomplete" in events
    assert "multipressongoing" not in events
    _attrs, _cmds, events = profile.predict(
        "Switch",
        ["MomentarySwitch", "MomentarySwitchRelease", "MomentarySwitchMultiPress"],
    )
    assert "multipressongoing" in events


def test_require_pin_needs_both_cota_and_pin():
    profile = Profile()
    cota = ["CredentialOverTheAirAccess"]
    attrs, _cmds, _events = profile.predict("Door Lock", cota)
    assert "requirepinforremoteoperation" not in attrs
    attrs, _cmds, _events = profile.predict("Door Lock", [*cota, "PINCredential"])
    assert "requirepinforremoteoperation" in attrs


def test_guard_table_names_known_clusters():
    profile = Profile()
    for namespace, present, absent, _gone in GUARDED_AWAY:
        cluster = profile.data["clusters"][namespace]
        for feature in (*present, *absent):
            assert feature in cluster["features"], (namespace, feature)


def test_evse_implies_charging_preferences():
    assert "charging_preferences" in Profile().implied_features("Energy EVSE", [])


def test_ota_requestor_device_type_needs_matter_ota(run_config):
    result = run_config("matter:\n  endpoints:\n    1:\n      ota_requestor: {}\n")
    assert not result.ok
    assert "matter: ota: true" in result.output


def test_ota_requestor_device_type_with_matter_ota(run_config):
    result = run_config(
        "matter:\n  software_version: 2\n  ota: true\n  endpoints:\n    1:\n      ota_requestor: {}\n"
    )
    assert result.ok, result.output[-2000:]
