"""`status_led:` / `pairing:` / `matter.remove_fabric` (schema) and the pure C++ logic
in matter_behaviours_logic.h (compiled and run on the host)."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import textwrap

from conftest import COMPONENTS
import pytest

PREAMBLE = """
output:
  - platform: gpio
    id: led_out
    pin: GPIO10
binary_sensor:
  - platform: gpio
    id: btn
    pin: GPIO11
"""

ENDPOINTS = """
  endpoints:
    1:
      on_off_light: {}
"""


def cfg(matter_extra: str) -> str:
    return PREAMBLE + "matter:\n" + textwrap.indent(textwrap.dedent(matter_extra), "  ") + ENDPOINTS


def test_status_led_output_ok(run_config):
    r = run_config(cfg("status_led:\n  output: led_out\n"))
    assert r.ok, r.output


@pytest.mark.parametrize("value", ["on", "off", "true", "false"])
def test_status_led_commissioned_bool(run_config, value):
    r = run_config(cfg(f"status_led:\n  output: led_out\n  commissioned: {value}\n"))
    assert r.ok, r.output


def test_status_led_needs_output_or_light(run_config):
    r = run_config(cfg("status_led:\n  commissioned: true\n"))
    assert not r.ok
    assert "output" in r.output and "light" in r.output


def test_status_led_unknown_id(run_config):
    r = run_config(cfg("status_led:\n  output: nope\n"))
    assert not r.ok
    assert "nope" in r.output


def test_pairing_defaults(run_config):
    r = run_config(cfg("pairing:\n  binary_sensor: btn\n"))
    assert r.ok, r.output
    out = r.output.replace("'", "")
    assert "open_window_hold: 3s" in out
    assert "factory_reset_hold: 10s" in out
    assert "timeout: 5min" in out


def test_pairing_requires_sensor(run_config):
    r = run_config(cfg("pairing:\n  open_window_hold: 3s\n"))
    assert not r.ok
    assert "binary_sensor" in r.output


def test_pairing_factory_reset_never(run_config):
    r = run_config(cfg("pairing:\n  binary_sensor: btn\n  factory_reset_hold: never\n"))
    assert r.ok, r.output


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ("open_window_hold: 100ms", "open_window_hold"),
        ("open_window_hold: 1min", "open_window_hold"),
        ("factory_reset_hold: 2s", "factory_reset_hold"),
        ("open_window_hold: 8s\n  factory_reset_hold: 10s", "longer than"),
        ("timeout: 1min", "timeout"),
        ("timeout: 20min", "timeout"),
    ],
)
def test_pairing_rejects_unsafe_values(run_config, body, needle):
    r = run_config(cfg(f"pairing:\n  binary_sensor: btn\n  {body}\n"))
    assert not r.ok
    assert needle in r.output


def test_remove_fabric_action(run_config):
    body = (
        "interval:\n  - interval: 1h\n    then:\n      - matter.remove_fabric:\n"
        "          fabric_index: 2\n"
    )
    r = run_config(cfg("") + body)
    assert r.ok, r.output


@pytest.mark.parametrize("index", ["0", "255"])
def test_remove_fabric_index_range(run_config, index):
    body = (
        "interval:\n  - interval: 1h\n    then:\n      - matter.remove_fabric:\n"
        f"          fabric_index: {index}\n"
    )
    r = run_config(cfg("") + body)
    assert not r.ok
    assert "fabric_index" in r.output or "range" in r.output.lower()


def test_remove_fabric_requires_index(run_config):
    body = "interval:\n  - interval: 1h\n    then:\n      - matter.remove_fabric:\n"
    r = run_config(cfg("") + body)
    assert not r.ok


def test_controllers_text_sensor(run_config):
    body = "text_sensor:\n  - platform: matter\n    controllers:\n      name: Controllers\n"
    r = run_config(cfg("") + body)
    assert r.ok, r.output


# --- pure logic -----------------------------------------------------------------------

HARNESS = r"""
#include "matter_behaviours_logic.h"
#include <cstdio>
using namespace esphome::matter;
using E = PairingHold::Event;
using S = PairingHold::Stage;
static int failures = 0;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); failures++; } } while (0)

static E run(PairingHold &h, uint32_t from, uint32_t to, bool pressed) {
  E last = E::NONE;
  for (uint32_t t = from; t <= to; t += 16) {
    E e = h.update(t, pressed);
    if (e != E::NONE) last = e;
  }
  return last;
}

