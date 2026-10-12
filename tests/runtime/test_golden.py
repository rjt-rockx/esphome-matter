"""Golden behavioural tests: the real firmware (tests/qemu/golden.yaml) under QEMU, driven from both sides.

Matter side: matter.js harness (commands, attribute reads, subscription reports and events).
ESPHome side: aioesphomeapi (entity commands and states, and the api actions defined in golden.yaml).

Time budgets are generous compared to real hardware because everything runs under CPU emulation. They are
constants so that they can be tightened when the loop is run on faster machines.
"""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.golden

REPORT = 6  # seconds a Matter report may take after an ESPHome-side change
QUIET = 3  # seconds without any further report that count as "settled"

BINARY, MONO, SWITCH, TEMP, BUTTON, FAN, HEATER, AC = 1, 2, 3, 4, 5, 6, 7, 8  # endpoints of golden.yaml


def oscillates(values: list) -> bool:
    """True if the report sequence contains a, b, a with a != b (an echo that flips the state back and forth)."""
    return any(values[i] == values[i + 2] and values[i] != values[i + 1] for i in range(len(values) - 2))


def move_to_level(level: int) -> dict:
    return {"level": level, "transitionTime": 0, "optionsMask": {}, "optionsOverride": {}}


def event_number(data, key: str):
    """Event records carry a list of events [{eventNumber, priority, epochTimestamp, data: {...}}]: fetch a payload field."""
    for ev in data if isinstance(data, list) else [data]:
        payload = ev.get("data", ev) if isinstance(ev, dict) else {}
        if key in payload:
            return payload[key]
    return None


def event_names(records: list[dict], ep: int) -> list[str]:
    """Names of the events in the records, in order (one record may carry several events)."""
    # Reports of events that are emitted close together can reach the controller out of order (seen: a MultiPressOngoing
    # report before the ShortRelease that has a lower eventNumber), so order by the device's event number.
    found: list[tuple[int, str]] = []
    for r in records:
        if r["kind"] == "event" and r["endpoint"] == ep:
            for ev in r["data"] if isinstance(r["data"], list) else [r["data"]]:
                found.append((ev.get("eventNumber", 0), r["event"]))
    return [name for _, name in sorted(found)]


# -- 1. light ------------------------------------------------------------------------------------------------------
def test_light_on_off_level_and_echo(node, api, device):
    m = node
    # Start from a known state.
    api.light("golden_mono_light", state=False)
    assert api.wait_state("golden_mono_light", lambda s: s.state is False, 5)
    time.sleep(QUIET)

    # Step 1: OnOff.On -> report true, API light on.
    mark = m.mark()
    m.invoke(MONO, "OnOff", "On")
    assert m.wait_report(MONO, "OnOff", "OnOff", True, since=mark, timeout=REPORT), "no OnOff=true report"
    assert api.wait_state("golden_mono_light", lambda s: s.state is True, REPORT), "API light is not on"
    time.sleep(QUIET)
    seq = m.reports(MONO, "OnOff", "OnOff", since=mark)
    assert not oscillates(seq), f"OnOff echo after On: {seq}"
    assert seq[-1] is True, f"OnOff reports after On: {seq}"

    # Step 2: MoveToLevel 128 -> API brightness ~0.50, CurrentLevel report 128.
    mark = m.mark()
    m.invoke(MONO, "LevelControl", "MoveToLevel", move_to_level(128))
    assert m.wait_report(MONO, "LevelControl", "CurrentLevel", 128, since=mark, timeout=REPORT), (
        f"CurrentLevel reports: {m.reports(MONO, 'LevelControl', 'CurrentLevel', since=mark)}"
    )
    st = api.wait_state("golden_mono_light", lambda s: abs(s.brightness - 0.5) <= 0.01, REPORT)
    assert st is not None, f"API brightness {api.state('golden_mono_light').brightness}, expected 0.50 +- 0.01"
    time.sleep(QUIET)
    for ep, cl, at in ((MONO, "OnOff", "OnOff"), (MONO, "LevelControl", "CurrentLevel")):
        seq = m.reports(ep, cl, at, since=mark)
        assert not oscillates(seq), f"{cl}.{at} echo after MoveToLevel: {seq}"
    assert m.read(MONO, "LevelControl", "CurrentLevel") == 128

    # Step 3: API turn_off -> OnOff=false report.
    mark = m.mark()
    api.light("golden_mono_light", state=False)
    assert m.wait_report(MONO, "OnOff", "OnOff", False, since=mark, timeout=REPORT), "no OnOff=false report after API turn_off"
    time.sleep(QUIET)
    seq = m.reports(MONO, "OnOff", "OnOff", since=mark)
    assert not oscillates(seq), f"OnOff echo after API turn_off: {seq}"
    assert m.read(MONO, "OnOff", "OnOff") is False

    # The binary light as well: On/Off from Matter, state seen by the API.
    mark = m.mark()
    m.invoke(BINARY, "OnOff", "On")
    assert api.wait_state("golden_binary_light", lambda s: s.state is True, REPORT)
    assert m.wait_report(BINARY, "OnOff", "OnOff", True, since=mark, timeout=REPORT)
    m.invoke(BINARY, "OnOff", "Off")
    assert api.wait_state("golden_binary_light", lambda s: s.state is False, REPORT)
    time.sleep(QUIET)
    seq = m.reports(BINARY, "OnOff", "OnOff", since=mark)
    assert not oscillates(seq), f"binary light OnOff echo: {seq}"


