"""Shared utilities for chart-rebase scripts.

Pure stdlib. Wraps curl/git/tar via subprocess for I/O and external tooling.
"""

import difflib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
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


def _curl_base_args() -> List[str]:
    """Build curl base args with optional proxy support from CHART_REBASE_PROXY env."""
    args = ["curl", "-fsSL"]
    proxy = os.environ.get("CHART_REBASE_PROXY")
    if proxy:
        args.extend(["--proxy", proxy])
    return args


def curl_get(url: str, timeout: int = 60) -> str:
    """Fetch URL and return body as text. Raises CalledProcessError on HTTP failure."""
    args = _curl_base_args() + [url]
    result = subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout)
    return result.stdout


def curl_download(url: str, dest: Path, timeout: int = 300) -> None:
    """Download URL to dest. Atomic: writes to dest.tmp then renames."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    args = _curl_base_args() + [url, "-o", str(tmp)]
    subprocess.run(args, check=True, timeout=timeout)
    tmp.rename(dest)


_GITIGNORE_ENTRIES = [".cache/", ".work/", "__pycache__/"]


def ensure_workspace(root: Path) -> None:
    """Create .cache and .work dirs, ensure .gitignore lists them.

    Idempotent. Safe to call at the start of any command.
    """
    (root / ".cache").mkdir(exist_ok=True)
    (root / ".work").mkdir(exist_ok=True)

    gitignore_path = root / ".gitignore"
    raw_text = gitignore_path.read_text() if gitignore_path.exists() else ""
    existing_set = set(raw_text.splitlines())
    additions = [e for e in _GITIGNORE_ENTRIES if e not in existing_set]
    if additions:
        with gitignore_path.open("a", encoding="utf-8") as f:
            if raw_text and not raw_text.endswith("\n"):
                f.write("\n")
            for entry in additions:
                f.write(entry + "\n")


def make_orphan_vendor_commit(
    repo_root: Path,
    local_dir: str,
    pristine_root: Path,
    version: str,
) -> str:
    """Create an orphan git commit whose tree contains only <local_dir>/ = pristine_root contents.

    Tags the commit as vendor/<local_dir>/<version>. Returns the commit SHA.

    Refuses to overwrite an existing tag of the same name.
    """
    tag_name = f"vendor/{local_dir}/{version}"
    # Check tag does not already exist
    existing = subprocess.run(
        ["git", "-C", str(repo_root), "tag", "-l", tag_name],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if existing:
        raise RuntimeError(f"Tag {tag_name} already exists; refusing to overwrite")

    with tempfile.TemporaryDirectory(dir=str(repo_root / ".work")) as tmp:
        staging = Path(tmp) / "staging"
        index_file = Path(tmp) / "index"

        # Copy pristine into staging/<local_dir>/
        target = staging / local_dir
        shutil.copytree(pristine_root, target)

        env = {**os.environ, "GIT_INDEX_FILE": str(index_file)}

        # Stage all files under <local_dir>/ into the temp index
        subprocess.run(
            ["git", "-C", str(repo_root), "--work-tree", str(staging),
             "add", "-A", local_dir],
            env=env, check=True,
        )

        tree_sha = subprocess.run(
            ["git", "-C", str(repo_root), "write-tree"],
            env=env, capture_output=True, text=True, check=True,
        ).stdout.strip()

        commit_sha = subprocess.run(
            ["git", "-C", str(repo_root), "commit-tree", tree_sha,
             "-m", f"vendor: {local_dir} {version}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        subprocess.run(
            ["git", "-C", str(repo_root), "tag", tag_name, commit_sha],
            check=True,
        )

    return commit_sha


@dataclass
class DiffScore:
    total_lines: int
    changed_files: int


def _walk_files(root: Path) -> set:
    files = set()
    if not root.exists():
        return files
    for p in root.rglob("*"):
        if p.is_file():
            files.add(str(p.relative_to(root)))
    return files


def _read_text(p: Path) -> list:
    try:
        return p.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except FileNotFoundError:
        return []


def diff_score(local: Path, candidate: Path) -> DiffScore:
    """Compute (total_lines, changed_files) between two directory trees.

    For each file present in either side (relative path union), counts the
    number of lines in a context-free unified diff. Files missing on one side
    contribute the entire content of the other side as their score.
    """
    paths = _walk_files(local) | _walk_files(candidate)
    total = 0
    changed = 0
    for rel in paths:
        a = _read_text(local / rel)
        b = _read_text(candidate / rel)
        diff_lines = [
            ln for ln in difflib.unified_diff(a, b, n=0, lineterm="")
            if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))
        ]
        if diff_lines:
            total += len(diff_lines)
            changed += 1
    return DiffScore(total_lines=total, changed_files=changed)


def fetch_index(repo_url: str) -> str:
    """Curl <repo_url>/index.yaml and return its text."""
    return curl_get(repo_url.rstrip("/") + "/index.yaml")


def download_and_extract_chart(
    repo_root: Path,
    repo_url: str,
    chart_name: str,
    version: str,
    tarball_url: str,
) -> Path:
    """Download and extract a chart tarball.

    Caches the tarball at .cache/<chart_name>-<version>.tgz.
    Extracts to .work/extracted/<chart_name>-<version>/.
    Returns the path to the extracted chart root (one level inside, since
    helm tarballs always contain a single top-level directory == chart_name).
    """
    abs_url = resolve_url(repo_url, tarball_url)
    cache_dir = repo_root / ".cache"
    work_dir = repo_root / ".work" / "extracted" / f"{chart_name}-{version}"
    tarball = cache_dir / f"{chart_name}-{version}.tgz"

    cache_dir.mkdir(parents=True, exist_ok=True)
    if not tarball.exists():
        curl_download(abs_url, tarball)

    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    with tarfile.open(tarball, "r:gz") as tf:
        tf.extractall(work_dir)

    # helm tarballs always have a single top-level dir == chart_name
    inner = work_dir / chart_name
    if not inner.is_dir():
        # fall back: pick the first directory inside
        candidates = [p for p in work_dir.iterdir() if p.is_dir()]
        if len(candidates) == 1:
            inner = candidates[0]
        else:
            raise RuntimeError(
                f"Unexpected tarball structure for {chart_name}-{version}: {candidates}"
            )
    return inner