int main() {
  // patterns
  CHECK(status_pattern(false, false, true, 0, true) == LedPattern::OFF);
  CHECK(status_pattern(true, true, true, 1, true) == LedPattern::DOUBLE_BLINK);
  CHECK(status_pattern(true, false, true, 0, true) == LedPattern::FAST_BLINK);
  CHECK(status_pattern(true, false, true, 2, true) == LedPattern::FAST_BLINK);
  CHECK(status_pattern(true, false, false, 0, true) == LedPattern::SLOW_BLINK);
  CHECK(status_pattern(true, false, false, 1, true) == LedPattern::ON);
  CHECK(status_pattern(true, false, false, 1, false) == LedPattern::OFF);
  CHECK(led_level(LedPattern::SLOW_BLINK, 0) && !led_level(LedPattern::SLOW_BLINK, 600));
  CHECK(led_level(LedPattern::FAST_BLINK, 100) && !led_level(LedPattern::FAST_BLINK, 200));
  CHECK(led_level(LedPattern::DOUBLE_BLINK, 100) && !led_level(LedPattern::DOUBLE_BLINK, 200) &&
        led_level(LedPattern::DOUBLE_BLINK, 350) && !led_level(LedPattern::DOUBLE_BLINK, 600));
  CHECK(!led_level(LedPattern::OFF, 5) && led_level(LedPattern::ON, 5));
  int flashes = 0; bool prev = false;
  for (uint32_t t = 0; t < LED_CONFIRM_MS; t++) { bool l = led_level(LedPattern::CONFIRM, t); if (l && !prev) flashes++; prev = l; }
  CHECK(flashes == 3);
  LedPattern o;
  CHECK(!pairing_overlay(S::IDLE, o) && !pairing_overlay(S::PRESSED, o));
  CHECK(pairing_overlay(S::OPEN_ARMED, o) && o == LedPattern::MEDIUM_BLINK);
  CHECK(pairing_overlay(S::RESET_ARMED, o) && o == LedPattern::STROBE);

  // short press: nothing
  { PairingHold h(3000, 10000); run(h, 0, 100, false);
    run(h, 200, 1500, true); CHECK(h.stage() == S::PRESSED);
    CHECK(run(h, 1600, 1700, false) == E::NONE); CHECK(h.stage() == S::IDLE); }
  // 3 s hold: window opens on release, not before
  { PairingHold h(3000, 10000); run(h, 0, 100, false);
    CHECK(run(h, 200, 3500, true) == E::NONE); CHECK(h.stage() == S::OPEN_ARMED);
    CHECK(run(h, 3600, 3700, false) == E::OPEN_WINDOW);
    CHECK(run(h, 3800, 4000, false) == E::NONE); }
  // 10 s hold: factory reset on release only
  { PairingHold h(3000, 10000); run(h, 0, 100, false);
    CHECK(run(h, 200, 11000, true) == E::NONE); CHECK(h.stage() == S::RESET_ARMED);
    CHECK(run(h, 11100, 11200, false) == E::FACTORY_RESET); }
  // factory reset disabled: a long hold only opens the window
  { PairingHold h(3000, 0); run(h, 0, 100, false);
    run(h, 200, 12000, true); CHECK(h.stage() == S::OPEN_ARMED);
    CHECK(run(h, 12100, 12200, false) == E::OPEN_WINDOW); }
  // held during boot: ignored until released once
  { PairingHold h(3000, 10000);
    run(h, 0, 15000, true); CHECK(h.stage() == S::IDLE);
    CHECK(run(h, 15100, 15200, false) == E::NONE);
    CHECK(run(h, 15300, 19000, true) == E::NONE); CHECK(h.stage() == S::OPEN_ARMED);
    CHECK(run(h, 19100, 19200, false) == E::OPEN_WINDOW); }
  // stuck button (held far past the last threshold): cancelled, nothing on release
  { PairingHold h(3000, 10000); run(h, 0, 100, false);
    run(h, 200, 10200 + PairingHold::CANCEL_AFTER_MS + 100, true);
    CHECK(h.stage() == S::IDLE);
    CHECK(run(h, 40000, 40100, false) == E::NONE); }
  // millis() wrap-around
  { PairingHold h(3000, 10000); run(h, 4294960000u, 4294960100u, false);
    uint32_t t = 4294960200u; E e = E::NONE;
    for (int i = 0; i < 300; i++, t += 16) h.update(t, true);
    CHECK(h.stage() == S::OPEN_ARMED);
    e = h.update(t, false); CHECK(e == E::OPEN_WINDOW); }
  printf(failures ? "FAILED\n" : "OK\n");
  return failures != 0;
}
"""


@pytest.mark.skipif(shutil.which("g++") is None, reason="no host C++ compiler")
def test_logic_header_on_host(tmp_path: Path):
    src = tmp_path / "t.cpp"
    src.write_text(HARNESS)
    exe = tmp_path / "t"
    comp = subprocess.run(
        ["g++", "-std=c++17", "-Wall", "-Werror", f"-I{COMPONENTS / 'matter'}", str(src), "-o", str(exe)],
        capture_output=True,
        text=True,
    )
    assert comp.returncode == 0, comp.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
