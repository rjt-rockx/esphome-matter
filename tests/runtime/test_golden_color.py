"""Golden tests for colour and colour temperature lights: endpoint 13 of tests/qemu/golden.yaml.

The endpoint is an extended_color_light bound to an ESPHome `rgbww` light (153..370 mireds, color_interlock) whose
outputs are template outputs, so the light state is observed through the ESPHome API (aioesphomeapi) and the Matter
state through reads and subscription reports. Same fixtures and conventions as test_golden.py.
"""

from __future__ import annotations

import itertools
import time

import pytest
from test_golden import QUIET, REPORT, move_to_level, oscillates

pytestmark = pytest.mark.golden

COLOR = 13
LIGHT = "golden_color_light"
MIN_MIREDS, MAX_MIREDS = 153, 370

HS, XY, CT = 0, 1, 2  # ColorControl.ColorMode
HUE_TOL = 6  # of 254 steps (8 bit RGB round trip)
SAT_TOL = 6
XY_TOL = 2500  # of 65536
MIREDS_TOL = 3


def _opts() -> dict:
    return {"optionsMask": {}, "optionsOverride": {}}


def move_to_hs(hue: int, sat: int, ds: int = 0) -> dict:
    return {"hue": hue, "saturation": sat, "transitionTime": ds, **_opts()}


def move_to_xy(x: int, y: int, ds: int = 0) -> dict:
    return {"colorX": x, "colorY": y, "transitionTime": ds, **_opts()}


def move_to_ct(mireds: int, ds: int = 0) -> dict:
    return {"colorTemperatureMireds": mireds, "transitionTime": ds, **_opts()}


def hue_close(a: int, b: int, tol: int = HUE_TOL) -> bool:
    d = abs(a - b) % 255
    return min(d, 255 - d) <= tol


def color_reports(m, since: int) -> dict[str, list]:
    """Every ColorControl attribute report since `since`, by attribute name."""
    out: dict[str, list] = {}
    for r in m.records(since):
        if (
            r["kind"] == "attribute"
            and r["endpoint"] == COLOR
            and r["cluster"] == "ColorControl"
        ):
            out.setdefault(r["attribute"], []).append(r["value"])
    return out


def settle(m, api) -> None:
    """A known starting point: light on at full brightness, warm white (colour temperature mode)."""
    api.light(
        LIGHT, state=True, brightness=1.0, color_brightness=0.0, color_temperature=300.0
    )
    assert api.wait_state(LIGHT, lambda s: s.state is True, REPORT)
    time.sleep(QUIET)
    # Make sure Matter agrees with the starting point (OnOff and level are the preconditions of colour commands).
    assert m.read(COLOR, "OnOff", "OnOff") is True


def rgb_of(s) -> tuple[float, float, float]:
    return (s.red, s.green, s.blue)


@pytest.fixture
def color_light(node, api, device):
    m = node
    # Colour commands are ignored while the light is off (ExecuteIfOff is not set): start on.
    settle(m, api)
    return m


# -- capabilities --------------------------------------------------------------------------------------------------
def test_color_capabilities_follow_the_light(color_light, api):
    m = color_light
    assert m.read(COLOR, "ColorControl", "ColorTempPhysicalMinMireds") == MIN_MIREDS
    assert m.read(COLOR, "ColorControl", "ColorTempPhysicalMaxMireds") == MAX_MIREDS
    caps = m.read(COLOR, "ColorControl", "ColorCapabilities")
    print(f"ColorCapabilities: {caps}")
    # bit 0 HS, bit 2 XY, bit 4 CT (a bitmap object or an int, depending on the harness decoding)
    if isinstance(caps, int):
        assert caps & 0b10101 == 0b10101, f"ColorCapabilities {caps:#x}"
    else:
        assert (
            caps.get("hueSaturation")
            and caps.get("xy")
            and caps.get("colorTemperature")
        ), caps


