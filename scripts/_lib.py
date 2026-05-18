"""Shared utilities for chart-rebase scripts.

Pure stdlib. Wraps curl/git/tar via subprocess for I/O and external tooling.
"""

import json
from pathlib import Path


def load_config(path: Path) -> dict:
    """Read charts.json. Return {'charts': {}} if file is missing."""
    if not path.exists():
        return {"charts": {}}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if "charts" not in data:
        data["charts"] = {}
    return data


def save_config(path: Path, config: dict) -> None:
    """Write charts.json pretty-printed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, sort_keys=True)
        f.write("\n")
