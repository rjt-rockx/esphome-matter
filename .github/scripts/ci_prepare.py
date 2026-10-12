#!/usr/bin/env python3
"""Prepare an ESPHome YAML for CI/local runs and print the path of the copy.

The original file is never modified. A sibling copy named ``ci_tmp_<name>.yaml``
is written next to it (so relative paths keep working) with:

* ``source: github://<owner>/esphome-matter@<ref>`` and the multi-line
  ``type: git`` form pointing at an esphome-matter repository rewritten to the
  local ``components/`` directory, so CI exercises the code under test and not
  the upstream default branch.
* optionally (``--toolchain esp-idf`` / ``--toolchain platformio``) the
  ``toolchain:`` option under ``esp32:`` is replaced, or inserted when absent,
  so the native ESP-IDF toolchain (ESPHome >= 2026.9.0 / dev) or PlatformIO
  (older ESPHome) is used. With ``--toolchain keep`` (default) the toolchain
  line is left untouched.

* remote ``packages:`` entries pointing at an esphome-matter repository (the
  ``url:``/``files:`` form, with optional ``vars``, or the
  ``github://owner/esphome-matter/<path>@ref`` shorthand) rewritten to local
  ``!include`` entries of the ``packages/`` directory in this checkout.

A ``secrets.yaml`` with dummy values is created next to the config for every
``!secret <key>`` that is not defined yet (never overwrites existing keys).

Usage: ci_prepare.py CONFIG [--toolchain keep|esp-idf|platformio] [--no-local-components]
"""

from __future__ import annotations

import argparse
import base64
import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPONENTS = REPO_ROOT / "components"

DUMMY_SECRETS = {
    "wifi_ssid": "ci-test-network",
    "wifi_password": "ci-test-password",
    # 32 hex-encoded bytes are enough for ESPHome's validator in CI
    "thread_tlv": "0e08000000000001000000030000134a0300001035060004001fffe00208dead00beef00cafe0708fd00000000000001051000112233445566778899aabbccddeeff030f4f70656e5468726561642d65737001021234",
    "api_encryption_key": base64.b64encode(b"0" * 32).decode(),
    "encryption_key": base64.b64encode(b"0" * 32).decode(),
    "ota_password": "ci-test-ota",
}

SECRET_RE = re.compile(r"!secret\s+([A-Za-z0-9_\-]+)")
GITHUB_SRC_RE = re.compile(r"source:\s*github://[^\s@/]+/esphome-matter(?:@\S+)?")
GIT_SRC_RE = re.compile(
    r"source:[ \t]*\n(?P<i>[ \t]+)type:[ \t]*git[ \t]*\n"
    r"(?:(?P=i)(?:url|ref|path|username|password):[^\n]*\n)+"
)


PKG_URL_RE = re.compile(
    r"^(?P<i>[ \t]*)url:[ \t]*https://github\.com/[^/\s]+/esphome-matter(?:\.git)?[ \t]*$"
)
PKG_SHORT_RE = re.compile(
    r"^(?P<i>[ \t]*)(?P<k>[\w\-]+):[ \t]*github://[^/\s]+/esphome-matter/(?P<p>[^@\s]+?)(?:@\S+)?[ \t]*$"
)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def rewrite_packages(text: str, src_dir: Path) -> str:
    """Rewrite remote esphome-matter ``packages:`` entries to local includes."""
    root = os.path.relpath(REPO_ROOT, src_dir)
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = PKG_SHORT_RE.match(line)
        if m:
            out.append(f"{m['i']}{m['k']}: !include {root}/{m['p']}")
            i += 1
            continue
        if PKG_URL_RE.match(line) and out:
            u = _indent(line)
            # the package key is the previous line (parent mapping key)
            key_line = out.pop()
            key = key_line.strip().rstrip(":")
            key_ind = " " * _indent(key_line)
            j = i + 1
            block: list[str] = []
            while j < len(lines) and (not lines[j].strip() or _indent(lines[j]) >= u):
                block.append(lines[j])
                j += 1
            entries: list[tuple[str, list[str]]] = []
            k = 0
            while k < len(block):
                b = block[k].strip()
                if b.startswith("- path:") or (b.startswith("- ") and ":" not in b):
                    path = b[2:].replace("path:", "", 1).strip()
                    ind = _indent(block[k])
                    k += 1
                    vars_: list[str] = []
                    while k < len(block) and (
                        not block[k].strip() or _indent(block[k]) > ind
                    ):
                        vars_.append(block[k])
                        k += 1
                    entries.append((path, vars_))
                else:
                    k += 1
            for n, (path, vars_) in enumerate(entries):
                name = key if len(entries) == 1 else f"{key}_{n}"
                vlines = [v for v in vars_ if v.strip() and v.strip() != "vars:"]
                if vlines:
                    base = min(_indent(v) for v in vlines)
                    out.append(f"{key_ind}{name}: !include")
                    out.append(f"{key_ind}  file: {root}/{path}")
                    out.append(f"{key_ind}  vars:")
                    out.extend(f"{key_ind}    {v[base:]}" for v in vlines)
                else:
                    out.append(f"{key_ind}{name}: !include {root}/{path}")
            while j > i and not lines[j - 1].strip():
                j -= 1  # keep trailing blank lines
            i = j
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def set_toolchain(text: str, toolchain: str) -> str:
    """Set ``toolchain:`` under the top-level ``esp32:`` block (insert if absent)."""
    text, n = re.subn(
        r"^(\s*toolchain:\s*)[\w\-]+", rf"\g<1>{toolchain}", text, flags=re.MULTILINE
    )
    if n:
        return text
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if re.match(r"^esp32:\s*(#.*)?$", line):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            ind = " " * (_indent(lines[j]) if j < len(lines) else 2) or "  "
            lines.insert(i + 1, f"{ind}toolchain: {toolchain}")
            break
    return "\n".join(lines)


def ensure_secrets(directory: Path, text: str) -> None:
    keys = sorted(set(SECRET_RE.findall(text)))
    if not keys:
        return
    path = directory / "secrets.yaml"
    existing = path.read_text() if path.exists() else ""
    missing = [
        k
        for k in keys
        if not re.search(rf"^{re.escape(k)}\s*:", existing, re.MULTILINE)
    ]
    if not missing:
        return
    lines = [f'{k}: "{DUMMY_SECRETS.get(k, "ci-dummy-" + k)}"\n' for k in missing]
    with path.open("a") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        fh.writelines(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--toolchain", choices=("keep", "esp-idf", "platformio"), default="keep")
    ap.add_argument("--no-local-components", action="store_true")
    args = ap.parse_args()

    src = Path(args.config).resolve()
    text = src.read_text()
    ensure_secrets(src.parent, text)

    if not args.no_local_components:
        rel = os.path.relpath(COMPONENTS, src.parent)
        text = GITHUB_SRC_RE.sub(f"source: {rel}", text)
        text = GIT_SRC_RE.sub(lambda m: f"source: {rel}\n", text)
        text = rewrite_packages(text, src.parent)
    if args.toolchain != "keep":
        text = set_toolchain(text, args.toolchain)

    dst = src.with_name(f"ci_tmp_{src.name}")
    dst.write_text(text)
    print(os.path.relpath(dst))


if __name__ == "__main__":
    main()
