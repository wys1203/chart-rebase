#!/usr/bin/env python3
"""List all charts: current version, dirty state, in-progress rebases."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib


def chart_dirty(repo_root: Path, chart: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain", "--",
         f"{chart}/"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip() != ""


def in_progress_tag(repo_root: Path, chart: str, current_version: str):
    sorted_tags = subprocess.run(
        ["git", "-C", str(repo_root), "for-each-ref",
         "--sort=-creatordate", "--format=%(refname:short)",
         f"refs/tags/vendor/{chart}/"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    current_tag = f"vendor/{chart}/{current_version}"
    for t in sorted_tags:
        if t and t != current_tag:
            return t
    return None


def main():
    repo_root = Path.cwd()
    cfg = _lib.load_config(repo_root / "charts.json")
    charts = cfg["charts"]
    if not charts:
        print("(no charts adopted yet — run `make adopt ...`)")
        return 0

    name_w = max(len(n) for n in charts) + 2
    ver_w = max(len(c["version"]) for c in charts.values()) + 2
    print(f"{'CHART'.ljust(name_w)}{'VERSION'.ljust(ver_w)}STATE")
    for name in sorted(charts):
        entry = charts[name]
        version = entry["version"]
        states = []
        if chart_dirty(repo_root, name):
            states.append("dirty")
        in_prog = in_progress_tag(repo_root, name, version)
        if in_prog:
            states.append(f"rebase-in-progress->{in_prog.split('/')[-1]}")
        state = ", ".join(states) if states else "clean"
        print(f"{name.ljust(name_w)}{version.ljust(ver_w)}{state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
