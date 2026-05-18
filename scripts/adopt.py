#!/usr/bin/env python3
"""Adopt an existing chart: auto-detect base version, create orphan vendor tag."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib


def detect_chart_name(local_dir: Path, fallback):
    """Try to read name: from Chart.yaml. Fall back to provided value or dir name."""
    chart_yaml = local_dir / "Chart.yaml"
    if chart_yaml.exists():
        for line in chart_yaml.read_text(encoding="utf-8").splitlines():
            line = line.rstrip()
            if line.startswith("name:"):
                value = line.split("name:", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    return fallback or local_dir.name


def pick_version_interactive(candidates, chart_local_name):
    top = candidates[:3]
    print(f"\nDetected base version candidates for {chart_local_name}:")
    for i, (ver, score) in enumerate(top, 1):
        marker = "  ★ best match" if i == 1 else ""
        print(f"  {i}. {ver}  →  {score.total_lines} lines / "
              f"{score.changed_files} files changed{marker}")
    print()
    raw = input(f"Use which? (1/2/3, or q to quit) [1]: ").strip().lower()
    if raw == "q":
        return None
    if raw == "":
        raw = "1"
    if raw not in ("1", "2", "3") or int(raw) > len(top):
        print(f"Invalid selection: {raw}", file=sys.stderr)
        return None
    return top[int(raw) - 1][0]


def main():
    ap = argparse.ArgumentParser(description="Adopt an existing chart")
    ap.add_argument("--chart", required=True, help="Local directory name")
    ap.add_argument("--repo", required=True, help="Upstream helm chart repo URL")
    ap.add_argument("--name", help="Upstream chart name (default: from Chart.yaml or local dir)")
    ap.add_argument("--version", help="Skip auto-detect; use this version explicitly")
    args = ap.parse_args()

    repo_root = Path.cwd()
    _lib.ensure_workspace(repo_root)

    local_dir = repo_root / args.chart
    if not local_dir.is_dir():
        print(f"error: {local_dir} is not a directory", file=sys.stderr)
        return 1

    chart_name = args.name or detect_chart_name(local_dir, None)
    print(f"chart: local={args.chart} upstream={chart_name} repo={args.repo}")

    print("fetching index.yaml ...")
    idx = _lib.fetch_index(args.repo)
    versions = _lib.list_versions(idx, chart_name)
    if not versions:
        print(f"error: no versions found for chart {chart_name} in {args.repo}",
              file=sys.stderr)
        return 1
    print(f"found {len(versions)} versions in upstream index")

    if args.version:
        # Explicit version path
        match = [(v, u) for (v, u) in versions if v == args.version]
        if not match:
            print(f"error: version {args.version} not in upstream index", file=sys.stderr)
            return 1
        chosen_version = args.version
        _, url = match[0]
        print(f"using explicit version: {chosen_version}")
        pristine = _lib.download_and_extract_chart(
            repo_root, args.repo, chart_name, chosen_version, url,
        )
    else:
        # Auto-detect: score every version
        print("downloading and scoring all upstream versions ...")
        scored = []
        for ver, url in versions:
            extracted = _lib.download_and_extract_chart(
                repo_root, args.repo, chart_name, ver, url,
            )
            score = _lib.diff_score(local_dir, extracted)
            scored.append((ver, score))
            print(f"  {ver}: {score.total_lines} lines / {score.changed_files} files")
        scored.sort(key=lambda x: (x[1].total_lines, x[1].changed_files))

        chosen_version = pick_version_interactive(scored, args.chart)
        if chosen_version is None:
            print("aborted", file=sys.stderr)
            return 1
        url = dict(versions)[chosen_version]
        pristine = _lib.download_and_extract_chart(
            repo_root, args.repo, chart_name, chosen_version, url,
        )

    print(f"creating orphan vendor commit for {args.chart} {chosen_version} ...")
    sha = _lib.make_orphan_vendor_commit(repo_root, args.chart, pristine, chosen_version)
    print(f"  commit: {sha}")
    print(f"  tag: vendor/{args.chart}/{chosen_version}")

    cfg_path = repo_root / "charts.json"
    cfg = _lib.load_config(cfg_path)
    cfg["charts"][args.chart] = {
        "repo": args.repo,
        "name": chart_name,
        "version": chosen_version,
    }
    _lib.save_config(cfg_path, cfg)
    print(f"updated charts.json")

    print()
    print(f"done. verify with: make diff CHART={args.chart}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
