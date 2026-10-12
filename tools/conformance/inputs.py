"""Input loading shared by the CLI subcommands (check, diff, gallery)."""

from __future__ import annotations

import json
from pathlib import Path

from .espm import Profile
from .model import NodeModel, from_dump, from_layout
from .spec import Spec


def classify(path: Path) -> str:
    """``yaml`` | ``layout`` (MATTER_LAYOUT_JSON) | ``dump`` (any runtime dump flavour)."""
    if path.suffix in (".yaml", ".yml"):
        return "yaml"
    data = json.loads(path.read_text())
    if isinstance(data, dict) and data.get("format") == 1 and "endpoints" in data:
        first = next(iter(data["endpoints"].values()), {})
        if isinstance(first, dict) and "device_types" in first:
            return "layout"
    return "dump"


def load_model(
    path: Path,
    spec: Spec,
    profile: Profile,
    keep_layout: str | None = None,
) -> tuple[NodeModel, str]:
    """Return the node model of a YAML / layout / dump file and the input kind."""
    kind = classify(path)
    if kind == "yaml":
        from .layout import extract_layout

        return from_layout(extract_layout(path, keep=keep_layout), spec, profile), kind
    if kind == "layout":
        return from_layout(path, spec, profile), kind
    return from_dump(path, spec), kind
