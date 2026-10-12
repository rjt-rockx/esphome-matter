"""Delegate layer: mode clusters, Mode Select, Operational State, RVC Operational State (docs/delegates.md)."""

from __future__ import annotations

import json
import textwrap

from conftest import COMPONENTS, CONFIGS, DATA_MODEL_DIR, prepare_config, run_esphome
import pytest

DATA = json.loads((DATA_MODEL_DIR / "delegate_data.json").read_text())

SELECTS = """
select:
  - platform: template
    id: program
    optimistic: true
    options: [Normal, Delicate, Heavy]
  - platform: template
    id: wash_state
    optimistic: true
    options: [stopped, running, paused]
"""


def washer(clusters: str, extra: str = SELECTS) -> str:
    """A laundry washer endpoint with the given `clusters:` body, plus extra top level YAML."""
    return (
        "matter:\n  endpoints:\n    1:\n      laundry_washer: {}\n      clusters:\n"
        + textwrap.indent(textwrap.dedent(clusters), " " * 8)
        + "\n"
        + textwrap.dedent(extra)
    )


WASHER_MODES = """\
laundry_washer_mode:
  modes:
    - {label: Normal, tags: [normal]}
    - {label: Delicate, tags: [delicate]}
"""


# --------------------------------------------------------------------------
# Generated data
# --------------------------------------------------------------------------


def test_delegate_data_matches_cluster_table():
    from esphome.core import CORE

    CORE.name = "unit"
    from components.matter.data_model.clusters import CLUSTERS_BY_NAME
    from components.matter import delegates

    for name, entry in DATA["mode_clusters"].items():
        cluster = CLUSTERS_BY_NAME.get(name)
        if cluster is not None:
            assert cluster.id == entry["id"], name
    for name, entry in DATA["operational_state_clusters"].items():
        cluster = CLUSTERS_BY_NAME.get(name)
        if cluster is not None:
            assert cluster.id == entry["id"], name
    # Every delegate cluster of the schema exists in the cluster table.
    for name in delegates.MODE_CLUSTERS:
        assert name in CLUSTERS_BY_NAME or name in (
            "DishwasherMode",
            "OvenMode",
            "RefrigeratorAndTemperatureControlledCabinetMode",
        ), name


def test_cpp_cluster_ids_match_data():
    """matter_modes.cpp hard-codes the cluster ids of its init-callback table."""
    text = (COMPONENTS / "matter" / "matter_modes.cpp").read_text()
    for name in (
        "LaundryWasherMode",
        "RVCRunMode",
        "RVCCleanMode",
        "MicrowaveOvenMode",
        "EnergyEVSEMode",
        "WaterHeaterMode",
        "DeviceEnergyManagementMode",
    ):
        cluster_id = DATA["mode_clusters"][name]["id"]
        assert f"{{0x{cluster_id:04X}," in text, name
    ops = (COMPONENTS / "matter" / "matter_operational_state.cpp").read_text()
    assert "OPERATIONAL_STATE_ID = 0x0060" in ops
    assert "RVC_OPERATIONAL_STATE_ID = 0x0061" in ops


def test_required_tags_exist():
    from esphome.core import CORE

    CORE.name = "unit"
    from components.matter import delegates

    for cluster, groups in delegates.REQUIRED_TAG_GROUPS.items():
        for group in groups:
            for tag in group:
                assert tag in DATA["mode_clusters"][cluster]["tags"], (cluster, tag)
    for cluster, groups in delegates.EXCLUSIVE_TAG_GROUPS.items():
        for group in groups:
            for tag in group:
                assert tag in DATA["mode_clusters"][cluster]["tags"], (cluster, tag)


def test_manufacturer_range_helpers():
    from esphome.core import CORE

    CORE.name = "unit"
    import esphome.config_validation as cv
    from components.matter import delegates

    validate = delegates._tag_validator("LaundryWasherMode")
    assert validate("normal")["value"] == 0x4000
    assert validate("Low Energy")["value"] == 0x0004
    assert validate(0x8001)["named"] is False
    assert validate({"mfg_code": 0xFFF1, "value": 7})["mfg_code"] == 0xFFF1
    with pytest.raises(cv.Invalid):
        validate("not_a_tag")
    with pytest.raises(cv.Invalid):
        validate(0x1234)  # neither a known tag nor in 0x8000-0xBFFF


