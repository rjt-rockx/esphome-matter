#!/usr/bin/env python3
"""Prepare an ESPHome YAML for CI/local runs and print the path of the copy.

The original file is never modified. A sibling copy named ``ci_tmp_<name>.yaml``
is written next to it (so relative paths keep working) with:

* ``source: github://<owner>/esphome-matter@<ref>`` and the multi-line
  ``type: git`` form pointing at an esphome-matter repository rewritten to the
  local ``components/`` directory, so CI exercises the code under test and not
  the upstream default branch.
* optionally (``--toolchain esp-idf``) ``toolchain: platformio`` replaced by
  ``toolchain: esp-idf`` so the native ESP-IDF toolchain is used. With
  ``--toolchain keep`` (default) the toolchain line is left untouched.

A ``secrets.yaml`` with dummy values is created next to the config for every
``!secret <key>`` that is not defined yet (never overwrites existing keys).

Usage: ci_prepare.py CONFIG [--toolchain keep|esp-idf] [--no-local-components]
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
    ap.add_argument("--toolchain", choices=("keep", "esp-idf"), default="keep")
    ap.add_argument("--no-local-components", action="store_true")
    args = ap.parse_args()

    src = Path(args.config).resolve()
    text = src.read_text()
    ensure_secrets(src.parent, text)

    if not args.no_local_components:
        rel = os.path.relpath(COMPONENTS, src.parent)
        text = GITHUB_SRC_RE.sub(f"source: {rel}", text)
        text = GIT_SRC_RE.sub(lambda m: f"source: {rel}\n", text)
    if args.toolchain == "esp-idf":
        text = re.sub(
            r"^(\s*toolchain:\s*)platformio\b", r"\1esp-idf", text, flags=re.MULTILINE
        )

    dst = src.with_name(f"ci_tmp_{src.name}")
    dst.write_text(text)
    print(os.path.relpath(dst))


if __name__ == "__main__":
    main()
