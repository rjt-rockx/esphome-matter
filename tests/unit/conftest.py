"""Shared fixtures for the Python-side unit tests (no hardware, no compiler).

Config validation runs ``esphome config`` in a subprocess on small generated
YAML files; it takes well under a second per config.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from esphome.core import CORE

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPONENTS = REPO_ROOT / "components"
CONFIGS = REPO_ROOT / "tests" / "configs"
DATA_MODEL_DIR = COMPONENTS / "matter" / "data_model"

# Make `components.matter...` importable for pure-Python tests.
sys.path.insert(0, str(REPO_ROOT))
# components/matter/__init__.py reads CORE.name at import time.
CORE.name = "matter-unit-tests"


def _esphome_cmd() -> list[str]:
    exe = os.environ.get("ESPHOME") or shutil.which("esphome")
    if exe:
        return [exe]
    return [sys.executable, "-m", "esphome"]


ESPHOME = _esphome_cmd()


def toolchain_line() -> str:
    """ESPHome < 2026.9 needs `toolchain: platformio`, newer uses native ESP-IDF."""
    from esphome.const import __version__

    m = re.match(r"(\d+)\.(\d+)", __version__)
    if m and (int(m[1]), int(m[2])) < (2026, 9):
        return "  toolchain: platformio\n"
    return ""


HEADER = """\
esphome:
  name: matter-unit

esp32:
  variant: ESP32C6
{toolchain}  framework:
    type: esp-idf

external_components:
  - source: {components}

logger:

api:

network:
  enable_ipv6: true
"""

WIFI = """
wifi:
  ssid: unit-test-network
  password: unit-test-password
"""


class Result:
    def __init__(self, proc: subprocess.CompletedProcess):
        self.returncode = proc.returncode
        self.output = (proc.stdout or "") + (proc.stderr or "")

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@pytest.fixture(autouse=True)
def reset_core():
    CORE.reset()
    CORE.name = "matter-unit-tests"
    yield
    CORE.reset()


@pytest.fixture
def run_config(tmp_path):
    """Return ``run(body, wifi=True) -> Result`` validating a generated config.

    ``body`` is the YAML after the standard header (matter:, switch:, ...).
    """

    def run(body: str, *, wifi: bool = True) -> Result:
        text = HEADER.format(toolchain=toolchain_line(), components=COMPONENTS)
        text += WIFI if wifi else ""
        text += textwrap.dedent(body)
        path = tmp_path / "unit.yaml"
        path.write_text(text)
        proc = subprocess.run(
            [*ESPHOME, "config", str(path)],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            timeout=300,
            check=False,
        )
        return Result(proc)

    return run


def prepare_config(src: Path, dst_dir: Path) -> Path:
    """Copy a tests/configs YAML into dst_dir with absolute component path and
    the toolchain line matching the installed ESPHome."""
    text = src.read_text()
    text = text.replace("../../components", str(COMPONENTS))
    text = re.sub(r"^  toolchain:.*\n", "", text, flags=re.MULTILINE)
    text = re.sub(
        r"^(esp32:\n)", lambda m: m[1] + toolchain_line(), text, flags=re.MULTILINE
    )
    dst = dst_dir / src.name
    dst.write_text(text)
    return dst


def run_esphome(*args: str, cwd: Path) -> Result:
    return Result(
        subprocess.run(
            [*ESPHOME, *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=900,
            check=False,
        )
    )


def config_files() -> list:
    """tests/configs/*.yaml without validate.sh's temporary files (ci_tmp_*, secrets.yaml)."""
    return sorted(
        p
        for p in CONFIGS.glob("*.yaml")
        if not p.name.startswith("ci_tmp_") and p.name != "secrets.yaml"
    )
