"""Golden tests for the appliance-style clusters: modes, operational state, window covering, door lock.

Same fixtures and conventions as test_golden.py (endpoints are defined in tests/qemu/golden.yaml).
"""

from __future__ import annotations

import time

import pytest
from test_golden import QUIET, REPORT, event_number, oscillates

pytestmark = pytest.mark.golden

WASHER, PRESET, COVER, LOCK = 9, 10, 11, 12


# -- modes ---------------------------------------------------------------------------------------------------------
MODE_CASES = [
    pytest.param(PRESET, "ModeSelect", "golden_preset", ["Low", "Mid", "High"], id="mode_select"),
    pytest.param(WASHER, "LaundryWasherMode", "golden_program", ["Normal", "Delicate", "Heavy"], id="laundry_washer_mode"),
]


@pytest.mark.parametrize("ep,cluster,select,options", MODE_CASES)
def test_change_to_mode_from_matter(node, api, device, ep, cluster, select, options):
    m = node
    api.select(select, options[0])
    assert api.wait_state(select, lambda s: s.state == options[0], 5)
    time.sleep(QUIET)
    mark = m.mark()
    res = m.invoke(ep, cluster, "ChangeToMode", {"newMode": 1})
    print(f"ChangeToMode response: {res}")
    st = api.wait_state(select, lambda s: s.state == options[1], REPORT)
    assert st is not None, f"select is {api.state(select).state!r}, expected {options[1]!r}"
    assert m.wait_report(ep, cluster, "CurrentMode", 1, since=mark, timeout=REPORT) or m.read(ep, cluster, "CurrentMode") == 1
    time.sleep(QUIET)
    seq = m.reports(ep, cluster, "CurrentMode", since=mark)
    assert not oscillates(seq), f"CurrentMode echo: {seq}"
    assert api.state(select).state == options[1]


@pytest.mark.parametrize("ep,cluster,select,options", MODE_CASES)
def test_select_change_reports_mode_without_echo(node, api, device, ep, cluster, select, options):
    m = node
    api.select(select, options[0])
    time.sleep(QUIET)
    logmark = device.log_mark()
    mark = m.mark()
    api.select(select, options[2])
    assert m.wait_report(ep, cluster, "CurrentMode", 2, since=mark, timeout=REPORT), (
        f"CurrentMode reports: {m.reports(ep, cluster, 'CurrentMode', since=mark)}"
    )
    time.sleep(QUIET)
    seq = m.reports(ep, cluster, "CurrentMode", since=mark)
    assert not oscillates(seq) and seq[-1] == 2, f"CurrentMode reports: {seq}"
    assert api.state(select).state == options[2]
    # The ESPHome-side change must not come back as an on_change_to_mode notification (echo).
    echoes = device.log_lines(logmark, r"golden_(preset|program) mode")
    assert not echoes, f"on_change_to_mode fired for an ESPHome-side change: {echoes}"


