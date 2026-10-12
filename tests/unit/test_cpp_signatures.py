"""Guard against by-value Action/Condition overrides (ESPHome >= 2026.1 uses const refs)."""

import re
from pathlib import Path

COMPONENTS = Path(__file__).resolve().parents[2] / "components"
BY_VALUE = re.compile(r"\b(play|check|play_complex|is_running)\s*\(\s*Ts\s*\.\.\.")


def test_no_by_value_ts_overrides():
    offenders = []
    for path in COMPONENTS.rglob("*"):
        if path.suffix not in (".h", ".cpp", ".hpp", ".tpp"):
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if BY_VALUE.search(line):
                offenders.append(f"{path.relative_to(COMPONENTS)}:{n}: {line.strip()}")
    assert not offenders, "use const Ts &...x:\n" + "\n".join(offenders)
