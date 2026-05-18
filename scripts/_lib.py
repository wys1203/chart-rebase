"""Shared utilities for chart-rebase scripts.

Pure stdlib. Wraps curl/git/tar via subprocess for I/O and external tooling.
"""

import json
from pathlib import Path
from typing import List, Optional, Tuple


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


def list_versions(index_yaml_text: str, chart_name: str) -> List[Tuple[str, str]]:
    """Parse a helm-generated index.yaml and return [(version, url), ...] for chart_name.

    Targeted line-by-line state machine. Assumes helm's standard 2-space-indent
    output with no flow style, anchors, or multi-line scalars in the captured
    fields. Order in the returned list matches order in the file.
    """
    out: List[Tuple[str, str]] = []
    in_entries = False
    in_target = False
    cur_version: Optional[str] = None
    cur_url: Optional[str] = None
    expecting_url_list = False

    def flush():
        nonlocal cur_version, cur_url
        if cur_version is not None:
            out.append((cur_version, cur_url or ""))
        cur_version, cur_url = None, None

    for raw in index_yaml_text.splitlines():
        line = raw.rstrip()
        if not in_entries:
            if line == "entries:":
                in_entries = True
            continue

        # New chart name section: "  <name>:" at column 2, not a list item
        if (
            line.startswith("  ")
            and not line.startswith("   ")
            and not line.startswith("  - ")
            and line.endswith(":")
        ):
            if in_target:
                flush()
            name = line.strip().rstrip(":")
            in_target = (name == chart_name)
            expecting_url_list = False
            continue

        if not in_target:
            continue

        # New version block: "  - apiVersion: ..." (or any "  - ..." line)
        # helm always puts version/urls on continuation lines, never on this marker
        if line.startswith("  - "):
            flush()
            expecting_url_list = False
            continue

        if line.startswith("    version: "):
            cur_version = line.split("version: ", 1)[1].strip().strip('"')
            continue

        if line.strip() == "urls:":
            expecting_url_list = True
            continue

        if expecting_url_list:
            if line.startswith("    - "):
                if cur_url is None:
                    cur_url = line.split("- ", 1)[1].strip()
                continue
            # Indent dropped back — leaving urls list
            if not line.startswith("    "):
                expecting_url_list = False

    if in_target:
        flush()
    return out


def resolve_url(repo_url: str, url: str) -> str:
    """If url is absolute, return as-is. Otherwise, join with repo_url."""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return repo_url.rstrip("/") + "/" + url.lstrip("/")