# -- Matter -> ESPHome ---------------------------------------------------------------------------------------------
def test_move_to_hue_and_saturation(color_light, api):
    m = color_light
    mark = m.mark()
    m.invoke(
        COLOR, "ColorControl", "MoveToHueAndSaturation", move_to_hs(170, 254)
    )  # 240 degrees: blue
    st = api.wait_state(
        LIGHT, lambda s: s.blue > 0.8 and s.red < 0.2 and s.green < 0.2, REPORT
    )
    assert st is not None, (
        f"API light after MoveToHueAndSaturation(170, 254): {api.state(LIGHT)}"
    )
    assert m.wait_report(
        COLOR,
        "ColorControl",
        "CurrentHue",
        lambda v: hue_close(v, 170),
        since=mark,
        timeout=REPORT,
    ) or hue_close(m.read(COLOR, "ColorControl", "CurrentHue"), 170)
    time.sleep(QUIET)
    hue = m.read(COLOR, "ColorControl", "CurrentHue")
    sat = m.read(COLOR, "ColorControl", "CurrentSaturation")
    assert hue_close(hue, 170), f"CurrentHue {hue}"
    assert abs(sat - 254) <= SAT_TOL, f"CurrentSaturation {sat}"
    assert m.read(COLOR, "ColorControl", "ColorMode") == HS
    reports = color_reports(m, mark)
    for attr, seq in reports.items():
        assert not oscillates(seq), f"{attr} echo after MoveToHueAndSaturation: {seq}"
    # The command is applied once: the API light keeps the colour it got (no fight with ESPHome echoes).
    st = api.state(LIGHT)
    assert st.blue > 0.8 and st.red < 0.2 and st.green < 0.2, f"API light drifted: {st}"


def test_move_to_color_xy(color_light, api):
    m = color_light
    mark = m.mark()
    # Matter xy is converted with the wide gamut matrix of Home Assistant / Hue: (0.70, 0.30) is pure red there.
    x, y = int(0.70 * 65536), int(0.30 * 65536)
    m.invoke(COLOR, "ColorControl", "MoveToColor", move_to_xy(x, y))
    st = api.wait_state(
        LIGHT, lambda s: s.red > 0.8 and s.green < 0.25 and s.blue < 0.25, REPORT
    )
    assert st is not None, f"API light after MoveToColor(red): {api.state(LIGHT)}"
    time.sleep(QUIET)
    cx = m.read(COLOR, "ColorControl", "CurrentX")
    cy = m.read(COLOR, "ColorControl", "CurrentY")
    print(
        f"XY read back: {cx}, {cy} (sent {x}, {y}); ColorMode {m.read(COLOR, 'ColorControl', 'ColorMode')}"
    )
    assert abs(cx - x) <= XY_TOL and abs(cy - y) <= XY_TOL, (
        f"CurrentX/Y {cx}, {cy}, expected about {x}, {y}"
    )
    # Spec: MoveToColor puts the light in XY mode. A light that shows an RGB colour may keep reporting HS only if it
    # also keeps CurrentX/Y consistent, which the assertions above cover: report what it does.
    mode = m.read(COLOR, "ColorControl", "ColorMode")
    assert mode in (HS, XY), f"ColorMode {mode}"
    assert mode == XY, (
        f"ColorMode after MoveToColor is {mode} (HS=0, XY=1): the spec requires CurrentX/CurrentY mode"
    )
    for attr, seq in color_reports(m, mark).items():
        assert not oscillates(seq), f"{attr} echo after MoveToColor: {seq}"


def test_move_to_color_temperature(color_light, api):
    m = color_light
    # Leave the colour temperature mode first so that the command has something to change.
    m.invoke(COLOR, "ColorControl", "MoveToHueAndSaturation", move_to_hs(0, 254))
    assert api.wait_state(LIGHT, lambda s: s.red > 0.8 and s.blue < 0.2, REPORT)
    time.sleep(QUIET)

    mark = m.mark()
    m.invoke(COLOR, "ColorControl", "MoveToColorTemperature", move_to_ct(200))
    st = api.wait_state(
        LIGHT,
        lambda s: (
            abs(s.color_temperature - 200) <= MIREDS_TOL and s.color_brightness < 0.05
        ),
        REPORT,
    )
    assert st is not None, (
        f"API light after MoveToColorTemperature(200): {api.state(LIGHT)}"
    )
    assert (
        m.wait_report(
            COLOR, "ColorControl", "ColorMode", CT, since=mark, timeout=REPORT
        )
        or m.read(COLOR, "ColorControl", "ColorMode") == CT
    )
    assert m.wait_report(
        COLOR, "ColorControl", "ColorTemperatureMireds", 200, since=mark, timeout=REPORT
    ) or (m.read(COLOR, "ColorControl", "ColorTemperatureMireds") == 200)
    time.sleep(QUIET)
    assert m.read(COLOR, "ColorControl", "ColorMode") == CT
    mireds = m.read(COLOR, "ColorControl", "ColorTemperatureMireds")
    assert abs(mireds - 200) <= MIREDS_TOL, f"ColorTemperatureMireds {mireds}"
    for attr, seq in color_reports(m, mark).items():
        assert not oscillates(seq), f"{attr} echo after MoveToColorTemperature: {seq}"
    assert abs(api.state(LIGHT).color_temperature - 200) <= MIREDS_TOL