def test_yaml_off_tag_is_not_a_bool():
    """YAML 1.1 reads an unquoted `off` as False; the water heater mode needs the `off` tag."""
    from esphome.core import CORE

    CORE.name = "unit"
    from components.matter import delegates

    assert delegates._tag_validator("WaterHeaterMode")(False)["value"] == 0x4000


# --------------------------------------------------------------------------
# Config validation: mode clusters
# --------------------------------------------------------------------------


def test_shipped_delegates_config_validates(tmp_path):
    prepared = prepare_config(CONFIGS / "esp32-delegates.yaml", tmp_path)
    result = run_esphome("config", str(prepared), cwd=tmp_path)
    assert result.ok, result.output[-3000:]


def test_minimal_mode_cluster_ok(run_config):
    result = run_config(washer(WASHER_MODES))
    assert result.ok, result.output[-2000:]


@pytest.mark.parametrize(
    ("clusters", "message"),
    [
        (
            """\
            laundry_washer_mode:
              modes:
                - {label: Normal, tags: [normal]}
            """,
            "at least 2",
        ),
        (
            """\
            laundry_washer_mode:
              modes:
                - {label: Normal, tags: [normal]}
                - {label: Normal, tags: [delicate]}
            """,
            "labels must be unique",
        ),
        (
            """\
            laundry_washer_mode:
              modes:
                - {label: A, mode: 1, tags: [normal]}
                - {label: B, mode: 1, tags: [delicate]}
            """,
            "Mode numbers must be unique",
        ),
        (
            """\
            laundry_washer_mode:
              modes:
                - {label: A, tags: [delicate]}
                - {label: B, tags: [heavy]}
            """,
            "requires LaundryWasherMode to have a mode with the 'normal' tag",
        ),
        (
            """\
            laundry_washer_mode:
              modes:
                - {label: A, tags: [normal, sparkly]}
                - {label: B, tags: [heavy]}
            """,
            "Unknown LaundryWasherMode tag",
        ),
        (
            """\
            laundry_washer_mode:
              modes:
                - {label: A, tags: [0x1234]}
                - {label: B, tags: [normal]}
            """,
            "manufacturer range",
        ),
        (
            """\
            laundry_washer_mode:
              modes:
                - {label: A, tags: [{mfg_code: 0xFFF1, value: 1}]}
                - {label: B, tags: [normal]}
            """,
            "at least one LaundryWasherMode tag",
        ),
        (
            """\
            laundry_washer_mode:
              modes:
                - {label: A, tags: []}
                - {label: B, tags: [normal]}
            """,
            "length of value must be at least 1",
        ),
        (
            """\
            laundry_washer_mode:
              modes:
                - {label: A, tags: [normal, normal]}
                - {label: B, tags: [heavy]}
            """,
            "lists a tag twice",
        ),
        (
            """\
            laundry_washer_mode:
              modes:
                - {label: "01234567890123456789012345678901234567890123456789012345678901234", tags: [normal]}
                - {label: B, tags: [heavy]}
            """,
            "maximum is 64",
        ),
        (
            """\
            laundry_washer_mode: {}
            """,
            "'modes' is a required option",
        ),
    ],
)
def test_mode_cluster_errors(run_config, clusters, message):
    result = run_config(washer(clusters))
    assert not result.ok
    assert message in result.output, result.output[-2500:]


def test_rvc_run_mode_tag_rules(run_config):
    base = """
        matter:
          endpoints:
            1:
              robotic_vacuum_cleaner: {{}}
              clusters:
                rvc_run_mode:
                  modes:
                    - {{label: Idle, tags: [{idle}]}}
                    - {{label: Clean, tags: [{clean}]}}
    """
    ok = run_config(base.format(idle="idle", clean="cleaning"))
    assert ok.ok, ok.output[-2000:]
    both = run_config(base.format(idle="idle, cleaning", clean="cleaning"))
    assert not both.ok and "at most one of idle, cleaning, mapping" in both.output
    missing = run_config(base.format(idle="idle", clean="mapping"))
    assert not missing.ok and "'cleaning' tag" in missing.output


