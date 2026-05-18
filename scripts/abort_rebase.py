#!/usr/bin/env python3
"""Roll back an in-progress rebase."""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib


def find_inprogress_new_tag(repo_root: Path, chart: str, current_version: str):
    """Find the most recently created vendor/<chart>/<v> tag where v != current_version.

    Such a tag indicates an unfinished rebase. After a successful rebase chain
    there may be many historic tags; the newest one by creation time is the
    in-progress one (because rebase creates the tag just before applying mods).
    """
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
    ap = argparse.ArgumentParser(description="Roll back an in-progress rebase")
    ap.add_argument("--chart", required=True)
    args = ap.parse_args()

    repo_root = Path.cwd()
    cfg = _lib.load_config(repo_root / "charts.json")
    entry = cfg["charts"].get(args.chart)
    if not entry:
        print(f"error: chart {args.chart} not in charts.json", file=sys.stderr)
        return 1

    # Restore tracked files (chart dir + charts.json) to HEAD state
    print(f"restoring {args.chart}/ and charts.json from HEAD ...")
    subprocess.run(
        ["git", "-C", str(repo_root), "checkout", "HEAD", "--",
         f"{args.chart}/", "charts.json"],
        check=True,
    )
    # Remove any untracked files that the rebase introduced under <chart>/
    print(f"cleaning untracked files under {args.chart}/ ...")
    subprocess.run(
        ["git", "-C", str(repo_root), "clean", "-fd", "--", f"{args.chart}/"],
        check=True,
    )

    # Reload config (just restored from HEAD)
    cfg = _lib.load_config(repo_root / "charts.json")
    current_version = cfg["charts"][args.chart]["version"]
    new_tag = find_inprogress_new_tag(repo_root, args.chart, current_version)
    if new_tag:
        print(f"deleting in-progress vendor tag: {new_tag}")
        subprocess.run(
            ["git", "-C", str(repo_root), "tag", "-d", new_tag], check=True,
        )
    else:
        print("no in-progress vendor tag found (already clean?)")

    print(f"aborted. back at vendor/{args.chart}/{current_version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
