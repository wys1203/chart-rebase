# chart-rebase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Makefile-driven Python tool that lets us maintain local Helm chart forks at repo root, auto-detect their upstream base version, rebase onto new upstream releases via git 3-way merge, and emit local mods as patches.

**Architecture:** Each chart lives in a directory at repo root (e.g. `istio-ingressgateway/`). For each chart, an orphan git commit holds the pristine upstream content of the current base version, marked by tag `vendor/<dir>/<version>`. Local mods are everything between that tag and `HEAD` for that chart's path. Rebase = capture squash diff → build new orphan vendor commit → replace working tree with new pristine → `git apply --3way` the squash diff → user resolves conflicts → finalize with one commit on `main`.

**Tech Stack:**
- Python 3.9+ stdlib only (`json`, `subprocess`, `pathlib`, `argparse`, `tarfile`, `difflib`, `unittest`, `tempfile`, `shutil`, `os`)
- System tools: `curl`, `git`, `tar`
- GNU Make
- Proxy via single env var: `CHART_REBASE_PROXY` → injected as `curl --proxy` argument

**Spec:** `docs/superpowers/specs/2026-05-18-chart-rebase-design.md`

---

## File Structure

```
chart-rebase/
├── Makefile                   # User-facing entry, thin wrappers around scripts
├── .gitignore                 # .cache/, .work/, __pycache__/
├── scripts/
│   ├── _lib.py                # All shared utilities (single module — small project)
│   ├── adopt.py
│   ├── rebase.py
│   ├── finish_rebase.py
│   ├── abort_rebase.py
│   ├── diff.py
│   ├── patch.py
│   └── status.py
└── tests/
    ├── __init__.py
    ├── test_lib.py            # Unit tests for pure functions in _lib
    └── fixtures/
        └── istio_index.yaml   # Real-ish index.yaml fixture for parser tests
```

**Why one `_lib.py`:** the shared surface is small (config I/O, index reader, curl/git wrappers, vendor commit builder, scoring). Splitting into multiple modules adds import gymnastics with no gain at this size. Revisit if `_lib.py` exceeds ~400 lines.

**Test scope:**
- Unit tests (stdlib `unittest`) for: index.yaml parser, charts.json read/write, URL resolution, diff scoring. All pure / no subprocess.
- Subprocess wrappers (curl, git plumbing) tested ad-hoc via the scripts that use them, not in `unittest`.
- No end-to-end automated test in this plan — too much fixture overhead; manual verification with `istio-ingressgateway` is the acceptance test.

Run tests: `python3 -m unittest discover -s tests -v`

---

## Task 1: Scaffold project skeleton

**Files:**
- Create: `.gitignore`
- Create: `Makefile`
- Create: `scripts/_lib.py` (empty placeholder with module docstring)
- Create: `tests/__init__.py` (empty)
- Create: `tests/fixtures/.keep` (empty)

- [ ] **Step 1: Write `.gitignore`**

Create `.gitignore`:

```gitignore
.cache/
.work/
__pycache__/
*.pyc
```

- [ ] **Step 2: Write minimal `Makefile` with `help` target**

Create `Makefile`:

```makefile
.PHONY: help adopt rebase finish-rebase abort-rebase diff patch status check-tools

CHART ?=
REPO ?=
NAME ?=
VERSION ?=
SPLIT ?=

help: ## Show this help
	@echo "chart-rebase: maintain local Helm chart forks on top of upstream"
	@echo ""
	@echo "Usage:"
	@echo "  make adopt CHART=<dir> REPO=<url> [NAME=<chart>] [VERSION=<v>]"
	@echo "  make rebase CHART=<dir> VERSION=<new-version>"
	@echo "  make finish-rebase CHART=<dir>"
	@echo "  make abort-rebase CHART=<dir>"
	@echo "  make diff CHART=<dir>"
	@echo "  make patch CHART=<dir> [SPLIT=1]"
	@echo "  make status"
	@echo ""
	@echo "Environment:"
	@echo "  CHART_REBASE_PROXY  HTTP/HTTPS proxy URL passed to curl as --proxy"
```

- [ ] **Step 3: Create placeholder `_lib.py`**

Create `scripts/_lib.py`:

```python
"""Shared utilities for chart-rebase scripts.

Pure stdlib. Wraps curl/git/tar via subprocess for I/O and external tooling.
"""
```

- [ ] **Step 4: Create test scaffolding**

Create `tests/__init__.py`:

```python
```

Create empty `tests/fixtures/.keep`:

```bash
mkdir -p tests/fixtures && touch tests/fixtures/.keep
```

- [ ] **Step 5: Verify `make help` works**

Run: `make help`

Expected output starts with `chart-rebase: maintain local Helm chart forks on top of upstream`.

- [ ] **Step 6: Commit**

```bash
git add .gitignore Makefile scripts/_lib.py tests/__init__.py tests/fixtures/.keep
git commit -m "feat: project scaffold (Makefile, gitignore, scripts/tests dirs)"
```

---

## Task 2: charts.json read/write helpers

**Files:**
- Modify: `scripts/_lib.py`
- Create: `tests/test_lib.py`

- [ ] **Step 1: Write failing tests for `load_config` and `save_config`**

Create `tests/test_lib.py`:

```python
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import _lib


class ConfigTests(unittest.TestCase):
    def test_load_missing_returns_empty(self):
        with TemporaryDirectory() as d:
            cfg = _lib.load_config(Path(d) / "charts.json")
            self.assertEqual(cfg, {"charts": {}})

    def test_round_trip(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "charts.json"
            original = {
                "charts": {
                    "istio-ingressgateway": {
                        "repo": "https://example.com/charts",
                        "name": "gateway",
                        "version": "1.21.0",
                    }
                }
            }
            _lib.save_config(p, original)
            loaded = _lib.load_config(p)
            self.assertEqual(loaded, original)

    def test_save_is_pretty_printed(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "charts.json"
            _lib.save_config(p, {"charts": {"x": {"a": "b"}}})
            text = p.read_text()
            self.assertIn("\n", text)
            self.assertIn("  ", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -v`

Expected: FAIL (`AttributeError: module '_lib' has no attribute 'load_config'`)

- [ ] **Step 3: Implement `load_config` and `save_config`**

Append to `scripts/_lib.py`:

```python
import json
from pathlib import Path


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/_lib.py tests/test_lib.py
git commit -m "feat(_lib): charts.json read/write helpers"
```

---

## Task 3: index.yaml targeted reader

**Files:**
- Modify: `scripts/_lib.py`
- Modify: `tests/test_lib.py`
- Create: `tests/fixtures/istio_index.yaml`

- [ ] **Step 1: Create test fixture**

Create `tests/fixtures/istio_index.yaml`:

```yaml
apiVersion: v1
entries:
  ambient:
  - apiVersion: v2
    appVersion: 1.30.0-rc.0
    name: ambient
    urls:
    - https://example.com/charts/samples/ambient-1.30.0-rc.0.tgz
    version: 1.30.0-rc.0
  - apiVersion: v2
    appVersion: 1.30.0-beta.0
    name: ambient
    urls:
    - https://example.com/charts/samples/ambient-1.30.0-beta.0.tgz
    version: 1.30.0-beta.0
  gateway:
  - apiVersion: v2
    appVersion: 1.22.0
    name: gateway
    urls:
    - https://example.com/charts/gateway-1.22.0.tgz
    version: 1.22.0
  - apiVersion: v2
    appVersion: 1.21.0
    name: gateway
    urls:
    - https://example.com/charts/gateway-1.21.0.tgz
    version: 1.21.0
  - apiVersion: v2
    appVersion: 1.20.3
    name: gateway
    urls:
    - relative/gateway-1.20.3.tgz
    version: 1.20.3
generated: "2026-05-05T20:02:41Z"
```

- [ ] **Step 2: Write failing tests for `list_versions` and `resolve_url`**

Append to `tests/test_lib.py`:

```python
class IndexReaderTests(unittest.TestCase):
    def setUp(self):
        self.fixture = (
            Path(__file__).resolve().parent / "fixtures" / "istio_index.yaml"
        ).read_text()

    def test_list_versions_for_gateway(self):
        versions = _lib.list_versions(self.fixture, "gateway")
        self.assertEqual(
            versions,
            [
                ("1.22.0", "https://example.com/charts/gateway-1.22.0.tgz"),
                ("1.21.0", "https://example.com/charts/gateway-1.21.0.tgz"),
                ("1.20.3", "relative/gateway-1.20.3.tgz"),
            ],
        )

    def test_list_versions_for_ambient(self):
        versions = _lib.list_versions(self.fixture, "ambient")
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0][0], "1.30.0-rc.0")

    def test_list_versions_unknown_chart_returns_empty(self):
        self.assertEqual(_lib.list_versions(self.fixture, "nope"), [])


class UrlResolutionTests(unittest.TestCase):
    def test_absolute_url_passes_through(self):
        self.assertEqual(
            _lib.resolve_url("https://example.com/charts", "https://other.com/x.tgz"),
            "https://other.com/x.tgz",
        )

    def test_relative_url_joins_with_repo(self):
        self.assertEqual(
            _lib.resolve_url("https://example.com/charts", "x-1.0.0.tgz"),
            "https://example.com/charts/x-1.0.0.tgz",
        )

    def test_relative_url_strips_trailing_slash_from_repo(self):
        self.assertEqual(
            _lib.resolve_url("https://example.com/charts/", "x-1.0.0.tgz"),
            "https://example.com/charts/x-1.0.0.tgz",
        )
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -v`

Expected: FAIL (`AttributeError: module '_lib' has no attribute 'list_versions'`)

- [ ] **Step 4: Implement `list_versions` and `resolve_url`**

Append to `scripts/_lib.py`:

```python
def list_versions(index_yaml_text: str, chart_name: str) -> list[tuple[str, str]]:
    """Parse a helm-generated index.yaml and return [(version, url), ...] for chart_name.

    Targeted line-by-line state machine. Assumes helm's standard 2-space-indent
    output with no flow style, anchors, or multi-line scalars in the captured
    fields. Order in the returned list matches order in the file.
    """
    out: list[tuple[str, str]] = []
    in_entries = False
    in_target = False
    cur_version: str | None = None
    cur_url: str | None = None
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
        if line.startswith("  - "):
            flush()
            expecting_url_list = False
            # fall through to also check this line for fields (rare but safe)
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/_lib.py tests/test_lib.py tests/fixtures/istio_index.yaml
git commit -m "feat(_lib): index.yaml targeted reader + url resolution"
```

---

## Task 4: curl wrapper with proxy support

**Files:**
- Modify: `scripts/_lib.py`

No unit test — this wraps subprocess. We smoke-test via a live URL inside the next task.

- [ ] **Step 1: Implement `curl_get` and `curl_download`**

Append to `scripts/_lib.py`:

```python
import os
import subprocess


def _curl_base_args() -> list[str]:
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
```

- [ ] **Step 2: Smoke test against a known-good URL**

Run:

```bash
python3 -c "
import sys; sys.path.insert(0, 'scripts')
import _lib
text = _lib.curl_get('https://example.com')
print(len(text), 'bytes received')
print('Contains <html>:', '<html>' in text.lower())
"
```

Expected: prints `>0 bytes received` and `Contains <html>: True`. (If your network requires a proxy, first `export CHART_REBASE_PROXY=...`.)

- [ ] **Step 3: Commit**

```bash
git add scripts/_lib.py
git commit -m "feat(_lib): curl wrapper with CHART_REBASE_PROXY env support"
```

---

## Task 5: Workspace bootstrap

**Files:**
- Modify: `scripts/_lib.py`
- Modify: `tests/test_lib.py`

- [ ] **Step 1: Write failing test for `ensure_workspace`**

Append to `tests/test_lib.py`:

```python
class WorkspaceTests(unittest.TestCase):
    def test_creates_cache_and_work_dirs(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _lib.ensure_workspace(root)
            self.assertTrue((root / ".cache").is_dir())
            self.assertTrue((root / ".work").is_dir())

    def test_creates_gitignore_when_missing(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            _lib.ensure_workspace(root)
            gi = (root / ".gitignore").read_text()
            self.assertIn(".cache/", gi)
            self.assertIn(".work/", gi)
            self.assertIn("__pycache__/", gi)

    def test_appends_missing_entries_to_existing_gitignore(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / ".gitignore").write_text("node_modules/\n")
            _lib.ensure_workspace(root)
            gi = (root / ".gitignore").read_text()
            self.assertIn("node_modules/", gi)
            self.assertIn(".cache/", gi)
            self.assertIn(".work/", gi)

    def test_does_not_duplicate_existing_entries(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / ".gitignore").write_text(".cache/\n.work/\n__pycache__/\n")
            _lib.ensure_workspace(root)
            gi = (root / ".gitignore").read_text()
            self.assertEqual(gi.count(".cache/"), 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -v`

