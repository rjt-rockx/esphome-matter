"""Golden codegen snapshots (see tools/snapshot_codegen.py).

Compares the Matter-relevant part of the generated ``main.cpp`` and sdkconfig of
every ``tests/configs/*.yaml`` with ``tests/snapshots/``. The snapshots are
ESPHome *dev* output, so other ESPHome versions are skipped unless
``SNAPSHOT_ANY_ESPHOME=1`` is set. ``UPDATE_SNAPSHOTS=1`` rewrites them.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
from conftest import CONFIGS, REPO_ROOT

pytestmark = pytest.mark.slow

_spec = importlib.util.spec_from_file_location(
    "snapshot_codegen", REPO_ROOT / "tools" / "snapshot_codegen.py"
)
snap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(snap)

UPDATE = os.environ.get("UPDATE_SNAPSHOTS") == "1"
CONFIG_FILES = snap.configs([])


@pytest.fixture(scope="module")
def version() -> str:
    v = snap.esphome_version()
    if "dev" not in v and not UPDATE and os.environ.get("SNAPSHOT_ANY_ESPHOME") != "1":
        pytest.skip(f"snapshots are ESPHome dev output (installed: {v})")
    return v


@pytest.mark.parametrize("config", CONFIG_FILES, ids=lambda p: p.stem)
def test_snapshot(config: Path, version: str):
    main_txt, sdk_txt = snap.generate(config, version)
    failures = []
    for path, new in zip(snap.snapshot_paths(config), (main_txt, sdk_txt)):
        if UPDATE:
            path.parent.mkdir(exist_ok=True)
            path.write_text(new)
        elif not path.exists():
            failures.append(f"missing snapshot {path.relative_to(REPO_ROOT)}")
        elif path.read_text() != new:
            failures.append(snap.unified(path, new))
    assert not failures, (
        "generated code differs from the golden snapshot. If intended, regenerate with\n"
        "  python tools/snapshot_codegen.py --update   (or UPDATE_SNAPSHOTS=1 pytest ...)\n\n"
        + "\n".join(failures)
    )


def test_every_snapshot_has_a_config():
    stems = {p.stem for p in CONFIGS.glob("*.yaml") if not p.name.startswith("ci_tmp_")}
    for f in snap.SNAPSHOTS.glob("*.txt"):
        assert f.name.split(".")[0] in stems, f"stale snapshot {f.name}"
