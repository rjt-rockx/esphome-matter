#!/usr/bin/env python3
"""Golden codegen snapshots: the byte-identical oracle for refactors.

For every ``tests/configs/*.yaml`` (``ci_tmp_*`` skipped) the config is prepared
like ``tests/validate.sh`` does (``.github/scripts/ci_prepare.py``, toolchain
chosen by the ESPHome version), ``esphome compile --only-generate`` runs, and two
files are written to ``tests/snapshots/``:

``<config>.main.txt``
    The statements of ``setup()`` in the generated ``src/main.cpp`` that
    reference ``matter`` / ``esp_matter`` / ``chip::`` or a ``matter_``
    variable (this includes the lambdas passed to ``register_endpoint``), in
    order. The YAML config-echo comment block and ``#line`` pragmas are
    stripped; absolute paths become ``<REPO>`` and the ESPHome version
    ``<ESPHOME_VERSION>``.
``<config>.sdkconfig.txt``
    The sorted ``CONFIG_*`` lines of the generated sdkconfig matching
    ``ESP_MATTER_ SUPPORT_ CHIP_ ENABLE_ USE_ *MATTER*``, path-normalised.

Regenerate everything with one command (the snapshots are ESPHome *dev* output;
use the same ESPHome as CI):

    /path/to/venv/bin/python tools/snapshot_codegen.py --update

Other flags: ``--only NAME`` (repeatable, config stem, globs allowed), ``--diff``
(default when no mode is given: print a unified diff per changed snapshot and
exit 1), ``--list``. The ESPHome binary is taken from ``$ESPHOME``, else
``esphome`` on PATH, else ``python -m esphome``.
"""

from __future__ import annotations

import argparse
import difflib
import fnmatch
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFIGS = REPO / "tests" / "configs"
SNAPSHOTS = REPO / "tests" / "snapshots"
PREPARE = REPO / ".github" / "scripts" / "ci_prepare.py"

# a setup() statement is kept when it matches this
MATTER_REF = re.compile(r"(?<![A-Za-z0-9])matter(?:::|_)|esp_matter|chip::")
# sdkconfig lines kept
SDK_KEEP = re.compile(r"^CONFIG_(?:ESP_MATTER_|SUPPORT_|CHIP_|ENABLE_|USE_|\w*MATTER)")


def esphome_cmd() -> list[str]:
    exe = os.environ.get("ESPHOME") or shutil.which("esphome")
    return [exe] if exe else [sys.executable, "-m", "esphome"]


def esphome_version() -> str:
    out = subprocess.run(
        [*esphome_cmd(), "version"], capture_output=True, text=True, check=True
    ).stdout
    m = re.search(r"\d+\.\d+\.\d+\S*", out)
    if not m:
        raise SystemExit(f"cannot parse `esphome version` output: {out!r}")
    return m.group(0)


def toolchain_for(version: str) -> str:
    """Same rule as tests/validate.sh: PlatformIO before 2026.9, native ESP-IDF after."""
    m = re.match(r"(\d+)\.(\d+)", version)
    return "platformio" if m and (int(m[1]), int(m[2])) < (2026, 9) else "esp-idf"


def normalise(text: str, version: str) -> str:
    text = text.replace(str(REPO), "<REPO>")
    if version:
        text = text.replace(version, "<ESPHOME_VERSION>")
    return text


def setup_body(main_cpp: str) -> list[str]:
    lines = main_cpp.splitlines()
    start = next(
        (i for i, ln in enumerate(lines) if ln.startswith("void setup()")), None
    )
    if start is None:
        raise SystemExit("no setup() in generated main.cpp")
    body: list[str] = []
    for ln in lines[start + 1 :]:
        if ln == "}":
            break
        body.append(ln)
    return body


def extract_main(main_cpp: str, version: str) -> str:
    kept: list[list[str]] = []
    stmt: list[str] = []

    def flush() -> None:
        nonlocal stmt
        if stmt and MATTER_REF.search("\n".join(stmt)):
            kept.append(stmt)
        stmt = []

    for ln in setup_body(main_cpp):
        stripped = ln.strip()
        # YAML config echo ("  // key: value") and #line pragmas
        if not stripped or stripped.startswith(("//", "#line")):
            continue
        indent = len(ln) - len(ln.lstrip())
        if indent <= 2 and not stripped.startswith("}"):
            flush()
        stmt.append(ln)
    flush()
    text = "\n".join("\n".join(s) for s in kept) + "\n"
    return normalise(text, version)


def find_sdkconfig(build_dir: Path, name: str) -> Path:
    """ESPHome writes sdkconfig.<name> into the build dir (native IDF toolchain; PlatformIO
    builds keep it in the build dir or under .pioenvs/<name>/ on older releases)."""
    candidates = [
        build_dir / f"sdkconfig.{name}",
        build_dir / ".pioenvs" / name / f"sdkconfig.{name}",
        build_dir / "sdkconfig.defaults",
    ]
    for c in candidates:
        if c.is_file():
            return c
    found = sorted(
        p
        for p in build_dir.glob("sdkconfig*")
        if p.is_file() and not p.name.endswith(".old")
    )
    if found:
        return found[0]
    raise SystemExit(f"no sdkconfig found in {build_dir}")


