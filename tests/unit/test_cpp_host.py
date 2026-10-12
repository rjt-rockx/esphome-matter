"""Build and run the host C++ tests in tests/cpp with the system g++.

The tests exercise pure logic (binding framework core, value conversions)
against tiny stubs of esp-matter / ESPHome in tests/cpp/stubs. No ESP-IDF, CHIP
or esp-matter headers are needed. Skipped when g++ is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CPP = REPO_ROOT / "tests" / "cpp"
COMPONENT = REPO_ROOT / "components" / "matter"

# Component sources compiled into the test binary. Add pure-logic files here.
COMPONENT_SOURCES = [
    "matter_binding.cpp",
    "matter_thread.cpp",
    "matter_conversions.cpp",
]
# ESPHome 2026 builds with gnu++20; also check the older C++17 floor.
STANDARDS = ["gnu++20", "c++17"]

GXX = shutil.which("g++") or shutil.which("c++")
pytestmark = pytest.mark.skipif(GXX is None, reason="no host C++ compiler (g++)")


def _build(out: Path, std: str) -> Path:
    exe = out / f"matter-host-tests-{std.replace('+', 'p')}"
    cmd = [
        GXX,
        f"-std={std}",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-DMATTER_HOST_TEST",
        "-pthread",
        f"-I{CPP / 'stubs'}",
        f"-I{CPP}",
        f"-I{COMPONENT}",
        *sorted(str(p) for p in CPP.glob("*.cpp")),
        *(str(COMPONENT / s) for s in COMPONENT_SOURCES),
        "-o",
        str(exe),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"host build failed ({std}):\n{result.stderr}"
    return exe


@pytest.mark.parametrize("std", STANDARDS)
def test_cpp_host_tests(tmp_path: Path, std: str) -> None:
    exe = _build(tmp_path, std)
    result = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 failed checks" in result.stdout