# -- 2. sensor -----------------------------------------------------------------------------------------------------
def test_sensor_report_and_coalescing(node, api, device):
    m = node
    mark = m.mark()
    api.action("set_temp", value=23.4)
    assert m.wait_report(TEMP, "TemperatureMeasurement", "MeasuredValue", 2340, since=mark, timeout=REPORT), (
        f"MeasuredValue reports: {m.reports(TEMP, 'TemperatureMeasurement', 'MeasuredValue', since=mark)}"
    )

    # 50 updates in a burst, ending at 19.0.
    logmark = device.log_mark()
    mark = m.mark()
    values = [20.0 + (i % 7) * 0.3 for i in range(49)] + [19.0]
    elapsed = api.action_burst("set_temp", [{"value": v} for v in values])
    print(f"burst of {len(values)} set_temp calls took {elapsed:.2f} s")
    assert m.wait_report(TEMP, "TemperatureMeasurement", "MeasuredValue", 1900, since=mark, timeout=REPORT + 4), (
        f"MeasuredValue reports: {m.reports(TEMP, 'TemperatureMeasurement', 'MeasuredValue', since=mark)}"
    )
    time.sleep(QUIET)
    assert m.reports(TEMP, "TemperatureMeasurement", "MeasuredValue", since=mark)[-1] == 1900
    assert m.read(TEMP, "TemperatureMeasurement", "MeasuredValue") == 1900
    failed = device.log_lines(logmark, r"Failed to schedule")
    assert not failed, f"UART: {failed[:3]}"


# -- 3. generic switch ---------------------------------------------------------------------------------------------
def _press(m, api, kind: str, expect_complete: int) -> list[str]:
    """Run the gesture and return the names of the events seen, in order."""
    mark = m.mark()
    api.action("press", kind=kind)
    done = m.wait_event(BUTTON, "Switch", "MultiPressComplete" if kind != "long" else "LongRelease", since=mark, timeout=REPORT + 6)
    assert done is not None, f"no final event for a {kind} press; events: {[(r['event']) for r in m.records(mark) if r['kind'] == 'event']}"
    if kind != "long":
        assert event_number(done, "totalNumberOfPressesCounted") == expect_complete, f"MultiPressComplete data: {done}"
    return event_names(m.records(mark), BUTTON)