# -- operational state ---------------------------------------------------------------------------------------------
def test_operational_state_commands_and_triggers(node, api, device):
    m = node
    api.select("golden_wash_state", "Stopped")
    time.sleep(QUIET)
    RUNNING, PAUSED, STOPPED = 1, 2, 0
    for command, log, option, expect in (
        ("Start", "golden_wash: start", "Running", RUNNING),
        ("Pause", "golden_wash: pause", "Paused", PAUSED),
        ("Resume", "golden_wash: resume", "Running", RUNNING),
    ):
        logmark = device.log_mark()
        mark = m.mark()
        m.invoke(WASHER, "OperationalState", command)
        assert device.wait_log(log, since=logmark, timeout=REPORT), f"trigger for {command} did not fire"
        assert api.wait_state("golden_wash_state", lambda s, o=option: s.state == o, REPORT)
        assert m.wait_report(WASHER, "OperationalState", "OperationalState", expect, since=mark, timeout=REPORT), (
            f"after {command}: {m.reports(WASHER, 'OperationalState', 'OperationalState', since=mark)}"
        )

    # matter.operational_state.completed -> OperationCompletion event
    mark = m.mark()
    api.action("op_complete")
    ev = m.wait_event(WASHER, "OperationalState", "OperationCompletion", since=mark, timeout=REPORT)
    assert ev is not None, "no OperationCompletion event"
    print(f"OperationCompletion: {ev}")

    # matter.operational_state.set path
    mark = m.mark()
    api.action("op_set", state="paused")
    assert m.wait_report(WASHER, "OperationalState", "OperationalState", PAUSED, since=mark, timeout=REPORT)

    logmark = device.log_mark()
    mark = m.mark()
    m.invoke(WASHER, "OperationalState", "Stop")
    assert device.wait_log("golden_wash: stop", since=logmark, timeout=REPORT)
    assert m.wait_report(WASHER, "OperationalState", "OperationalState", STOPPED, since=mark, timeout=REPORT)
    time.sleep(QUIET)
    assert not oscillates(m.reports(WASHER, "OperationalState", "OperationalState", since=mark))


# -- cover ---------------------------------------------------------------------------------------------------------
def go_to_lift(m, percent100ths: int) -> None:
    m.invoke(COVER, "WindowCovering", "GoToLiftPercentage", {"liftPercent100thsValue": percent100ths})


def open_cover(m, api) -> None:
    api.action("set_refuse_cover", refuse=False)
    go_to_lift(m, 0)  # Matter 0 % closed = ESPHome position 1.0
    assert api.wait_state("golden_cover", lambda s: s.position >= 0.99, 40)
    time.sleep(QUIET)


def test_cover_go_to_lift_follows_and_reports(node, api, device):
    m = node
    open_cover(m, api)
    mark = m.mark()
    go_to_lift(m, 4000)  # 40 % closed -> ESPHome position 0.6
    assert m.wait_report(COVER, "WindowCovering", "TargetPositionLiftPercent100ths", 4000, since=mark, timeout=REPORT), (
        f"target reports: {m.reports(COVER, 'WindowCovering', 'TargetPositionLiftPercent100ths', since=mark)}"
    )
    assert api.wait_state("golden_cover", lambda s: abs(s.position - 0.6) < 0.02, 40), f"API cover position {api.state('golden_cover').position}"
    assert m.wait_report(
        COVER, "WindowCovering", "CurrentPositionLiftPercent100ths", lambda v: v is not None and abs(v - 4000) <= 200, since=mark, timeout=REPORT + 4
    )
    time.sleep(QUIET)
    cur = m.reports(COVER, "WindowCovering", "CurrentPositionLiftPercent100ths", since=mark)
    print(f"CurrentPositionLiftPercent100ths reports: {cur}")
    assert len([v for v in cur if v is not None and 0 < v < 3800]) >= 1, f"no intermediate position reports: {cur}"
    assert all(a <= b for a, b in zip(cur, cur[1:]) if a is not None and b is not None), f"position went backwards: {cur}"


def test_cover_stop_mid_move(node, api, device):
    m = node
    open_cover(m, api)
    go_to_lift(m, 10000)
    assert api.wait_state("golden_cover", lambda s: s.position <= 0.8, 40)
    m.invoke(COVER, "WindowCovering", "StopMotion")
    time.sleep(2 * QUIET)
    pos = api.state("golden_cover").position
    time.sleep(QUIET)
    assert api.state("golden_cover").position == pos, "cover kept moving after StopMotion"
    assert 0.0 < pos < 0.9, f"stopped at {pos}"
    assert abs(m.read(COVER, "WindowCovering", "CurrentPositionLiftPercent100ths") - (1 - pos) * 10000) <= 300