# -- ESPHome -> Matter ---------------------------------------------------------------------------------------------
def test_esphome_color_change_is_reported_once(color_light, api, device):
    m = color_light
    logmark = device.log_mark()
    mark = m.mark()
    api.light(
        LIGHT, state=True, brightness=1.0, rgb=(0.0, 1.0, 0.0), color_brightness=1.0
    )  # 120 degrees: green
    assert m.wait_report(
        COLOR,
        "ColorControl",
        "CurrentHue",
        lambda v: hue_close(v, 85),
        since=mark,
        timeout=REPORT,
    ), f"CurrentHue reports: {color_reports(m, mark).get('CurrentHue')}"
    # (saturation may already be at its maximum from the previous test: attributes only report changes)
    time.sleep(QUIET)
    reports = color_reports(m, mark)
    print(f"ColorControl reports after an ESPHome colour change: {reports}")
    # The mode stays what the controller used last (HS or XY) when the colour is representable in both.
    assert m.read(COLOR, "ColorControl", "ColorMode") in (HS, XY)
    assert hue_close(m.read(COLOR, "ColorControl", "CurrentHue"), 85)
    assert m.read(COLOR, "ColorControl", "CurrentSaturation") >= 254 - SAT_TOL
    # No echo storm: every attribute reports a handful of times, never flip-flops.
    for attr, seq in reports.items():
        assert len(seq) <= 4, (
            f"{attr} reported {len(seq)} times for one ESPHome change: {seq}"
        )
        assert not oscillates(seq), f"{attr} oscillates: {seq}"
    # And the change did not come back to ESPHome as a Matter command: the light is still green.
    st = api.state(LIGHT)
    assert st.green > 0.9 and st.red < 0.1 and st.blue < 0.1, (
        f"API light changed after settling: {st}"
    )
    bounced = device.log_lines(logmark, r"(?i)ColorControl.*(MoveTo|command)")
    print(f"UART lines about Matter colour commands: {bounced[:5]}")


def test_esphome_color_temperature_change_is_reported(color_light, api):
    m = color_light
    api.light(
        LIGHT, state=True, brightness=1.0, rgb=(1.0, 0.0, 0.0), color_brightness=1.0
    )
    assert (
        m.wait_report(COLOR, "ColorControl", "ColorMode", HS, timeout=REPORT)
        or m.read(COLOR, "ColorControl", "ColorMode") == HS
    )
    time.sleep(QUIET)
    mark = m.mark()
    api.light(
        LIGHT, state=True, brightness=1.0, color_brightness=0.0, color_temperature=180.0
    )
    assert m.wait_report(
        COLOR,
        "ColorControl",
        "ColorTemperatureMireds",
        lambda v: abs(v - 180) <= MIREDS_TOL,
        since=mark,
        timeout=REPORT,
    ), f"reports: {color_reports(m, mark)}"
    time.sleep(QUIET)
    assert m.read(COLOR, "ColorControl", "ColorMode") == CT
    for attr, seq in color_reports(m, mark).items():
        assert len(seq) <= 4 and not oscillates(seq), (
            f"{attr} reports after an ESPHome CT change: {seq}"
        )


