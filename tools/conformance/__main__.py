"""``python -m tools.conformance <yaml | layout.json | dump.json> ...``

Checks whether an esphome-matter configuration (or a runtime wildcard-read dump)
yields a Matter data model that conforms to the specification. Exit status 1 on
errors (or on warnings with --strict), 0 otherwise.

Subcommands: ``diff <ours> <reference>`` (structural diff against a real device dump or
catalog id) and ``gallery <dir>`` (diff every YAML with a ``# mirrors: <id>`` header);
see ``diff.py`` and docs/conformance.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import baseline as baseline_mod
from .checks import ERROR, INFO, WARNING, Report, check_node
from .compat import check_compat
from .espm import Profile
from .inputs import classify
from .model import from_dump, from_layout, to_dump
from .spec import DEFAULT_VERSION, Spec

KNOWN = baseline_mod.KNOWN


def run_one(
    path: Path,
    spec: Spec,
    profile: Profile,
    compat: bool,
    gaps: list[dict],
    keep_layout: str | None = None,
    emit_dump: Path | None = None,
) -> Report:
    kind = classify(path)
    if kind == "yaml":
        from .layout import extract_layout

        layout = extract_layout(path, keep=keep_layout)
        model = from_layout(layout, spec, profile)
    elif kind == "layout":
        model = from_layout(path, spec, profile)
    else:
        model = from_dump(path, spec)
    if emit_dump is not None and model.mode == "layout":
        emit_dump.write_text(json.dumps(to_dump(model, spec), indent=1) + "\n")
    rep = check_node(model, spec)
    if compat:
        check_compat(model, spec, rep)
    baseline_mod.apply(rep, gaps)
    rep.stats["input"] = str(path)
    rep.stats["kind"] = kind
    return rep


def render(rep: Report, verbose: bool) -> str:
    lines = [
        f"== {rep.stats.get('input')} [{rep.stats.get('mode')} mode, spec {rep.stats.get('spec')}] "
        f"{rep.count(ERROR)} error(s), {rep.count(WARNING)} warning(s), {rep.count(KNOWN)} known SDK gap(s)"
    ]
    order = {ERROR: 0, WARNING: 1, KNOWN: 2, INFO: 3}
    for f in sorted(rep.findings, key=lambda f: (order[f.level], f.where, f.code)):
        if f.level == INFO and not verbose:
            continue
        lines.append(f"  {f.level.upper():7} {f.code:22} {f.where}: {f.message}")
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("diff", "gallery"):
        from . import diff as diff_mod

        return diff_mod.main(argv)
    ap = argparse.ArgumentParser(
        prog="python -m tools.conformance", description=__doc__
    )
    ap.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="ESPHome YAML, MATTER_LAYOUT_JSON layout, or runtime wildcard-read dump",
    )
    ap.add_argument(
        "--spec-version",
        default=DEFAULT_VERSION,
        help="data model version directory (default %(default)s)",
    )
    ap.add_argument(
        "--spec-dir",
        type=Path,
        help="data_model directory (default: $MATTER_DATA_MODEL or the esp-matter checkout)",
    )
    ap.add_argument(
        "--json", type=Path, help="write the full result as JSON to this file"
    )
    ap.add_argument(
        "--strict", action="store_true", help="exit non-zero on warnings too"
    )
    ap.add_argument(
        "--no-compat", action="store_true", help="skip Home Assistant / Apple hints"
    )
    ap.add_argument(
        "--baseline",
        type=Path,
        help="known SDK gap list (default: data/known_gaps.json)",
    )
    ap.add_argument(
        "--no-baseline", action="store_true", help="report known SDK gaps as errors"
    )
    ap.add_argument(
        "-v", "--verbose", action="store_true", help="also print info findings"
    )
    ap.add_argument(
        "--emit-dump",
        type=Path,
        help="YAML/layout input: write the predicted model as a runtime-style dump",
    )
    ap.add_argument(
        "--keep-layout", help="for a YAML input, save the exported layout JSON here"
    )
    args = ap.parse_args(argv)

    spec = Spec(args.spec_dir, args.spec_version)
    profile = Profile()
    gaps = [] if args.no_baseline else baseline_mod.load(args.baseline)
    reports = []
    failed = False
    for path in args.inputs:
        try:
            rep = run_one(
                path,
                spec,
                profile,
                not args.no_compat,
                gaps,
                args.keep_layout,
                args.emit_dump,
            )
        except Exception as exc:  # noqa: BLE001 - report and continue with the next input
            print(f"== {path}: TOOL FAILURE: {exc}", file=sys.stderr)
            failed = True
            continue
        reports.append(rep)
        print(render(rep, args.verbose))
        if rep.count(ERROR) or (args.strict and rep.count(WARNING)):
            failed = True
    if args.json:
        args.json.write_text(
            json.dumps(
                [
                    dict(stats=r.stats, findings=[f.as_dict() for f in r.findings])
                    for r in reports
                ],
                indent=1,
            )
            + "\n"
        )
    total_e = sum(r.count(ERROR) for r in reports)
    total_w = sum(r.count(WARNING) for r in reports)
    print(f"\n{len(reports)} input(s): {total_e} error(s), {total_w} warning(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