Expected: FAIL (`AttributeError: module '_lib' has no attribute 'ensure_workspace'`)

- [ ] **Step 3: Implement `ensure_workspace`**

Append to `scripts/_lib.py`:

```python
_GITIGNORE_ENTRIES = [".cache/", ".work/", "__pycache__/"]


def ensure_workspace(root: Path) -> None:
    """Create .cache and .work dirs, ensure .gitignore lists them.

    Idempotent. Safe to call at the start of any command.
    """
    (root / ".cache").mkdir(exist_ok=True)
    (root / ".work").mkdir(exist_ok=True)

    gitignore_path = root / ".gitignore"
    existing = gitignore_path.read_text().splitlines() if gitignore_path.exists() else []
    existing_set = set(existing)
    additions = [e for e in _GITIGNORE_ENTRIES if e not in existing_set]
    if additions:
        with gitignore_path.open("a", encoding="utf-8") as f:
            if existing and not existing[-1].endswith("\n") and existing[-1] != "":
                # ensure a newline before appending
                pass
            for entry in additions:
                f.write(entry + "\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/_lib.py tests/test_lib.py
git commit -m "feat(_lib): ensure_workspace bootstraps .cache, .work, .gitignore"
```

---

## Task 6: Orphan vendor commit creation

**Files:**
- Modify: `scripts/_lib.py`

No automated test (requires real git repo). Manual smoke test included.

- [ ] **Step 1: Implement `make_orphan_vendor_commit`**

Append to `scripts/_lib.py`:

```python
import shutil
import tempfile


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
```

- [ ] **Step 2: Manual smoke test**

Run:

```bash
mkdir -p /tmp/cr-smoke/orig /tmp/cr-smoke/pristine
cd /tmp/cr-smoke/orig && git init -q && git commit -q --allow-empty -m "init"
mkdir -p /tmp/cr-smoke/pristine
echo "hello: world" > /tmp/cr-smoke/pristine/Chart.yaml

python3 -c "
import sys; sys.path.insert(0, '$PWD/scripts')
from pathlib import Path
import _lib
_lib.ensure_workspace(Path('/tmp/cr-smoke/orig'))
sha = _lib.make_orphan_vendor_commit(
    Path('/tmp/cr-smoke/orig'),
    'mychart',
    Path('/tmp/cr-smoke/pristine'),
    '1.0.0',
)
print('commit:', sha)
"

cd /tmp/cr-smoke/orig
git tag -l 'vendor/*'
git show vendor/mychart/1.0.0 --stat
cd - >/dev/null
rm -rf /tmp/cr-smoke
```

Expected: tag `vendor/mychart/1.0.0` exists, `git show` lists `mychart/Chart.yaml` as added.

- [ ] **Step 3: Commit**

```bash
git add scripts/_lib.py
git commit -m "feat(_lib): make_orphan_vendor_commit via git plumbing"
```

---

## Task 7: Diff scoring for auto-detect

**Files:**
- Modify: `scripts/_lib.py`
- Modify: `tests/test_lib.py`

- [ ] **Step 1: Write failing tests for `diff_score`**

Append to `tests/test_lib.py`:

```python
class DiffScoreTests(unittest.TestCase):
    def test_identical_trees_score_zero(self):
        with TemporaryDirectory() as d:
            a = Path(d) / "a"
            b = Path(d) / "b"
            a.mkdir(); b.mkdir()
            (a / "f.txt").write_text("hello\nworld\n")
            (b / "f.txt").write_text("hello\nworld\n")
            score = _lib.diff_score(a, b)
            self.assertEqual(score.total_lines, 0)
            self.assertEqual(score.changed_files, 0)

    def test_one_modified_file(self):
        with TemporaryDirectory() as d:
            a = Path(d) / "a"
            b = Path(d) / "b"
            a.mkdir(); b.mkdir()
            (a / "f.txt").write_text("hello\nworld\n")
            (b / "f.txt").write_text("hello\nthere\n")
            score = _lib.diff_score(a, b)
            self.assertGreater(score.total_lines, 0)
            self.assertEqual(score.changed_files, 1)

    def test_file_only_in_one_side(self):
        with TemporaryDirectory() as d:
            a = Path(d) / "a"
            b = Path(d) / "b"
            a.mkdir(); b.mkdir()
            (a / "only.txt").write_text("a\nb\nc\n")
            score = _lib.diff_score(a, b)
            self.assertEqual(score.changed_files, 1)
            self.assertGreaterEqual(score.total_lines, 3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest discover -s tests -v`

Expected: FAIL.

- [ ] **Step 3: Implement `diff_score`**

Append to `scripts/_lib.py`:

```python
import difflib
from dataclasses import dataclass


@dataclass
class DiffScore:
    total_lines: int
    changed_files: int


def _walk_files(root: Path) -> set[str]:
    files = set()
    if not root.exists():
        return files
    for p in root.rglob("*"):
        if p.is_file():
            files.add(str(p.relative_to(root)))
    return files


def _read_text(p: Path) -> list[str]:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/_lib.py tests/test_lib.py
git commit -m "feat(_lib): diff_score for base-version auto-detection"
```

---

## Task 8: Chart download + extract helper

**Files:**
- Modify: `scripts/_lib.py`

No automated test. Manual smoke uses a real tarball.

- [ ] **Step 1: Implement `download_and_extract_chart`**

Append to `scripts/_lib.py`:

```python
import tarfile


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
```

- [ ] **Step 2: Manual smoke test (requires network)**

Run:

```bash
mkdir -p /tmp/cr-smoke
cd /tmp/cr-smoke && git init -q

python3 -c "
import sys; sys.path.insert(0, '$OLDPWD/scripts')
from pathlib import Path
import _lib
root = Path('/tmp/cr-smoke')
_lib.ensure_workspace(root)
idx = _lib.fetch_index('https://istio-release.storage.googleapis.com/charts')
versions = _lib.list_versions(idx, 'gateway')
print('versions:', versions[:3])
v, url = versions[0]
chart_dir = _lib.download_and_extract_chart(root, 'https://istio-release.storage.googleapis.com/charts', 'gateway', v, url)
print('extracted to:', chart_dir)
print('contents:', sorted(p.name for p in chart_dir.iterdir())[:5])
"
rm -rf /tmp/cr-smoke
cd -
```

Expected: prints version list, extracted dir path, and 5 file/dir names like `Chart.yaml`, `templates`, etc. (Set `CHART_REBASE_PROXY` first if behind corporate proxy.)

- [ ] **Step 3: Commit**

```bash
git add scripts/_lib.py
git commit -m "feat(_lib): fetch_index + download_and_extract_chart"
```

---

## Task 9: `adopt` command

**Files:**
- Create: `scripts/adopt.py`
- Modify: `Makefile`

- [ ] **Step 1: Implement `scripts/adopt.py`**

Create `scripts/adopt.py`:

```python
#!/usr/bin/env python3
"""Adopt an existing chart: auto-detect base version, create orphan vendor tag."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib


def detect_chart_name(local_dir: Path, fallback: str | None) -> str:
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


def pick_version_interactive(
    candidates: list[tuple[str, _lib.DiffScore]],
    chart_local_name: str,
) -> str | None:
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


def main() -> int:
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
        scored: list[tuple[str, _lib.DiffScore]] = []
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
```

- [ ] **Step 2: Make adopt.py executable and add Make target**

Make script executable:

```bash
chmod +x scripts/adopt.py
```

Edit `Makefile`, replace the `help` target block by appending after it:

```makefile
adopt: ## Adopt an existing chart and auto-detect base version
	@python3 scripts/adopt.py --chart $(CHART) --repo $(REPO) \
		$(if $(NAME),--name $(NAME),) $(if $(VERSION),--version $(VERSION),)
```

- [ ] **Step 3: Verify Make target invokes script**