def test_cover_refused_command_reconciles(node, api, device):
    m = node
    open_cover(m, api)
    api.action("set_refuse_cover", refuse=True)
    try:
        mark = m.mark()
        t0 = time.monotonic()
        go_to_lift(m, 5000)
        # The template ignores the command: the target must fall back to the real position (0).
        ok = m.wait_report(COVER, "WindowCovering", "TargetPositionLiftPercent100ths", lambda v: v == 0, since=mark, timeout=REPORT)
        print(f"target back to current after {time.monotonic() - t0:.1f} s (includes the command round trip)")
        time.sleep(QUIET)
        tgt = m.read(COVER, "WindowCovering", "TargetPositionLiftPercent100ths")
        assert ok and tgt == 0, f"target stayed {tgt}; reports {m.reports(COVER, 'WindowCovering', 'TargetPositionLiftPercent100ths', since=mark)}"
        assert api.state("golden_cover").position >= 0.99
    finally:
        api.action("set_refuse_cover", refuse=False)


# -- lock ----------------------------------------------------------------------------------------------------------
LOCKED, UNLOCKED = 1, 2


def ensure_locked(m, api) -> None:
    api.action("set_refuse_lock", refuse=False)
    if m.read(LOCK, "DoorLock", "LockState") != LOCKED:
        m.invoke(LOCK, "DoorLock", "LockDoor")
        time.sleep(QUIET)


def test_lock_commands_from_matter(node, api, device):
    from aioesphomeapi import LockState

    m = node
    ensure_locked(m, api)
    mark = m.mark()
    m.invoke(LOCK, "DoorLock", "UnlockDoor")
    assert api.wait_state("golden_lock", lambda s: s.state == LockState.UNLOCKED, REPORT)
    assert m.wait_report(LOCK, "DoorLock", "LockState", UNLOCKED, since=mark, timeout=REPORT)
    m.invoke(LOCK, "DoorLock", "LockDoor")
    assert api.wait_state("golden_lock", lambda s: s.state == LockState.LOCKED, REPORT)
    assert m.wait_report(LOCK, "DoorLock", "LockState", LOCKED, since=mark, timeout=REPORT)
    time.sleep(QUIET)
    assert not oscillates(m.reports(LOCK, "DoorLock", "LockState", since=mark))


def test_lock_refused_unlock_reconciles(node, api, device):
    from aioesphomeapi import LockState

    m = node
    ensure_locked(m, api)
    api.action("set_refuse_lock", refuse=True)
    try:
        logmark = device.log_mark()
        m.invoke(LOCK, "DoorLock", "UnlockDoor")
        assert device.wait_log("unlock refused", since=logmark, timeout=REPORT)
        time.sleep(2 * QUIET)
        assert api.state("golden_lock").state == LockState.LOCKED
        assert m.read(LOCK, "DoorLock", "LockState") == LOCKED
    finally:
        api.action("set_refuse_lock", refuse=False)


def test_lock_operation_events_and_sources(node, api, device):
    from aioesphomeapi import LockCommand

    m = node
    ensure_locked(m, api)

    def operation(mark, op_type):
        return m.wait_event(
            LOCK, "DoorLock", "LockOperation", since=mark, timeout=REPORT + 4, pred=lambda d: event_number(d, "lockOperationType") == op_type
        )

    # ESPHome-side change: source Manual (1)
    mark = m.mark()
    api.lock("golden_lock", LockCommand.UNLOCK)
    ev = operation(mark, 1)
    assert ev is not None, f"no LockOperation(Unlock) event; records: {m.records(mark)}"
    print(f"ESPHome unlock event: {ev}")
    assert event_number(ev, "operationSource") == 1, f"source for an ESPHome change: {ev}"

    # Matter command: source Remote (7)
    mark = m.mark()
    m.invoke(LOCK, "DoorLock", "LockDoor")
    ev = operation(mark, 0)
    assert ev is not None, f"no LockOperation(Lock) event; records: {m.records(mark)}"
    print(f"Matter lock event: {ev}")
    assert event_number(ev, "operationSource") == 7, f"source for a Matter command: {ev}"
