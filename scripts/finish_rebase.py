#!/usr/bin/env python3
"""Finalize a rebase: verify no conflict markers, commit applied result."""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib


CONFLICT_MARKERS = ("<<<<<<< ", "=======\n", ">>>>>>> ")


def find_conflict_files(chart_dir: Path):
    out = []
    for p in chart_dir.rglob("*"):
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "<<<<<<< " in text:
            out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser(description="Finalize an in-progress rebase")
    ap.add_argument("--chart", required=True)
    args = ap.parse_args()

    repo_root = Path.cwd()
    cfg = _lib.load_config(repo_root / "charts.json")
    entry = cfg["charts"].get(args.chart)
    if not entry:
        print(f"error: chart {args.chart} not in charts.json", file=sys.stderr)
        return 1

    chart_dir = repo_root / args.chart
    conflicts = find_conflict_files(chart_dir)
    if conflicts:
        print("error: unresolved conflict markers in:", file=sys.stderr)
        for p in conflicts:
            print(f"  {p.relative_to(repo_root)}", file=sys.stderr)
        return 1

    new_version = entry["version"]
    subprocess.run(
        ["git", "-C", str(repo_root), "add", f"{args.chart}/", "charts.json"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "commit",
         "-m", f"chore({args.chart}): rebase onto upstream {new_version}"],
        check=True,
    )
    print(f"finished. now at vendor/{args.chart}/{new_version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
