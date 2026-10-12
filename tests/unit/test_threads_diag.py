"""Boot diagnostics (named build steps) and main loop lock discipline."""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "components" / "matter"
SNAPSHOTS = ROOT / "tests" / "snapshots"

# Files that may call the CHIP lock APIs, with the reason. Everything else must
# hop with run_on_matter_thread()/ScheduleWork (the binding framework, PushScope
# and MatterPushGuard all do) or use BoundedChipLock.
LOCK_ALLOWED = {
    "matter_chip_lock.h": "BoundedChipLock (diagnostics only, bounded wait)",
}
LOCK_PATTERN = re.compile(r"\b(LockChipStack|ScopedChipStackLock)\b")


def test_generated_build_callbacks_name_the_failing_step():
    snapshots = sorted(SNAPSHOTS.glob("*.main.txt"))
    assert snapshots
    for path in snapshots:
        text = path.read_text()
        assert "return false;" not in text, f"{path.name}: unnamed build failure"
    assert any('MATTER_BUILD_FAIL("endpoint ' in p.read_text() for p in snapshots)


def test_python_generators_do_not_emit_bare_failures():
    for path in COMPONENT.glob("*.py"):
        assert "return false" not in path.read_text(), path.name


def test_main_loop_does_not_take_the_chip_lock():
    offenders = []
    for path in sorted(COMPONENT.iterdir()):
        if path.suffix not in (".cpp", ".h") or path.name in LOCK_ALLOWED:
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if LOCK_PATTERN.search(line) and not line.lstrip().startswith("//"):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, "blocking CHIP lock on a non-CHIP thread:\n" + "\n".join(
        offenders
    )