def test_generic_switch_events(node, api, device):
    m = node
    names = _press(m, api, "short", 1)
    assert names == ["InitialPress", "ShortRelease", "MultiPressComplete"], names
    mark = m.mark()
    assert m.read(BUTTON, "Switch", "CurrentPosition") == 0

    names = _press(m, api, "double", 2)
    assert names == ["InitialPress", "ShortRelease", "MultiPressOngoing", "ShortRelease", "MultiPressComplete"], names
    assert m.read(BUTTON, "Switch", "CurrentPosition") == 0

    names = _press(m, api, "long", 0)
    assert names == ["InitialPress", "LongPress", "LongRelease"], names
    assert m.read(BUTTON, "Switch", "CurrentPosition") == 0
    del mark


# -- 4. fan --------------------------------------------------------------------------------------------------------
def test_fan_mode_both_directions(node, api, device):
    m = node
    LOW, HIGH = 1, 3
    api.fan("golden_fan", state=False)
    assert api.wait_state("golden_fan", lambda s: s.state is False, 5)
    time.sleep(QUIET)

    # Matter -> ESPHome: FanMode=Low
    mark = m.mark()
    m.write(FAN, "FanControl", "FanMode", LOW)
    st = api.wait_state("golden_fan", lambda s: s.state is True and s.speed_level == 1, REPORT)
    assert st is not None, f"API fan after FanMode=Low: {api.state('golden_fan')}"
    assert m.wait_report(FAN, "FanControl", "FanMode", LOW, since=mark, timeout=REPORT) or m.read(FAN, "FanControl", "FanMode") == LOW

    # ESPHome -> Matter: speed 3
    mark = m.mark()
    api.fan("golden_fan", state=True, speed_level=3)
    assert m.wait_report(FAN, "FanControl", "PercentCurrent", 100, since=mark, timeout=REPORT), (
        f"PercentCurrent reports: {m.reports(FAN, 'FanControl', 'PercentCurrent', since=mark)}"
    )
    assert m.wait_report(FAN, "FanControl", "SpeedCurrent", 3, since=mark, timeout=REPORT), (
        f"SpeedCurrent reports: {m.reports(FAN, 'FanControl', 'SpeedCurrent', since=mark)}"
    )
    assert m.wait_report(FAN, "FanControl", "FanMode", HIGH, since=mark, timeout=REPORT), (
        f"FanMode reports: {m.reports(FAN, 'FanControl', 'FanMode', since=mark)}"
    )
    time.sleep(QUIET)
    assert m.read(FAN, "FanControl", "FanMode") == HIGH

    # Off from the API
    api.fan("golden_fan", state=False)
    assert m.wait_report(FAN, "FanControl", "PercentCurrent", 0, since=mark, timeout=REPORT)


# -- 5. reconcile and consistency ----------------------------------------------------------------------------------
def test_reconcile_refused_turn_on(node, api, device):
    m = node
    api.switch("golden_switch", False)
    assert api.wait_state("golden_switch", lambda s: s.state is False, 5)
    time.sleep(QUIET)
    api.action("set_refuse", refuse=True)
    try:
        logmark = device.log_mark()
        mark = m.mark()
        m.invoke(SWITCH, "OnOff", "On")
        assert device.wait_log(r"turn_on refused", since=logmark, timeout=REPORT), "the template switch did not see turn_on"
        time.sleep(1)  # plan: back to false within 1 s of the apply; allow the report latency on top
        reports = m.reports(SWITCH, "OnOff", "OnOff", since=mark)
        time.sleep(QUIET)
        assert m.read(SWITCH, "OnOff", "OnOff") is False, f"OnOff stayed true after the refused turn_on; reports: {reports}"
        assert api.state("golden_switch").state is False
    finally:
        api.action("set_refuse", refuse=False)


def test_interleaved_toggles_converge(node, api, device):
    m = node
    api.action("set_refuse", refuse=False)
    api.switch("golden_switch", False)
    assert api.wait_state("golden_switch", lambda s: s.state is False, 5)
    time.sleep(QUIET)
    mark = m.mark()
    for i in range(20):
        cur = api.state("golden_switch").state
        api.switch("golden_switch", not cur)
        m.invoke(SWITCH, "OnOff", "Toggle")
    time.sleep(2 * QUIET)
    matter_state = m.read(SWITCH, "OnOff", "OnOff")
    api_state = api.state("golden_switch").state
    last_report = (m.reports(SWITCH, "OnOff", "OnOff", since=mark) or [None])[-1]
    assert matter_state == api_state, f"Matter OnOff={matter_state} but API switch={api_state} (last report {last_report})"
    assert last_report in (None, matter_state), f"last report {last_report} != attribute {matter_state}"


