"""Host test of the Fan Control Step logic (components/matter/matter_fan_step.h).

The header has no ESPHome / Matter dependencies, so it is compiled with the host
C++ compiler. Skipped when no compiler is available.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from conftest import COMPONENTS
import pytest

CXX = shutil.which("g++") or shutil.which("clang++") or shutil.which("c++")

pytestmark = pytest.mark.skipif(CXX is None, reason="no host C++ compiler")

# (current, max, increase, wrap, lowest_off) -> target
CASES = [
    # Increase
    (0, 3, True, False, True, 1),
    (1, 3, True, False, True, 2),
    (3, 3, True, False, True, 3),  # stays at the top
    (3, 3, True, True, True, 0),  # wraps to Off
    (3, 3, True, True, False, 1),  # wraps to the lowest speed (Off is not stepped to)
    (0, 3, True, False, False, 1),  # off fan turns on at speed 1
    # Decrease
    (3, 3, False, False, True, 2),
    (1, 3, False, False, True, 0),  # lowestOff: down to Off
    (1, 3, False, False, False, 1),  # stays at the lowest speed
    (0, 3, False, False, True, 0),  # already off
    (0, 3, False, True, True, 3),  # wraps to the top
    (1, 3, False, True, False, 3),
    (0, 3, False, False, False, 0),  # a step down never turns the fan on
    (0, 3, False, True, False, 3),
    # Degenerate input is clamped
    (7, 3, True, False, True, 3),
    (-1, 3, True, False, True, 1),
    (2, 0, True, True, True, 2),  # no speeds: unchanged
    (1, 1, True, False, True, 1),
    (1, 1, True, True, True, 0),
]


def test_fan_step_target(tmp_path: Path):
    source = tmp_path / "step.cpp"
    lines = ['#include "matter_fan_step.h"', "#include <cstdio>", "int main() {"]
    for current, top, increase, wrap, lowest_off, _ in CASES:
        lines.append(
            "  std::printf(\"%d\\n\", esphome::matter::fan_step_target("
            f"{current}, {top}, {str(increase).lower()}, {str(wrap).lower()}, {str(lowest_off).lower()}));"
        )
    lines += ["  return 0;", "}"]
    source.write_text("\n".join(lines))
    exe = tmp_path / "step"
    build = subprocess.run(
        [CXX, "-std=c++17", "-Wall", "-Werror", f"-I{COMPONENTS / 'matter'}", str(source), "-o", str(exe)],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.split()
    assert [int(v) for v in out] == [case[-1] for case in CASES]
