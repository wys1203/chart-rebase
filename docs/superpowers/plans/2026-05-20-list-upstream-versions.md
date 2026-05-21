# List Upstream Versions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `make versions` command that lists the chart versions available in an upstream Helm repository.

**Architecture:** Extend the existing `index.yaml` state-machine parser in `_lib.py` to also capture `appVersion`, exposed as a new `list_versions_detailed()` returning `ChartVersion` records; rewrite `list_versions()` as a thin wrapper so `adopt.py`/`rebase.py` are untouched. Add a `scripts/versions.py` CLI and a `versions` Makefile target.

**Tech Stack:** Python 3 stdlib, GNU Make, curl (via existing `_lib` helpers).

---

## File Structure

- `scripts/_lib.py` — add `ChartVersion` dataclass + `list_versions_detailed()`; rewrite `list_versions()` as a wrapper.
- `scripts/versions.py` — new CLI entry point.
- `Makefile` — add `versions` target, `.PHONY`, and help line.
- `tests/test_lib.py` — add tests for `list_versions_detailed()`.

---

### Task 1: `list_versions_detailed()` in `_lib.py`

**Files:**
- Modify: `scripts/_lib.py:37-107` (the `list_versions` function)
- Test: `tests/test_lib.py` (add to `IndexReaderTests` class)

- [ ] **Step 1: Write the failing tests**

Add these two methods inside the `IndexReaderTests` class in `tests/test_lib.py` (after `test_list_versions_unknown_chart_returns_empty`):

```python
    def test_list_versions_detailed_for_gateway(self):
        versions = _lib.list_versions_detailed(self.fixture, "gateway")
        self.assertEqual(
            [(v.version, v.app_version, v.url) for v in versions],
            [
                ("1.22.0", "1.22.0", "https://example.com/charts/gateway-1.22.0.tgz"),
                ("1.21.0", "1.21.0", "https://example.com/charts/gateway-1.21.0.tgz"),
                ("1.20.3", "1.20.3", "relative/gateway-1.20.3.tgz"),
            ],
        )

    def test_list_versions_detailed_for_ambient(self):
        versions = _lib.list_versions_detailed(self.fixture, "ambient")
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0].version, "1.30.0-rc.0")
        self.assertEqual(versions[0].app_version, "1.30.0-rc.0")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_lib -v`
Expected: FAIL — `AttributeError: module '_lib' has no attribute 'list_versions_detailed'`

- [ ] **Step 3: Implement `ChartVersion` + `list_versions_detailed()` and rewrite `list_versions()`**

In `scripts/_lib.py`, replace the entire `list_versions` function (lines 37-107) with the following. `dataclass` is already imported at line 13.

```python
@dataclass
class ChartVersion:
    """One chart version entry from a helm index.yaml."""
    version: str
    app_version: str
    url: str


def list_versions_detailed(index_yaml_text: str, chart_name: str) -> List[ChartVersion]:
    """Parse a helm-generated index.yaml and return [ChartVersion, ...] for chart_name.

    Targeted line-by-line state machine. Assumes helm's standard 2-space-indent
    output with no flow style, anchors, or multi-line scalars in the captured
    fields. Order in the returned list matches order in the file.
    """
    out: List[ChartVersion] = []
    in_entries = False
    in_target = False
    cur_version: Optional[str] = None
    cur_app_version: str = ""
    cur_url: Optional[str] = None
    expecting_url_list = False

    def flush():
        nonlocal cur_version, cur_app_version, cur_url
        if cur_version is not None:
            out.append(ChartVersion(cur_version, cur_app_version, cur_url or ""))
        cur_version, cur_app_version, cur_url = None, "", None

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

        if line.startswith("    appVersion: "):
            cur_app_version = line.split("appVersion: ", 1)[1].strip().strip('"')
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


def list_versions(index_yaml_text: str, chart_name: str) -> List[Tuple[str, str]]:
    """Parse a helm-generated index.yaml and return [(version, url), ...] for chart_name.

    Thin wrapper over list_versions_detailed; preserved for existing callers.
    """
    return [
        (v.version, v.url)
        for v in list_versions_detailed(index_yaml_text, chart_name)
    ]
```

- [ ] **Step 4: Run the full test file to verify all pass**

Run: `python3 -m unittest tests.test_lib -v`
Expected: PASS — the 2 new tests pass and the 3 existing `list_versions` tests (`test_list_versions_for_gateway`, `test_list_versions_for_ambient`, `test_list_versions_unknown_chart_returns_empty`) still pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/_lib.py tests/test_lib.py
git commit -m "feat: add list_versions_detailed with appVersion capture"
```

---

### Task 2: `versions.py` CLI + Makefile target

**Files:**
- Create: `scripts/versions.py`
- Modify: `Makefile` (`.PHONY` line, `help` block, new target)

- [ ] **Step 1: Create `scripts/versions.py`**

```python
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
```

- [ ] **Step 2: Add the `versions` target to the Makefile**

In `Makefile`, change the `.PHONY` line (line 1) from:

```makefile
.PHONY: help adopt rebase finish-rebase abort-rebase diff patch status check-tools
```

to:

```makefile
.PHONY: help adopt rebase finish-rebase abort-rebase diff patch status versions check-tools
```

In the `help` target, after the `make adopt ...` echo line, add:

```makefile
	@echo "  make versions CHART=<dir> | REPO=<url> NAME=<chart>"
```

After the `status` target (before `check-tools`), add:

```makefile
versions: ## List upstream chart versions
	@python3 scripts/versions.py $(if $(CHART),--chart $(CHART),) \
		$(if $(REPO),--repo $(REPO),) $(if $(NAME),--name $(NAME),)
```

- [ ] **Step 3: Verify the argument-error paths (offline, no network)**

Run: `make versions`
Expected: prints `usage: versions.py --chart <dir> | --repo <url> --name <chart>` to stderr, exit status 1 (`echo $?` → `1`).

Run: `make versions CHART=does-not-exist`
Expected: prints `error: chart does-not-exist not in charts.json` to stderr, exit status 1.

- [ ] **Step 4: Verify the happy path against a real repo (requires network)**

Run: `make versions REPO=https://istio-release.storage.googleapis.com/charts NAME=gateway`
Expected: prints `fetching index.yaml ...`, a `VERSION` / `APP VERSION` table, and an `N versions` summary line, exit status 0.
If no network is available, skip this step and note it.

- [ ] **Step 5: Commit**

```bash
git add scripts/versions.py Makefile
git commit -m "feat: make versions — list upstream chart versions"
```

---

## Self-Review Notes

- **Spec coverage:** interface (`--chart` / `--repo`+`--name`) → Task 2 Step 1; `list_versions_detailed` + `ChartVersion` + `list_versions` wrapper → Task 1 Step 3; output table → Task 2 Step 1; error handling (chart not found, missing args, curl failure via `_lib`) → Task 2 Step 1; Makefile target → Task 2 Step 2; tests → Task 1 Steps 1-4. All covered.
- **Types:** `ChartVersion(version, app_version, url)` defined in Task 1 Step 3; used as `v.version` / `v.app_version` / `v.url` in Task 1 tests and Task 2 `versions.py` — consistent.
- **No placeholders:** every code step shows complete code; every command shows expected output.
