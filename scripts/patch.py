#!/usr/bin/env python3
"""Emit local modifications as a patch.

Default: single squashed diff to stdout.
--split: per-commit patches via git format-patch into .work/patches/<chart>/.
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib


def main():
    ap = argparse.ArgumentParser(description="Emit local mods as patch(es)")
    ap.add_argument("--chart", required=True)
    ap.add_argument("--split", action="store_true",
                    help="Use git format-patch (one file per commit)")
    args = ap.parse_args()

    repo_root = Path.cwd()
    _lib.ensure_workspace(repo_root)
    cfg = _lib.load_config(repo_root / "charts.json")
    entry = cfg["charts"].get(args.chart)
    if not entry:
        print(f"error: chart {args.chart} not in charts.json", file=sys.stderr)
        return 1

    tag = f"vendor/{args.chart}/{entry['version']}"

    if args.split:
        out_dir = repo_root / ".work" / "patches" / args.chart
        if out_dir.exists():
            for p in out_dir.iterdir():
                if p.is_file():
                    p.unlink()
        out_dir.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "-C", str(repo_root), "format-patch", f"{tag}..HEAD",
             "-o", str(out_dir), "--", f"{args.chart}/"],
            check=False, capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            return result.returncode
        print(result.stdout.strip())
        return 0

    # Default: squashed diff to stdout
    result = subprocess.run(
        ["git", "-C", str(repo_root), "diff", tag, "HEAD", "--", f"{args.chart}/"],
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