def extract_sdkconfig(text: str, version: str) -> str:
    lines = {ln.strip() for ln in text.splitlines() if SDK_KEEP.match(ln.strip())}
    return normalise("\n".join(sorted(lines)) + "\n", version)


def configs(only: list[str]) -> list[Path]:
    paths = sorted(
        p for p in CONFIGS.glob("*.yaml") if not p.name.startswith("ci_tmp_")
    )
    paths = [p for p in paths if p.name != "secrets.yaml"]
    if only:
        paths = [p for p in paths if any(fnmatch.fnmatch(p.stem, o) for o in only)]
        if not paths:
            raise SystemExit(f"--only {only}: no matching config in {CONFIGS}")
    return paths


def _is_tracked(path: Path) -> bool:
    cmd = ["git", "ls-files", "--error-unmatch", str(path.relative_to(REPO))]
    return (
        subprocess.run(cmd, cwd=REPO, capture_output=True, check=False).returncode == 0
    )


def generate(config: Path, version: str | None = None) -> tuple[str, str]:
    """Return (main_text, sdkconfig_text) for one tests/configs YAML."""
    version = version or esphome_version()
    prepared_rel = subprocess.run(
        [
            sys.executable,
            str(PREPARE),
            str(config.relative_to(REPO)),
            "--toolchain",
            toolchain_for(version),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    prepared = REPO / prepared_rel
    try:
        name = re.search(r"^\s+name:\s*(\S+)", prepared.read_text(), re.MULTILINE)[1]
        build = prepared.parent / ".esphome" / "build" / name
        # Fresh build dir so no stale sdkconfig/main.cpp can leak in.
        shutil.rmtree(build, ignore_errors=True)
        proc = subprocess.run(
            [*esphome_cmd(), "compile", "--only-generate", str(prepared)],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        if proc.returncode != 0:
            raise SystemExit(
                f"{config.name}: esphome compile --only-generate failed\n"
                + (proc.stdout + proc.stderr)[-3000:]
            )
        main = extract_main((build / "src" / "main.cpp").read_text(), version)
        sdk = extract_sdkconfig(find_sdkconfig(build, name).read_text(), version)
        return main, sdk
    finally:
        prepared.unlink(missing_ok=True)
        secrets = config.parent / "secrets.yaml"
        if secrets.exists() and not _is_tracked(secrets):
            secrets.unlink()


def snapshot_paths(config: Path) -> tuple[Path, Path]:
    return (
        SNAPSHOTS / f"{config.stem}.main.txt",
        SNAPSHOTS / f"{config.stem}.sdkconfig.txt",
    )


def unified(path: Path, new: str) -> str:
    old = path.read_text() if path.exists() else ""
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path.relative_to(REPO)}",
            tofile=f"b/{path.relative_to(REPO)} (generated)",
        )
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    ap.add_argument("--update", action="store_true", help="rewrite tests/snapshots/*")
    ap.add_argument(
        "--diff", action="store_true", help="show diffs, exit 1 on any (default)"
    )
    ap.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="NAME",
        help="config stem (glob ok), repeatable",
    )
    ap.add_argument("--list", action="store_true", help="list configs and exit")
    args = ap.parse_args()

    todo = configs(args.only)
    if args.list:
        print("\n".join(p.stem for p in todo))
        return 0
    version = esphome_version()
    print(
        f"ESPHome {version}, toolchain {toolchain_for(version)}, {len(todo)} config(s)",
        file=sys.stderr,
    )

    changed = 0
    SNAPSHOTS.mkdir(exist_ok=True)
    for config in todo:
        main_txt, sdk_txt = generate(config, version)
        for path, new in zip(snapshot_paths(config), (main_txt, sdk_txt)):
            old = path.read_text() if path.exists() else None
            if old == new:
                continue
            changed += 1
            if args.update:
                path.write_text(new)
                print(f"updated {path.relative_to(REPO)}")
            else:
                sys.stdout.write(
                    unified(path, new) or f"missing snapshot {path.relative_to(REPO)}\n"
                )
    if args.update:
        if not args.only:  # drop snapshots of configs that no longer exist
            keep = {p.name for c in todo for p in snapshot_paths(c)}
            for stale in SNAPSHOTS.glob("*.txt"):
                if stale.name not in keep:
                    stale.unlink()
                    print(f"removed {stale.relative_to(REPO)}")
        print(f"{changed} snapshot file(s) changed", file=sys.stderr)
        return 0
    print(f"{changed} snapshot file(s) differ", file=sys.stderr)
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
