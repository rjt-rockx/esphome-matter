"""Run ESPHome code generation and capture the layout export."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_PREPARE = REPO_ROOT / (".git" + "hub") / "scripts" / "ci_prepare.py"


class LayoutError(RuntimeError):
    pass


def _esphome_cmd() -> list[str]:
    exe = os.environ.get("ESPHOME") or shutil.which("esphome")
    if not exe and (REPO_ROOT.parent / "venv" / "bin" / "esphome").exists():
        exe = str(REPO_ROOT.parent / "venv" / "bin" / "esphome")
    return [exe] if exe else [sys.executable, "-m", "esphome"]


def _toolchain(esphome: list[str]) -> str:
    out = subprocess.run([*esphome, "version"], capture_output=True, text=True).stdout
    m = re.search(r"(\d+)\.(\d+)", out)
    return "platformio" if m and (int(m[1]), int(m[2])) < (2026, 9) else "esp-idf"


def extract_layout(yaml_path: str | Path, keep: str | None = None) -> dict:
    """``esphome compile --only-generate`` with ``MATTER_LAYOUT_JSON`` set.

    The YAML is first run through the repo's CI preparation script (local
    components, dummy secrets, toolchain line) when that script exists, on a
    sibling copy that is deleted afterwards.
    """
    yaml_path = Path(yaml_path).resolve()
    esphome = _esphome_cmd()
    created: list[Path] = []
    target = yaml_path
    if CI_PREPARE.exists():
        secrets = yaml_path.parent / "secrets.yaml"
        had_secrets = secrets.exists()
        res = subprocess.run(
            [
                sys.executable,
                str(CI_PREPARE),
                str(yaml_path),
                "--toolchain",
                _toolchain(esphome),
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        if res.returncode != 0:
            raise LayoutError(f"ci_prepare failed:\n{res.stderr}")
        target = Path(res.stdout.strip().splitlines()[-1])
        if not target.is_absolute():
            target = REPO_ROOT / target
        created.append(target)
        if not had_secrets and secrets.exists():
            created.append(secrets)
    tmp = Path(tempfile.mkdtemp(prefix="matter-conformance-"))
    try:
        out = tmp / "layout.json"
        env = dict(
            os.environ, MATTER_LAYOUT_JSON=str(out), ESPHOME_DATA_DIR=str(tmp / "data")
        )
        res = subprocess.run(
            [*esphome, "compile", "--only-generate", str(target)],
            capture_output=True,
            text=True,
            env=env,
            cwd=target.parent,
        )
        if res.returncode != 0 or not out.exists():
            tail = "\n".join((res.stdout + res.stderr).strip().splitlines()[-25:])
            raise LayoutError(
                f"esphome code generation failed for {yaml_path.name}:\n{tail}"
            )
        text = out.read_text()
        if keep:
            Path(keep).write_text(text)
        return json.loads(text)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for p in created:
            p.unlink(missing_ok=True)
