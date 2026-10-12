"""Known-gap baseline.

Findings that come from the SDK (esp-matter / connectedhomeip) and cannot be
fixed in this repository are listed in ``data/known_gaps.json``. They are still
reported (level ``known``) but do not fail the run, so CI only breaks on new
problems. Each entry::

    {"code": "CL-ATTR-MISSING",            # exact finding code
     "where": "General Diagnostics",       # regex searched in `where`
     "message": "DeviceLoadStatus",        # optional regex searched in the message
     "reason": "...", "ref": "file:line"}
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .checks import Finding, Report

DEFAULT_BASELINE = Path(__file__).parent / "data" / "known_gaps.json"
KNOWN = "known"


def load(path: Path | None = None) -> list[dict]:
    path = path or DEFAULT_BASELINE
    if not path.exists():
        return []
    return json.loads(path.read_text())["gaps"]


def matches(entry: dict, f: Finding) -> bool:
    if entry.get("code") != f.code:
        return False
    if "where" in entry and not re.search(entry["where"], f.where):
        return False
    if "message" in entry and not re.search(entry["message"], f.message):
        return False
    return True


def apply(rep: Report, gaps: list[dict]) -> None:
    for f in rep.findings:
        if f.level in ("error", "warning"):
            for g in gaps:
                if matches(g, f):
                    f.message += f"  [known SDK gap: {g.get('reason', '')}]"
                    f.level = KNOWN
                    break
