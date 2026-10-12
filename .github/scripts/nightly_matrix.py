#!/usr/bin/env python3
"""Print the nightly compile matrix: one entry per ``tests/configs/*.yaml``.

Usage: nightly_matrix.py [FILTER]

Prints a JSON list of ``{"name", "config", "variant"}`` objects (and, when
``$GITHUB_OUTPUT`` is set, appends ``configs=<json>`` to it). ``name`` is the
file stem and ``config`` the repository relative path. ``variant`` is the ESP32
variant the config compiles for: the ``esp32_variant`` substitution, else a
literal ``esp32: variant:``, else ESP32C6. It only feeds the cache keys, since
the compile step passes it as ``-s esp32_variant`` and configs that hard-code
their variant ignore it.

Optional FILTER keeps only configs whose stem contains the text. Temporary
files (``ci_tmp_*``) and ``secrets.yaml`` are skipped, like in tests/validate.sh.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS = REPO_ROOT / "tests" / "configs"

SUBSTITUTION_RE = re.compile(r"^\s+esp32_variant:\s*(ESP32\w*)\s*$", re.MULTILINE)
LITERAL_RE = re.compile(r"^\s+variant:\s*(ESP32\w*)\s*$", re.MULTILINE)
DEFAULT_VARIANT = "ESP32C6"


def variant_of(text: str) -> str:
    for pattern in (SUBSTITUTION_RE, LITERAL_RE):
        match = pattern.search(text)
        if match:
            return match.group(1)
    return DEFAULT_VARIANT


def build_matrix(name_filter: str = "") -> list[dict[str, str]]:
    entries = []
    for path in sorted(CONFIGS.glob("*.yaml")):
        if path.name.startswith("ci_tmp_") or path.name == "secrets.yaml":
            continue
        if name_filter and name_filter not in path.stem:
            continue
        entries.append(
            {
                "name": path.stem,
                "config": path.relative_to(REPO_ROOT).as_posix(),
                "variant": variant_of(path.read_text()),
            }
        )
    return entries


def main() -> int:
    entries = build_matrix(sys.argv[1] if len(sys.argv) > 1 else "")
    if not entries:
        print("no configs matched", file=sys.stderr)
        return 1
    payload = json.dumps(entries, separators=(",", ":"))
    print(payload)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as handle:
            handle.write(f"configs={payload}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