def test_select_options_must_match_modes(run_config):
    clusters = """\
    laundry_washer_mode:
      modes:
        - {label: Normal, tags: [normal]}
        - {label: Whites, tags: [heavy]}
      select: program
    """
    result = run_config(washer(clusters))
    assert not result.ok
    assert "has no option ['Whites']" in result.output, result.output[-2500:]
    fixed = """\
    laundry_washer_mode:
      modes:
        - {label: Normal, tags: [normal]}
        - {label: Whites, option: Heavy, tags: [heavy]}
      select: program
    """
    assert run_config(washer(fixed)).ok


def test_select_must_be_a_select(run_config):
    clusters = """\
    laundry_washer_mode:
      modes:
        - {label: Normal, tags: [normal]}
        - {label: Delicate, tags: [delicate]}
      select: sw
    """
    extra = "\nswitch:\n  - platform: template\n    id: sw\n    optimistic: true\n"
    result = run_config(washer(clusters, extra))
    assert not result.ok


def test_mode_select_requires_description(run_config):
    body = """
        matter:
          endpoints:
            1:
              mode_select: {}
              clusters:
                mode_select:
                  modes:
                    - {label: Low}
                    - {label: High}
    """
    result = run_config(body)
    assert not result.ok and "'description' is a required option" in result.output
    ok = run_config(body.replace("modes:", "description: Speed\n                  modes:"))
    assert ok.ok, ok.output[-2000:]


def test_mode_select_tags_need_manufacturer_code(run_config):
    body = """
        matter:
          endpoints:
            1:
              mode_select: {}
              clusters:
                mode_select:
                  description: Speed
                  modes:
                    - {label: Low, tags: [normal]}
                    - {label: High}
    """
    result = run_config(body)
    assert not result.ok and "need a manufacturer code" in result.output