# -- mode switches -------------------------------------------------------------------------------------------------
def test_color_mode_switch_ct_hs_ct(color_light, api):
    m = color_light
    # CT
    m.invoke(COLOR, "ColorControl", "MoveToColorTemperature", move_to_ct(250))
    assert api.wait_state(
        LIGHT,
        lambda s: (
            abs(s.color_temperature - 250) <= MIREDS_TOL and s.color_brightness < 0.05
        ),
        REPORT,
    ), api.state(LIGHT)
    time.sleep(QUIET)
    assert m.read(COLOR, "ColorControl", "ColorMode") == CT

    # CT -> HS
    mark = m.mark()
    m.invoke(COLOR, "ColorControl", "MoveToHueAndSaturation", move_to_hs(0, 254))
    st = api.wait_state(
        LIGHT, lambda s: s.red > 0.8 and s.green < 0.2 and s.blue < 0.2, REPORT
    )
    assert st is not None, f"API light after CT -> HS: {api.state(LIGHT)}"
    assert (
        m.wait_report(
            COLOR, "ColorControl", "ColorMode", HS, since=mark, timeout=REPORT
        )
        or m.read(COLOR, "ColorControl", "ColorMode") == HS
    )
    time.sleep(QUIET)
    assert m.read(COLOR, "ColorControl", "ColorMode") == HS
    seq = color_reports(m, mark).get("ColorMode", [])
    assert not oscillates(seq), f"ColorMode flip-flop on CT -> HS: {seq}"
    # The white channels are off when the RGB colour is shown (colour interlock).
    st = api.state(LIGHT)
    assert st.cold_white < 0.05 and st.warm_white < 0.05, (
        f"white channels on in HS mode: {st}"
    )

    # HS -> CT
    mark = m.mark()
    m.invoke(COLOR, "ColorControl", "MoveToColorTemperature", move_to_ct(320))
    st = api.wait_state(
        LIGHT,
        lambda s: (
            abs(s.color_temperature - 320) <= MIREDS_TOL and s.color_brightness < 0.05
        ),
        REPORT,
    )
    assert st is not None, f"API light after HS -> CT: {api.state(LIGHT)}"
    assert (
        m.wait_report(
            COLOR, "ColorControl", "ColorMode", CT, since=mark, timeout=REPORT
        )
        or m.read(COLOR, "ColorControl", "ColorMode") == CT
    )
    time.sleep(QUIET)
    assert m.read(COLOR, "ColorControl", "ColorMode") == CT
    assert (
        abs(m.read(COLOR, "ColorControl", "ColorTemperatureMireds") - 320) <= MIREDS_TOL
    )
    seq = color_reports(m, mark).get("ColorMode", [])
    assert not oscillates(seq), f"ColorMode flip-flop on HS -> CT: {seq}"


# -- range handling ------------------------------------------------------------------------------------------------
def test_color_temperature_out_of_range(color_light, api):
    m = color_light
    m.invoke(COLOR, "ColorControl", "MoveToColorTemperature", move_to_ct(250))
    assert api.wait_state(
        LIGHT, lambda s: abs(s.color_temperature - 250) <= MIREDS_TOL, REPORT
    )
    time.sleep(QUIET)

    # ColorTemperatureMireds is read-only in the spec: a write is refused (or, if accepted, must clamp).
    try:
        m.write(COLOR, "ColorControl", "ColorTemperatureMireds", 600)
        refused = False
    except Exception as e:  # noqa: BLE001
        refused = True
        print(f"write refused: {e}")
    time.sleep(QUIET)
    now = m.read(COLOR, "ColorControl", "ColorTemperatureMireds")
    assert MIN_MIREDS - MIREDS_TOL <= now <= MAX_MIREDS + MIREDS_TOL, (
        f"ColorTemperatureMireds {now} outside the physical range after a write (refused={refused})"
    )
    if refused:
        assert abs(now - 250) <= MIREDS_TOL, f"refused write changed the value to {now}"
    ct = api.state(LIGHT).color_temperature
    assert MIN_MIREDS - 1 <= ct <= MAX_MIREDS + 1, (
        f"ESPHome colour temperature {ct} outside the range"
    )

    # The command clamps to ColorTempPhysicalMin/MaxMireds (or is refused, leaving the value alone).
    for asked, lo, hi in ((600, MAX_MIREDS, MAX_MIREDS), (50, MIN_MIREDS, MIN_MIREDS)):
        try:
            m.invoke(COLOR, "ColorControl", "MoveToColorTemperature", move_to_ct(asked))
            ok = True
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"MoveToColorTemperature({asked}) refused: {e}")
        time.sleep(2 * QUIET)
        now = m.read(COLOR, "ColorControl", "ColorTemperatureMireds")
        ct = api.state(LIGHT).color_temperature
        print(f"asked {asked}: ok={ok} matter={now} api={ct}")
        assert MIN_MIREDS - MIREDS_TOL <= now <= MAX_MIREDS + MIREDS_TOL, (
            f"ColorTemperatureMireds {now} outside the range"
        )
        assert MIN_MIREDS - 1 <= ct <= MAX_MIREDS + 1, (
            f"ESPHome colour temperature {ct} outside the range"
        )
        if ok:
            assert lo - MIREDS_TOL <= now <= hi + MIREDS_TOL, (
                f"{asked} mireds became {now}, expected {lo}"
            )
        assert abs(now - ct) <= MIREDS_TOL, f"Matter {now} and ESPHome {ct} disagree"


