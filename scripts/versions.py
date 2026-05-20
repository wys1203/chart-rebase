#!/usr/bin/env python3
"""List the chart versions available in an upstream Helm repository."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib


def main():
    ap = argparse.ArgumentParser(description="List upstream chart versions")
    ap.add_argument("--chart")
    ap.add_argument("--repo")
    ap.add_argument("--name")
    args = ap.parse_args()

    repo_root = Path.cwd()

    if args.chart:
        cfg = _lib.load_config(repo_root / "charts.json")
        entry = cfg["charts"].get(args.chart)
        if not entry:
            print(f"error: chart {args.chart} not in charts.json", file=sys.stderr)
            return 1
        repo_url = entry["repo"]
        chart_name = entry["name"]
    elif args.repo and args.name:
        repo_url = args.repo
        chart_name = args.name
    else:
        print("usage: versions.py --chart <dir> | --repo <url> --name <chart>",
              file=sys.stderr)
        return 1

    print("fetching index.yaml ...")
    idx = _lib.fetch_index(repo_url)
    versions = _lib.list_versions_detailed(idx, chart_name)
    if not versions:
        print(f"error: no versions found for chart {chart_name}", file=sys.stderr)
        return 1

    width = max(len("VERSION"), max(len(v.version) for v in versions))
    print(f"{'VERSION':<{width}}  APP VERSION")
    for v in versions:
        print(f"{v.version:<{width}}  {v.app_version}")
    print(f"{len(versions)} versions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