def test_delegate_cluster_without_config_warns(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              robotic_vacuum_cleaner: {}
        """
    )
    assert result.ok, result.output[-2000:]
    assert "needs a" in result.output and "delegate to work" in result.output


# --------------------------------------------------------------------------
# Config validation: operational state
# --------------------------------------------------------------------------


def washer_state(opstate: str, extra: str = SELECTS) -> str:
    return washer(WASHER_MODES + "operational_state:\n" + textwrap.indent(textwrap.dedent(opstate), "  "), extra)


def test_operational_state_ok(run_config):
    result = run_config(
        washer_state(
            """\
            phases: [Wash, Rinse]
            state: wash_state
            on_start:
              - logger.log: start
            on_pause:
              - logger.log: pause
            on_resume:
              - logger.log: resume
            """
        )
    )
    assert result.ok, result.output[-2500:]


@pytest.mark.parametrize(
    ("opstate", "message"),
    [
        ("on_pause:\n  - logger.log: x\n", "on_pause and on_resume go together"),
        ("on_go_home:\n  - logger.log: x\n", "on_go_home exists only on rvc_operational_state"),
        ("phase: wash_state\n", "phase needs a list of phases"),
        ("phases: [A, a]\n", "Phase names must be unique"),
        ("states: [warp_speed]\n", "Unknown OperationalState state"),
        ("states: [{id: 5, label: x}]\n", "value must be at least 128"),
        ("state: wash_state\nstate_map: {x: nonsense}\n", "not a state of this cluster"),
    ],
)
def test_operational_state_errors(run_config, opstate, message):
    result = run_config(washer_state(opstate))
    assert not result.ok
    assert message in result.output, result.output[-2500:]


def test_operational_state_select_options_checked(run_config):
    extra = "\nselect:\n  - platform: template\n    id: bad_state\n    optimistic: true\n    options: [stopped, dancing]\n"
    result = run_config(washer_state("state: bad_state\n", extra))
    assert not result.ok
    assert "not operational states" in result.output
    mapped = run_config(washer_state("state: bad_state\nstate_map: {dancing: running}\n", extra))
    assert mapped.ok, mapped.output[-2500:]


def test_rvc_has_no_start_stop(run_config):
    result = run_config(
        """
        matter:
          endpoints:
            1:
              robotic_vacuum_cleaner: {}
              clusters:
                rvc_operational_state:
                  on_start:
                    - logger.log: x
        """
    )
    assert not result.ok and "does not exist on rvc_operational_state" in result.output


def test_operational_state_without_source_warns(run_config):
    result = run_config(washer_state("phases: [A]\n"))
    assert result.ok
    assert "operational state will stay Stopped" in result.output or "stay Stopped" in result.output


def test_operational_actions(run_config):
    result = run_config(
        washer_state(
            "state: wash_state\n"
            "decide_in_automation: true\n"
            "on_start:\n  - matter.operational_state.reject:\n      error: unable_to_start_or_resume\n      label: Door open\n"
        )
        + "\ninterval:\n  - interval: 1h\n    then:\n"
        "      - matter.operational_state.set: {endpoint: 1, state: running}\n"
        "      - matter.operational_state.error: {endpoint: 1, error: unable_to_complete_operation}\n"
        "      - matter.operational_state.completed: {endpoint: 1, total_operational_time: 60}\n"
        "      - matter.mode.set: {endpoint: 1, cluster: laundry_washer_mode, mode: 1}\n"
    )
    assert result.ok, result.output[-2500:]


def test_reject_status_validation(run_config):
    template = """\
    laundry_washer_mode:
      decide_in_automation: true
      on_change_to_mode:
        - matter.mode.reject:
            status: STATUS
      modes:
        - {label: Normal, tags: [normal]}
        - {label: Delicate, tags: [delicate]}
    """
    assert run_config(washer(template.replace("STATUS", "invalid_in_mode"))).ok
    bad = run_config(washer(template.replace("STATUS", "nonsense")))
    assert not bad.ok and "Unknown status" in bad.output
    # numbers are only for manufacturer specific statuses
    number = run_config(washer(template.replace("STATUS", "0x43")))
    assert not number.ok and "outside the manufacturer range" in number.output
    assert run_config(washer(template.replace("STATUS", "0x80"))).ok


# --------------------------------------------------------------------------
# Code generation
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def generated_main(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("delegates")
    prepared = prepare_config(CONFIGS / "esp32-delegates.yaml", tmp_path)
    result = run_esphome("compile", "--only-generate", str(prepared), cwd=tmp_path)
    assert result.ok, result.output[-3000:]
    mains = list(tmp_path.glob(".esphome/build/*/src/main.cpp"))
    assert len(mains) == 1
    build = mains[0].parent
    return mains[0].read_text(), (build / "esphome" / "core" / "defines.h").read_text()


@pytest.mark.slow
def test_codegen_registers_delegates(generated_main):
    code, defines = generated_main
    assert "#define USE_MATTER_MODES" in defines
    assert "#define USE_MATTER_OPERATIONAL_STATE" in defines
    # RVC endpoint: run + clean mode + RVC operational state, attached after the clusters exist
    assert "esphome::matter::attach_delegate(endpoint, 0x0054)" in code
    assert "esphome::matter::attach_delegate(endpoint, 0x0055)" in code
    assert "esphome::matter::attach_delegate(endpoint, 0x0061)" in code
    # washer endpoint
    assert "esphome::matter::attach_delegate(endpoint, 0x0051)" in code
    assert "esphome::matter::attach_delegate(endpoint, 0x0060)" in code
    # mode select (device type) + description
    assert "esphome::matter::attach_delegate(endpoint, 0x0050)" in code
    assert 'strncpy(device_config_0.mode_select.description, "Fan speed preset"' in code
    assert "device_config_0.mode_select.current_mode = 0;" in code
    assert "esphome::matter::attach_delegate(endpoint, 0x009E)" in code


@pytest.mark.slow
def test_codegen_modes_tags_and_numbers(generated_main):
    code, _ = generated_main
    assert 'add_mode("Idle", 0, "Idle");' in code
    assert 'add_mode("Vacuum then mop", 5, "Vacuum then mop");' in code
    assert 'add_mode("Heavy", 10, "Heavy");' in code
    # Mop has the Mop (0x4002) and Quiet (2) tags
    assert "add_tag(16386, 0, false);\n  matter_mattermodemapping_id_2->add_tag(2, 0, false);" in code
    # Manufacturer specific semantic tag of the Mode Select cluster
    assert "add_tag(1, 65521, true);" in code
    assert "set_select(vacuum_run_mode)" in code
    assert "add_change_trigger(" in code


@pytest.mark.slow
def test_codegen_operational_state(generated_main):
    code, _ = generated_main
    assert "MatterOperationalStateMapping(1, 97)" in code
    assert "MatterOperationalStateMapping(2, 96)" in code
    assert "add_state(128, \"Soaking\");" in code  # manufacturer specific state
    assert "add_state_name(\"Washing\", 1);" in code  # state_map alias
    assert "add_phase(\"Pre-wash\");" in code
    assert "set_state_select(washer_state)" in code
    assert "set_phase_text_sensor(vacuum_phase)" in code
    assert "set_countdown_sensor(washer_remaining)" in code
    assert "MatterOperationalCommand::GO_HOME" in code
    assert "MatterOperationalCommand::START" in code
    assert "MatterOperationalCommand::STOP" in code
    # actions
    assert "MatterOperationalStateRejectAction" in code
    assert "MatterModeRejectAction<uint8_t>" in code
    assert "set_status([](uint8_t mode) -> uint8_t {\n      return 67;" in code  # dust_bin_full
    # the blocking mode is opt-in per cluster, with its timeout in ms
    assert "mattermodemapping_id->set_decision(true, 250);" in code
    assert "mattermodemapping_id_2->set_decision(false, 250);" in code
    assert "mattermodemapping_id_3->set_accept_when_idle(true);" in code
    assert "matteroperationalstatemapping_id->set_decision(true, 300);" in code
    assert "add_allowed_state(matter::MatterOperationalCommand::START, 0);" in code
    assert "add_idle_state(2);" in code  # paused of the washer


# --------------------------------------------------------------------------
# Laundry Washer Controls, Dishwasher Mode, Refrigerator mode
# --------------------------------------------------------------------------

CONTROLS_SELECTS = """
select:
  - platform: template
    id: spin
    optimistic: true
    options: ["Off", "400", "800"]
  - platform: template
    id: rinse
    optimistic: true
    options: [none, normal, extra]
"""


def controls(body: str) -> str:
    return washer(
        WASHER_MODES + "laundry_washer_controls:\n" + textwrap.indent(textwrap.dedent(body), "  "),
        CONTROLS_SELECTS,
    )


def test_laundry_controls_ok(run_config):
    result = run_config(
        controls(
            """\
            spin_speeds: ["Off", "400", "800"]
            spin_speed: spin
            rinses: [none, normal, extra]
            number_of_rinses: rinse
            """
        )
    )
    assert result.ok, result.output[-2500:]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("with_features: [spin]\n", "The spin feature needs `spin_speeds:`"),
        ("spin_speed: spin\nrinses: [none]\n", "spin_speed needs spin_speeds"),
        ("spin_speeds: [a, a]\n", "spin_speeds must be unique"),
        ("rinses: [none, turbo]\n", "turbo"),
        (
            "spin_speeds: ['Off', '400']\nspin_speed: spin\n",
            "has options ['800'] that are not in `spin_speeds`",
        ),
        (
            "spin_speeds: ['Off']\nrinses: [none, normal]\nnumber_of_rinses: rinse\n",
            "has options ['extra'] that are not in `rinses`",
        ),
    ],
)
def test_laundry_controls_errors(run_config, body, message):
    result = run_config(controls(body))
    assert not result.ok
    assert message in result.output, result.output[-2500:]


@pytest.mark.slow
def test_codegen_laundry_controls(generated_main):
    code, defines = generated_main
    assert "#define USE_MATTER_LAUNDRY_CONTROLS" in defines
    assert 'add_spin_speed("400");' in code
    assert 'add_rinse(1, "normal");' in code
    assert "set_spin_speed_select(washer_spin)" in code
    assert "set_rinses_select(washer_rinses)" in code
    # NumberOfRinses must start with a supported value; both features are enabled by the lists
    assert "laundry_washer_controls_config.features.rinse.number_of_rinses = 0;" in code
    assert "feature::spin::get_id() | esp_matter::cluster::laundry_washer_controls::feature::rinse::get_id()" in code
    assert "esphome::matter::attach_delegate(endpoint, 0x0053)" in code


@pytest.mark.slow
def test_codegen_dishwasher_and_fridge_modes(generated_main):
    code, _ = generated_main
    assert "esphome::matter::attach_delegate(endpoint, 0x0059)" in code  # Dishwasher Mode
    assert "esphome::matter::attach_delegate(endpoint, 0x0052)" in code  # Refrigerator mode
    assert "esp_matter::cluster::dish_washer_mode::create(" in code
    assert "esp_matter::cluster::refrigerator_and_tcc_mode::create(" in code


# --------------------------------------------------------------------------
# Conformance checker sees the delegate commands
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_conformance_checker_clean(tmp_path):
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "tools.conformance", str(CONFIGS / "esp32-delegates.yaml")],
        capture_output=True,
        text=True,
        cwd=COMPONENTS.parent,
        timeout=600,
    )
    assert "0 error(s)" in result.stdout, result.stdout[-3000:] + result.stderr[-1000:]


def test_conformance_checker_flags_missing_delegate():
    """A mode cluster created without the delegate layer has no ChangeToMode; with it the checker is satisfied."""
    from tools.conformance.checks import check_node
    from tools.conformance.model import from_layout
    from tools.conformance.spec import DEFAULT_SPEC_ROOT, Spec

    if not DEFAULT_SPEC_ROOT.exists():
        pytest.skip("Matter data model XML not available")
    spec = Spec()

    def layout(delegate):
        cluster = {"id": 0x59, "source": "component", "features": []}
        if delegate:
            cluster["delegate"] = {
                "attrs": ["SupportedModes", "CurrentMode"],
                "cmds": ["ChangeToMode", "ChangeToModeResponse"],
                "events": [],
            }
        return {
            "network": {"wifi": True, "thread": False, "ethernet": False},
            "endpoints": {
                "1": {
                    "device_types": [{"name": "dishwasher", "id": 117}],
                    "clusters": {"DishwasherMode": cluster},
                    "parent": 0,
                    "tags": [],
                    "label": None,
                }
            },
        }

    without = check_node(from_layout(layout(False), spec), spec)
    messages = [f.message for f in without.findings if f.code.startswith("CL-")]
    assert any("ChangeToMode" in m for m in messages), messages
    with_delegate = check_node(from_layout(layout(True), spec), spec)
    assert not [f for f in with_delegate.findings if f.code.startswith("CL-")], with_delegate.findings


# --------------------------------------------------------------------------
# Command decision modes (async default, opt-in blocking)
# --------------------------------------------------------------------------


def test_reject_needs_decide_in_automation(run_config):
    clusters = """\
    laundry_washer_mode:
      on_change_to_mode:
        - matter.mode.reject: invalid_in_mode
      modes:
        - {label: Normal, tags: [normal]}
        - {label: Delicate, tags: [delicate]}
    """
    result = run_config(washer(clusters))
    assert not result.ok and "only works with `decide_in_automation: true`" in result.output
    assert run_config(washer(clusters.replace("      modes:", "      decide_in_automation: true\n      modes:", 1))).ok


def test_reject_in_operational_state_needs_decide(run_config):
    result = run_config(
        washer_state(
            "state: wash_state\non_start:\n  - matter.operational_state.reject: unable_to_start_or_resume\n"
        )
    )
    assert not result.ok and "only works with `decide_in_automation: true`" in result.output


def test_accept_when_idle_needs_operational_state(run_config):
    clusters = """\
    laundry_washer_mode:
      accept: when_idle
      modes:
        - {label: Normal, tags: [normal]}
        - {label: Delicate, tags: [delicate]}
    """
    result = run_config(washer(clusters))
    assert not result.ok and "operational_state or rvc_operational_state" in result.output
    with_state = textwrap.dedent(clusters) + "operational_state:\n  state: wash_state\n"
    assert run_config(washer(with_state)).ok


def test_decide_timeout_is_configurable(run_config):
    clusters = """\
    laundry_washer_mode:
      decide_in_automation: true
      decide_timeout: 5s
      modes:
        - {label: Normal, tags: [normal]}
        - {label: Delicate, tags: [delicate]}
    """
    assert run_config(washer(clusters)).ok
    bad = run_config(washer(clusters.replace("5s", "soon")))
    assert not bad.ok


@pytest.mark.parametrize(
    ("opstate", "message"),
    [
        ("allowed_states: {go_home: [stopped]}\n", "go_home exists only on rvc_operational_state"),
        ("allowed_states: {start: [warp]}\n", "'warp' is not a state of this cluster"),
        ("idle_states: [warp]\n", "idle_states: 'warp'"),
    ],
)
def test_allowed_and_idle_states_errors(run_config, opstate, message):
    result = run_config(washer_state(opstate))
    assert not result.ok and message in result.output, result.output[-1500:]


def test_allowed_states_ok(run_config):
    result = run_config(
        washer_state(
            "state: wash_state\nallowed_states: {start: [stopped], pause: [running]}\nidle_states: [stopped, paused]\n"
            "on_start:\n  - logger.log: x\n"
        )
    )
    assert result.ok, result.output[-2000:]


def test_mode_select_accepts_one_mode_and_byte_limited_description(run_config):
    body = """
        matter:
          endpoints:
            1:
              mode_select: {}
              clusters:
                mode_select:
                  description: DESC
                  modes:
                    - {label: Only}
    """
    assert run_config(body.replace("DESC", "Speed")).ok
    # 33 two-byte characters are 66 bytes, over the 64 byte limit although they are fewer than 64 characters
    too_long = run_config(body.replace("DESC", "\u00e9" * 33))
    assert not too_long.ok and "maximum is 64" in too_long.output


def test_mode_cluster_still_needs_two_modes(run_config):
    result = run_config(
        washer("laundry_washer_mode:\n  modes:\n    - {label: Normal, tags: [normal]}\n")
    )
    assert not result.ok and "at least 2" in result.output


def test_utf8_prefix_length_is_in_the_header():
    """The C++ helper that truncates StatusText / labels on a character boundary."""
    text = (COMPONENTS / "matter" / "matter_delegates.h").read_text()
    assert "utf8_prefix_length" in text and "& 0xC0) == 0x80" in text
    # the same algorithm in Python, to document the expected results
    def prefix(value: bytes, limit: int) -> int:
        if len(value) <= limit:
            return len(value)
        n = limit
        while n > 0 and (value[n] & 0xC0) == 0x80:
            n -= 1
        return n

    data = ("a" + "\u00e9" * 40).encode()
    assert prefix(data, 64) == 63  # not 64: that would cut an é in half
    assert prefix(b"short", 64) == 5


def test_layout_extras_match_attach_code():
    """conformance `layout_extras` and the C++ attach code create the same things."""
    from esphome.core import CORE

    CORE.name = "unit"
    from components.matter import delegates
    from components.matter.data_model.clusters import CLUSTERS_BY_NAME
    from components.matter.util import snake_case

    modes = (COMPONENTS / "matter" / "matter_modes.cpp").read_text()
    ops = (COMPONENTS / "matter" / "matter_operational_state.cpp").read_text()
    mode_extras = delegates.layout_extras(CLUSTERS_BY_NAME["RVCRunMode"], {})
    for name in mode_extras["cmds"]:
        assert f"create_{snake_case(name)}(cluster)" in modes, name
    for name in mode_extras["attrs"]:
        if name == "SupportedModes":
            assert "create_supported_modes(" in modes
    select_extras = delegates.layout_extras(CLUSTERS_BY_NAME["ModeSelect"], {})
    assert "ChangeToModeResponse" not in select_extras["cmds"]
    config = {
        delegates.CONF_ON_PAUSE: [1],
        delegates.CONF_ON_RESUME: [1],
        delegates.CONF_ON_START: [1],
        delegates.CONF_ON_STOP: [1],
        delegates.CONF_COUNTDOWN_TIME: "sensor_id",
    }
    extras = delegates.layout_extras(CLUSTERS_BY_NAME["OperationalState"], config)
    assert set(extras["cmds"]) == {"Pause", "Resume", "Start", "Stop", "OperationalCommandResponse"}
    assert extras["attrs"] == ["CountdownTime"]
    for name in extras["cmds"]:
        assert f"create_{snake_case(name)}(cluster)" in ops, name
    for name in extras["events"]:
        assert f"create_{snake_case(name)}(cluster)" in ops, name
    assert "create_countdown_time(" in ops
    rvc = delegates.layout_extras(CLUSTERS_BY_NAME["RVCOperationalState"], {delegates.CONF_ON_GO_HOME: [1]})
    assert rvc["cmds"] == ["GoHome", "OperationalCommandResponse"]
    assert "GoHome::Id" in ops


@pytest.mark.slow
def test_codegen_countdown_clamp_and_send_event(generated_main):
    """CountdownTime is limited to 3 days and send_event no longer takes the CHIP lock on the main loop."""
    ops = (COMPONENTS / "matter" / "matter_operational_state.cpp").read_text()
    assert "259200" in ops
    actions = (COMPONENTS / "matter" / "matter_actions.cpp").read_text()
    start = actions.index("void send_event(")
    assert "ScopedChipStackLock" not in actions[start:]
    assert "ScheduleWork" in actions[start:]