# -- 6. climate ----------------------------------------------------------------------------------------------------
def _heater_to_heat(m, api) -> None:
    from aioesphomeapi import ClimateMode

    mark = m.mark()
    api.climate("golden_heater", mode=ClimateMode.HEAT, target_temperature=20.0)
    assert api.wait_state("golden_heater", lambda s: s.mode == ClimateMode.HEAT, 5)
    assert m.wait_report(HEATER, "Thermostat", "SystemMode", 4, since=mark, timeout=REPORT) or m.read(HEATER, "Thermostat", "SystemMode") == 4
    time.sleep(QUIET)


def test_thermostat_setpoint_both_directions(node, api, device):
    m = node
    _heater_to_heat(m, api)

    # Matter -> ESPHome
    m.write(HEATER, "Thermostat", "OccupiedHeatingSetpoint", 2250)
    st = api.wait_state("golden_heater", lambda s: abs(s.target_temperature - 22.5) < 0.05, REPORT)
    assert st is not None, f"API target {api.state('golden_heater').target_temperature}, expected 22.5"

    # ESPHome -> Matter
    mark = m.mark()
    api.climate("golden_heater", target_temperature=19.0)
    assert m.wait_report(HEATER, "Thermostat", "OccupiedHeatingSetpoint", 1900, since=mark, timeout=REPORT), (
        f"setpoint reports: {m.reports(HEATER, 'Thermostat', 'OccupiedHeatingSetpoint', since=mark)}"
    )
    time.sleep(QUIET)
    seq = m.reports(HEATER, "Thermostat", "OccupiedHeatingSetpoint", since=mark)
    assert not oscillates(seq), f"setpoint echo: {seq}"


def test_thermostat_setpoint_out_of_range_reconciles(node, api, device):
    m = node
    _heater_to_heat(m, api)
    # visual max is 30 C. The write may be refused outright or clamped; either way Matter and ESPHome must agree afterwards.
    try:
        m.write(HEATER, "Thermostat", "OccupiedHeatingSetpoint", 3500)
        refused = False
    except Exception as e:  # noqa: BLE001
        refused = True
        print(f"write refused: {e}")
    time.sleep(2 * QUIET)
    matter_sp = m.read(HEATER, "Thermostat", "OccupiedHeatingSetpoint")
    target = api.state("golden_heater").target_temperature
    print(f"refused={refused} matter={matter_sp} api={target}")
    assert target <= 30.0 + 1e-3, f"ESPHome accepted a target above visual max: {target}"
    assert matter_sp <= 3000, f"Matter keeps an out-of-range setpoint: {matter_sp}"
    assert abs(matter_sp / 100 - target) < 0.06, f"Matter {matter_sp / 100} and ESPHome {target} disagree"


def test_ac_fan_mode_both_directions(node, api, device):
    from aioesphomeapi import ClimateFanMode, ClimateMode

    m = node
    LOW, HIGH = 1, 3
    mark = m.mark()
    api.climate("golden_ac", mode=ClimateMode.COOL, fan_mode=ClimateFanMode.LOW)
    assert api.wait_state("golden_ac", lambda s: s.mode == ClimateMode.COOL and s.fan_mode == ClimateFanMode.LOW, 5)
    assert m.wait_report(AC, "FanControl", "FanMode", LOW, since=mark, timeout=REPORT) or m.read(AC, "FanControl", "FanMode") == LOW
    time.sleep(QUIET)

    m.write(AC, "FanControl", "FanMode", HIGH)
    st = api.wait_state("golden_ac", lambda s: s.fan_mode == ClimateFanMode.HIGH, REPORT)
    assert st is not None, f"API fan_mode {api.state('golden_ac').fan_mode}, expected HIGH"
    time.sleep(QUIET)
    assert m.read(AC, "FanControl", "FanMode") == HIGH