# -- transitions ---------------------------------------------------------------------------------------------------
def test_level_transition_is_not_fought_by_echoes(color_light, api):
    m = color_light
    # Start from a low level, settled.
    m.invoke(COLOR, "LevelControl", "MoveToLevel", move_to_level(20))
    assert api.wait_state(LIGHT, lambda s: abs(s.brightness - 20 / 254) <= 0.02, REPORT)
    time.sleep(QUIET)

    mark = m.mark()
    t0 = time.monotonic()
    m.invoke(
        COLOR,
        "LevelControl",
        "MoveToLevel",
        {"level": 200, "transitionTime": 30, "optionsMask": {}, "optionsOverride": {}},
    )  # 3 s
    # Sample the ESPHome side while the transition runs: brightness must only rise (an echo of a stale Matter level
    # pulling the light back is the fight this test looks for).
    samples: list[tuple[float, float]] = []
    # CHIP's own LevelControl transition runs far slower than nominal under QEMU (about 45 s for these 3 s), so
    # sample until its final report instead of assuming the nominal duration.
    deadline = time.monotonic() + REPORT + 90
    while time.monotonic() < deadline:
        samples.append(
            (round(time.monotonic() - t0, 2), round(api.state(LIGHT).brightness * 254))
        )
        if m.reports(COLOR, "LevelControl", "CurrentLevel", since=mark)[-1:] == [200]:
            break
        time.sleep(0.25)
    levels = [b for _, b in samples]
    print(f"ESPHome brightness samples (s, level/254): {samples}")
    print(
        f"CurrentLevel reports: {m.reports(COLOR, 'LevelControl', 'CurrentLevel', since=mark)}"
    )
    dips = [(a, b) for a, b in itertools.pairwise(levels) if b < a - 3]
    assert not dips, (
        f"ESPHome brightness went backwards during the MoveToLevel transition: {dips}; samples {samples}"
    )
    assert m.wait_report(
        COLOR, "LevelControl", "CurrentLevel", 200, since=mark, timeout=1
    ), (
        f"CurrentLevel reports: {m.reports(COLOR, 'LevelControl', 'CurrentLevel', since=mark)}"
    )
    elapsed = time.monotonic() - t0
    time.sleep(QUIET)
    seq = m.reports(COLOR, "LevelControl", "CurrentLevel", since=mark)
    print(
        f"CurrentLevel reports during a 3 s transition ({elapsed:.1f} s wall): {len(seq)}: {seq}"
    )
    assert seq[-1] == 200, f"final CurrentLevel reports: {seq}"
    assert m.read(COLOR, "LevelControl", "CurrentLevel") == 200
    # No fight: the level never goes back down while it rises, and a transition is not a storm of pushes.
    assert seq == sorted(seq), (
        f"CurrentLevel is not monotonic during the transition: {seq}"
    )
    assert not oscillates(seq), f"CurrentLevel oscillates: {seq}"
    # CHIP reports its own steps (rate limited); a fight would show up as dips or oscillation, checked above.
    assert len(seq) <= 120, f"{len(seq)} CurrentLevel reports for one transition: {seq}"
    st = api.wait_state(LIGHT, lambda s: abs(s.brightness - 200 / 254) <= 0.02, REPORT)
    assert st is not None, (
        f"API brightness {api.state(LIGHT).brightness}, expected {200 / 254:.3f}"
    )
    onoff = m.reports(COLOR, "OnOff", "OnOff", since=mark)
    assert not oscillates(onoff), f"OnOff flip-flop during the transition: {onoff}"
    assert m.read(COLOR, "OnOff", "OnOff") is True
    # Settled: nothing keeps moving after the end of the transition.
    later = m.mark()
    time.sleep(QUIET)
    assert not m.reports(COLOR, "LevelControl", "CurrentLevel", since=later), (
        "CurrentLevel keeps reporting after the transition"
    )