Run: `make adopt` (no args)

Expected: argparse error message about `--chart` being required, exit non-zero.

- [ ] **Step 4: Commit**

```bash
git add scripts/adopt.py Makefile
git commit -m "feat: make adopt — auto-detect base version, create vendor tag"
```

---

## Task 10: `diff` command

**Files:**
- Create: `scripts/diff.py`
- Modify: `Makefile`

- [ ] **Step 1: Implement `scripts/diff.py`**

Create `scripts/diff.py`:

```python
#!/usr/bin/env python3
"""Print local modifications vs the current vendor baseline."""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib


def main() -> int:
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
```

- [ ] **Step 2: Make executable and wire Make target**

Make executable:

```bash
chmod +x scripts/diff.py
```

Append to `Makefile`:

```makefile
diff: ## Show local mods vs vendor baseline
	@python3 scripts/diff.py --chart $(CHART)
```

- [ ] **Step 3: Verify**

Run: `make diff` (no chart)

Expected: argparse error about `--chart` required.

- [ ] **Step 4: Commit**

```bash
git add scripts/diff.py Makefile
git commit -m "feat: make diff — git diff vendor-tag HEAD -- chart/"
```

---

## Task 11: `patch` command

**Files:**
- Create: `scripts/patch.py`
- Modify: `Makefile`

- [ ] **Step 1: Implement `scripts/patch.py`**

Create `scripts/patch.py`:

```python
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


def main() -> int:
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
```

- [ ] **Step 2: Make executable and add Make target**

```bash
chmod +x scripts/patch.py
```

Append to `Makefile`:

```makefile
patch: ## Emit local mods as patch (default squash; SPLIT=1 for per-commit)
	@python3 scripts/patch.py --chart $(CHART) $(if $(SPLIT),--split,)
```

- [ ] **Step 3: Verify**

Run: `make patch` (no args)

Expected: argparse error about `--chart`.

- [ ] **Step 4: Commit**

```bash
git add scripts/patch.py Makefile
git commit -m "feat: make patch — squash by default, SPLIT=1 for format-patch"
```

---

## Task 12: `rebase` command

**Files:**
- Create: `scripts/rebase.py`
- Modify: `Makefile`

The big one. Implements steps 1–6 of spec §7.4.

- [ ] **Step 1: Implement `scripts/rebase.py`**

Create `scripts/rebase.py`:

```python
#!/usr/bin/env python3
"""Rebase a chart onto a new upstream version using git 3-way merge."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib


def working_tree_clean(repo_root: Path, paths: list[str]) -> bool:
    """Return True if the given paths have no staged or unstaged changes."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain", "--"] + paths,
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip() == ""


def tag_exists(repo_root: Path, tag: str) -> bool:
    out = subprocess.run(
        ["git", "-C", str(repo_root), "tag", "-l", tag],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return bool(out)


def main() -> int:
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

    # Step 5: apply squash diff with 3-way merge
    print(f"applying local mods with 3-way merge ...")
    apply_result = subprocess.run(
        ["git", "-C", str(repo_root), "apply", "--3way",
         "--whitespace=nowarn", str(patch_path)],
        capture_output=True, text=True, check=False,
    )

    # Step 6: update charts.json (stage it)
    cfg["charts"][args.chart]["version"] = new_version
    _lib.save_config(cfg_path, cfg)
    subprocess.run(
        ["git", "-C", str(repo_root), "add", "charts.json"], check=True,
    )

    # Report
    has_conflicts = "with conflicts" in apply_result.stderr.lower() or \
                    apply_result.returncode != 0
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
        if apply_result.stderr:
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
```

- [ ] **Step 2: Make executable and add Make target**

```bash
chmod +x scripts/rebase.py
```

Append to `Makefile`:

```makefile
rebase: ## Rebase chart onto a new upstream version
	@python3 scripts/rebase.py --chart $(CHART) --version $(VERSION)
```

- [ ] **Step 3: Verify**

Run: `make rebase` (no args)

Expected: argparse error about `--chart` and `--version`.

- [ ] **Step 4: Commit**

```bash
git add scripts/rebase.py Makefile
git commit -m "feat: make rebase — 3-way merge local mods onto new upstream"
```

