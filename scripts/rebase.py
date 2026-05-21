#!/usr/bin/env python3
"""Rebase a chart onto a new upstream version using git 3-way merge."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib


def working_tree_clean(repo_root: Path, paths):
    """Return True if the given paths have no staged or unstaged changes."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain", "--"] + list(paths),
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip() == ""


def tag_exists(repo_root: Path, tag: str) -> bool:
    out = subprocess.run(
        ["git", "-C", str(repo_root), "tag", "-l", tag],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return bool(out)


def main():
    ap = argparse.ArgumentParser(description="Rebase chart onto new upstream version")
    ap.add_argument("--chart", required=True)
    ap.add_argument("--version", required=True, help="New upstream version to rebase onto")
    args = ap.parse_args()

    repo_root = Path.cwd()
    _lib.ensure_workspace(repo_root)

    cfg_path = repo_root / "charts.json"
    cfg = _lib.load_config(cfg_path)
    entry = cfg["charts"].get(args.chart)
    if not entry:
        print(f"error: chart {args.chart} not in charts.json", file=sys.stderr)
        return 1

    old_version = entry["version"]
    new_version = args.version
    old_tag = f"vendor/{args.chart}/{old_version}"
    new_tag = f"vendor/{args.chart}/{new_version}"

    # Precondition: working tree clean for chart dir + charts.json
    if not working_tree_clean(repo_root, [f"{args.chart}/", "charts.json"]):
        print(f"error: working tree has changes under {args.chart}/ or charts.json",
              file=sys.stderr)
        return 1

    # Precondition: old vendor tag exists
    if not tag_exists(repo_root, old_tag):
        print(f"error: tag {old_tag} missing; run `make adopt` first",
              file=sys.stderr)
        return 1

    # Precondition: new vendor tag does NOT exist
    if tag_exists(repo_root, new_tag):
        print(f"error: tag {new_tag} already exists; rebase already started? "
              f"run `make abort-rebase CHART={args.chart}` first",
              file=sys.stderr)
        return 1

    # Step 1: capture squash diff
    patch_path = repo_root / ".work" / f"{args.chart}-localmods.patch"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"capturing squash diff: {old_tag} -> HEAD -- {args.chart}/ ...")
    with patch_path.open("w") as f:
        subprocess.run(
            ["git", "-C", str(repo_root), "diff", old_tag, "HEAD", "--", f"{args.chart}/"],
            stdout=f, check=True,
        )
    print(f"  -> {patch_path}")

    # Step 2: download new upstream
    print(f"fetching index.yaml ...")
    idx = _lib.fetch_index(entry["repo"])
    versions = _lib.list_versions(idx, entry["name"])
    match = [(v, u) for (v, u) in versions if v == new_version]
    if not match:
        print(f"error: version {new_version} not in upstream index", file=sys.stderr)
        return 1
    _, url = match[0]
    print(f"downloading {entry['name']} {new_version} ...")
    pristine = _lib.download_and_extract_chart(
        repo_root, entry["repo"], entry["name"], new_version, url,
    )

    # Step 3: build new orphan vendor commit + tag
    print(f"creating orphan vendor commit for {args.chart} {new_version} ...")
    sha = _lib.make_orphan_vendor_commit(repo_root, args.chart, pristine, new_version)
    print(f"  commit: {sha}")
    print(f"  tag: {new_tag}")

    # Step 4: replace working tree's <chart>/ with new pristine
    print(f"replacing {args.chart}/ with new pristine ...")
    target = repo_root / args.chart
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(pristine, target)
    # Stage the new pristine so `git apply --3way` sees a consistent index:
    # without this, git apply rejects every modified file with
    # "does not match index" and never reaches the 3-way merge.
    subprocess.run(
        ["git", "-C", str(repo_root), "add", "--", f"{args.chart}/"], check=True,
    )

    # Step 5: apply squash diff with 3-way merge (skip if patch is empty)
    if patch_path.stat().st_size == 0:
        print("no local modifications to apply (squash diff was empty)")
        apply_result = None
        has_conflicts = False
    else:
        print(f"applying local mods with 3-way merge ...")
        apply_result = subprocess.run(
            ["git", "-C", str(repo_root), "apply", "--3way",
             "--whitespace=nowarn", str(patch_path)],
            capture_output=True, text=True, check=False,
        )
        has_conflicts = "with conflicts" in apply_result.stderr.lower() or \
                        apply_result.returncode != 0

    # Restore the index for <chart>/ to HEAD. The staging above was only to
    # satisfy `git apply --3way`; leaving the index at HEAD keeps the merged
    # result purely in the working tree, so `make abort-rebase` can still
    # `git clean` the new upstream files and `make finish-rebase` stages the
    # final result itself.
    subprocess.run(
        ["git", "-C", str(repo_root), "reset", "-q", "HEAD", "--", f"{args.chart}/"],
        check=True,
    )

    # Step 6: update charts.json (stage it)
    cfg["charts"][args.chart]["version"] = new_version
    _lib.save_config(cfg_path, cfg)
    subprocess.run(
        ["git", "-C", str(repo_root), "add", "charts.json"], check=True,
    )
    if has_conflicts:
        print()
        print("=" * 60)
        print("CONFLICTS detected. Files with conflict markers:")
        # List files with conflict markers
        for path in target.rglob("*"):
            if path.is_file():
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if "<<<<<<< " in text:
                    print(f"  {path.relative_to(repo_root)}")
        print()
        print(f"Resolve conflicts, then run:")
        print(f"  make finish-rebase CHART={args.chart}")
        print(f"Or to roll back:")
        print(f"  make abort-rebase CHART={args.chart}")
        # Print stderr from git apply for context
        if apply_result is not None and apply_result.stderr:
            print()
            print("git apply output:")
            print(apply_result.stderr)
    else:
        print()
        print("Clean 3-way apply. Review the changes, then run:")
        print(f"  make finish-rebase CHART={args.chart}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
