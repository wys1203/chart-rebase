#!/usr/bin/env python3
"""Print local modifications vs the current vendor baseline."""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib


def main():
    ap = argparse.ArgumentParser(description="Show local mods vs vendor baseline")
    ap.add_argument("--chart", required=True)
    args = ap.parse_args()

    repo_root = Path.cwd()
    cfg = _lib.load_config(repo_root / "charts.json")
    entry = cfg["charts"].get(args.chart)
    if not entry:
        print(f"error: chart {args.chart} not in charts.json", file=sys.stderr)
        return 1

    tag = f"vendor/{args.chart}/{entry['version']}"
    result = subprocess.run(
        ["git", "-C", str(repo_root), "diff", tag, "HEAD", "--", f"{args.chart}/"],
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