---

## Task 13: `finish-rebase` command

**Files:**
- Create: `scripts/finish_rebase.py`
- Modify: `Makefile`

- [ ] **Step 1: Implement `scripts/finish_rebase.py`**

Create `scripts/finish_rebase.py`:

```python
#!/usr/bin/env python3
"""Finalize a rebase: verify no conflict markers, commit applied result."""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib


CONFLICT_MARKERS = ("<<<<<<< ", "=======\n", ">>>>>>> ")


def find_conflict_files(chart_dir: Path) -> list[Path]:
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


def main() -> int:
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
```

- [ ] **Step 2: Make executable and add Make target**

```bash
chmod +x scripts/finish_rebase.py
```

Append to `Makefile`:

```makefile
finish-rebase: ## Finalize a successful rebase (commit applied result)
	@python3 scripts/finish_rebase.py --chart $(CHART)
```

- [ ] **Step 3: Verify**

Run: `make finish-rebase` (no chart)

Expected: argparse error.

- [ ] **Step 4: Commit**

```bash
git add scripts/finish_rebase.py Makefile
git commit -m "feat: make finish-rebase — verify clean + commit result"
```

---

## Task 14: `abort-rebase` command

**Files:**
- Create: `scripts/abort_rebase.py`
- Modify: `Makefile`

- [ ] **Step 1: Implement `scripts/abort_rebase.py`**

Create `scripts/abort_rebase.py`:

```python
#!/usr/bin/env python3
"""Roll back an in-progress rebase."""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib


def find_inprogress_new_tag(repo_root: Path, chart: str, current_version: str) -> str | None:
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


def main() -> int:
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
```

- [ ] **Step 2: Make executable and add Make target**

```bash
chmod +x scripts/abort_rebase.py
```

Append to `Makefile`:

```makefile
abort-rebase: ## Roll back an in-progress rebase
	@python3 scripts/abort_rebase.py --chart $(CHART)
```

- [ ] **Step 3: Verify**

Run: `make abort-rebase` (no chart)

Expected: argparse error.

- [ ] **Step 4: Commit**

```bash
git add scripts/abort_rebase.py Makefile
git commit -m "feat: make abort-rebase — restore working tree + drop new tag"
```

---

## Task 15: `status` command + final Makefile polish

**Files:**
- Create: `scripts/status.py`
- Modify: `Makefile`

- [ ] **Step 1: Implement `scripts/status.py`**

Create `scripts/status.py`:

```python
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
         f"{chart}/", "charts.json"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip() != ""


def in_progress_tag(repo_root: Path, chart: str, current_version: str) -> str | None:
    out = subprocess.run(
        ["git", "-C", str(repo_root), "tag", "-l", f"vendor/{chart}/*"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    others = [t for t in out if t and t != f"vendor/{chart}/{current_version}"]
    if not others:
        return None
    # Newest by creation date
    sorted_out = subprocess.run(
        ["git", "-C", str(repo_root), "for-each-ref",
         "--sort=-creatordate", "--format=%(refname:short)",
         f"refs/tags/vendor/{chart}/"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    for t in sorted_out:
        if t != f"vendor/{chart}/{current_version}":
            return t
    return None


def main() -> int:
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
```

- [ ] **Step 2: Make executable and add Make target**

```bash
chmod +x scripts/status.py
```

Append to `Makefile`:

```makefile
status: ## Show all charts and their current state
	@python3 scripts/status.py
```

- [ ] **Step 3: Verify status works on empty repo**

Run: `make status`

Expected: `(no charts adopted yet — run `make adopt ...`)`

- [ ] **Step 4: Commit**

```bash
git add scripts/status.py Makefile
git commit -m "feat: make status — list charts and their state"
```

---

## Task 16: End-to-end acceptance test with istio-ingressgateway

This task is the human-driven acceptance test, not an automated one. It exercises the full workflow against a real upstream chart and validates everything works end-to-end.

- [ ] **Step 1: Set up a test chart directory**

