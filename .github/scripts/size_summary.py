#!/usr/bin/env python3
"""Append firmware size information to the GitHub job summary (or stdout).

Usage: size_summary.py COMPILE_LOG BUILD_DIR TITLE

Reads the ``RAM:``/``Flash:`` lines ESPHome prints after a build and lists the
sizes of the produced ``firmware*.bin`` images.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

LINE_RE = re.compile(
    r"(RAM|Flash):\s*\[[^\]]*\]\s+([\d.]+)%\s+\(used (\d+) bytes from (\d+) bytes\)"
)


def main() -> int:
    log, build_dir, title = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    out = [f"### Firmware size: {title}\n"]
    found: dict[str, tuple[str, str, str]] = {}
    if log.exists():
        for m in LINE_RE.finditer(log.read_text(errors="replace")):
            found[m.group(1)] = (m.group(2), m.group(3), m.group(4))
    if found:
        out.append("| Region | Used | Total | % |\n|---|---:|---:|---:|")
        for name in ("Flash", "RAM"):
            if name in found:
                pct, used, total = found[name]
                out.append(f"| {name} | {int(used):,} B | {int(total):,} B | {pct}% |")
    else:
        out.append("_No size line found in the compile log (build failed?)._")
    bins = sorted(build_dir.glob("**/firmware*.bin")) if build_dir.exists() else []
    bins = [b for b in bins if "managed_components" not in b.parts][:6]
    if bins:
        out.append("\n| Image | Size |\n|---|---:|")
        for b in bins:
            out.append(f"| `{b.name}` | {b.stat().st_size:,} B |")
    text = "\n".join(out) + "\n"
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a") as fh:
            fh.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