```bash
# (Optional) if behind corporate proxy:
# export CHART_REBASE_PROXY=http://proxy.internal:3128

# Make a stand-in for "an existing chart with local mods we don't know the base of"
mkdir -p istio-ingressgateway
# Pre-seed with a known version + a tweak so adopt has something to detect against
python3 -c "
import sys; sys.path.insert(0, 'scripts')
from pathlib import Path
import _lib
root = Path.cwd()
_lib.ensure_workspace(root)
idx = _lib.fetch_index('https://istio-release.storage.googleapis.com/charts')
versions = _lib.list_versions(idx, 'gateway')
# Pick a non-latest version to test detection
target_version = versions[2][0]
print('seeding with', target_version)
pristine = _lib.download_and_extract_chart(root, 'https://istio-release.storage.googleapis.com/charts', 'gateway', target_version, versions[2][1])
import shutil
shutil.rmtree('istio-ingressgateway')
shutil.copytree(pristine, 'istio-ingressgateway')
"

# Add a fake local mod
echo "# local fork tweak" >> istio-ingressgateway/values.yaml

git add istio-ingressgateway/
git commit -m "import istio-ingressgateway (existing local fork)"
```

- [ ] **Step 2: Adopt the chart (auto-detect)**

```bash
make adopt CHART=istio-ingressgateway \
           REPO=https://istio-release.storage.googleapis.com/charts
```

Expected:
- Lists candidate versions with diff scores
- Top candidate matches the version we seeded with
- After selection: creates `vendor/istio-ingressgateway/<version>` tag, updates `charts.json`

Verify:

```bash
git tag -l 'vendor/*'
cat charts.json
```

- [ ] **Step 3: Verify `make diff` shows only our local tweak**

```bash
make diff CHART=istio-ingressgateway
```

Expected: a one-line addition `+# local fork tweak` at end of `values.yaml`.

- [ ] **Step 4: Verify `make patch` emits the same diff**

```bash
make patch CHART=istio-ingressgateway
```

Expected: same diff output.

- [ ] **Step 5: Rebase onto a different version**

```bash
# Pick a different version from the index
make rebase CHART=istio-ingressgateway VERSION=<a-different-version-from-step-2-list>
```

Expected: either clean apply or conflict report. If clean:

```bash
make finish-rebase CHART=istio-ingressgateway
```

Verify final state:

```bash
git log --oneline -5
git tag -l 'vendor/*'
cat charts.json
make diff CHART=istio-ingressgateway   # should still show only the tweak
```

- [ ] **Step 6: Status check**

```bash
make status
```

Expected: lists `istio-ingressgateway` with the new version and `clean` state.

- [ ] **Step 7: Document any issues found**

If any step fails or behaves oddly, file an issue (or open a task) describing the gap. Common surface-area items to watch:
- Conflict marker detection misses a file (unusual line endings, etc.)
- `clean -fd` removes something it shouldn't (it shouldn't, because precondition is clean tree)
- Score-based detection picks wrong version when local tweaks are sparse

There is no commit for this task — it's verification only.

---

## Self-Review Notes

Spec coverage check:
- §4 layout → Task 1 ✓
- §5 charts.json schema → Task 2 ✓
- §6 environment / 6.2 proxy → Task 4 ✓
- §7.1 adopt → Tasks 3, 7, 8, 9 ✓
- §7.2 diff → Task 10 ✓
- §7.3 patch (incl. SPLIT=1) → Task 11 ✓
- §7.4 rebase → Task 12 ✓
- §7.5 finish-rebase → Task 13 ✓
- §7.6 abort-rebase → Task 14 ✓
- §7.7 status → Task 15 ✓
- §7.8 help (Makefile) → Task 1 + grown across all tasks ✓
- §8.1 index reader → Task 3 ✓
- §8.2 orphan vendor commit → Task 6 ✓
- §8.3 3-way merge (`git apply --3way`) → Task 12 ✓
- §8.4 auto-detect scoring → Task 7 ✓
- §11 bootstrap (`ensure_workspace`) → Task 5 ✓

Placeholder scan: no TBDs, no "add appropriate error handling", every code step has complete code.

Type consistency: `DiffScore` defined in Task 7 has fields `total_lines` and `changed_files`; used consistently in Task 9 (`pick_version_interactive`) and Task 15 (status doesn't reference it). `make_orphan_vendor_commit` signature matches between Task 6 definition and Tasks 9 + 12 call sites.
