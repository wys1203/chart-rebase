# chart-rebase: Design Spec

**Date:** 2026-05-18
**Status:** Draft, awaiting review

## 1. Motivation

We maintain local forks of community Helm charts (e.g. `istio-ingressgateway`, `coredns`, `argo-cd`) at the repo root. Each chart was originally pulled from an upstream Helm repo, then modified to fit our on-prem environment. When upstream releases a new version, we need to rebase our local modifications onto the new upstream, surfacing conflicts for manual resolution. We also want a clean way to emit our diff as a patch for upstream contribution.

We work in a closed enterprise environment: only `curl` can reach the internet (with a proxy). `helm` is available but only `helm template` is usable — `helm repo add`, `helm search`, `helm pull` all require network access we don't have via helm.

## 2. Goals

- Maintain N charts at repo root (`istio-ingressgateway/`, `coredns/`, ...). Each chart's "current local version" is what's in its directory.
- Adopt existing charts where we **don't know the upstream base version**. The tool auto-detects it.
- Rebase a chart onto a new upstream version using git's 3-way merge per file; surface conflicts as standard conflict markers for manual resolution.
- Emit local modifications as a git patch (squash by default; per-commit on opt-in) for upstream PRs.
- Work behind a corporate HTTP proxy, configured by a single environment variable.
- No third-party Python dependencies, no `pip install`, no `venv`.
- Use only system tools that are already present in our environment: `python3`, `curl`, `git`, `tar`, optionally `helm` (only `helm template`).

## 3. Non-goals

- Auto-resolving conflicts. We always surface them; resolution is manual.
- Managing chart dependencies / subcharts beyond what `tar` extraction provides.
- Tracking patches as a long-lived series (quilt-style). Local mods live in git history; tags mark vendor baselines. Patches are derived on demand.
- Cross-chart batching (e.g. "rebase all"). Each command operates on one chart by name.

## 4. Repo Layout

```
chart-rebase/
├── Makefile                       # User-facing entry point
├── charts.json                    # Per-chart upstream config + current base version
├── .gitignore                     # Includes .cache/, .work/, __pycache__/
├── scripts/
│   ├── _lib.py                    # Shared: curl wrapper, git plumbing, index.yaml parser, 3-way merge
│   ├── adopt.py                   # Auto-detect base version, create orphan vendor commit + tag
│   ├── rebase.py                  # Pull new upstream, build new orphan vendor commit, 3-way apply local mods
│   ├── finish_rebase.py           # Verify no conflict markers, commit applied result
│   ├── abort_rebase.py            # Roll back an in-progress rebase
│   ├── diff.py                    # Show local mods (git diff vendor-tag HEAD -- chart/)
│   ├── patch.py                   # Emit local mods as squashed patch (or per-commit with SPLIT=1)
│   └── status.py                  # List all charts: current version, dirty state
├── .cache/                        # Downloaded tarballs (persist across runs; gitignored)
├── .work/                         # Per-command scratch space (cleaned per run; gitignored)
├── istio-ingressgateway/          # Working chart (you edit here, this gets deployed)
├── coredns/
└── argo-cd/
```

`.cache/` keeps tarballs to avoid re-download. `.work/` is scratch for extracted upstream and intermediate patches, recreated each run.

## 5. Config Schema (`charts.json`)

```json
{
  "charts": {
    "istio-ingressgateway": {
      "repo": "https://istio-release.storage.googleapis.com/charts",
      "name": "gateway",
      "version": "1.21.0"
    },
    "coredns": {
      "repo": "https://coredns.github.io/helm",
      "name": "coredns",
      "version": "1.29.0"
    }
  }
}
```

- Top-level key is the **local directory name** (= the chart dir at repo root).
- `repo`: upstream Helm chart repo URL (where `index.yaml` lives).
- `name`: chart name as published by upstream (often differs from local dir name, e.g. local `istio-ingressgateway/` ↔ upstream `gateway`).
- `version`: current base version. Mirrors the git tag suffix `vendor/<local-dir>/<version>`. Updated by `adopt` and `finish-rebase`.

Config is hand-editable but normally managed by the tool.

## 6. Environment

### 6.1 Required system tools

The Makefile and scripts probe for these on startup. On missing tool: clear error message with no further action.

| Tool | Used for |
|---|---|
| `python3` (≥3.9) | All scripts |
| `curl` | All HTTP (`index.yaml` fetch + tarball download) |
| `git` | Plumbing for orphan commits, tags, `git apply --3way`, `git merge-file`, `git diff`, `git format-patch` |
| `tar` (via Python `tarfile`) | Extract chart tarballs |

### 6.2 Proxy

A single env var: **`CHART_REBASE_PROXY`**.

- If set: every `curl` invocation gets `--proxy "$CHART_REBASE_PROXY"` appended.
- If unset: `curl` runs without `--proxy` (will not pick up standard `HTTPS_PROXY` env vars unless system default behavior).
- Documented in `make help` and in Makefile comments.

No proxy configuration in `charts.json` (no per-chart override). Single switch, single source.

## 7. Commands

All commands invoked via `make`. Each Make target is a thin wrapper around one Python script.

### 7.1 `make adopt CHART=<local-dir> REPO=<url> [NAME=<chart-name>] [VERSION=<v>]`

Bootstrap a chart that already exists in the repo but has no `vendor/<dir>/<ver>` tag yet.

**Behavior:**
1. Read `<local-dir>/Chart.yaml` to default `NAME` from its `name:` field if not given.
2. Fetch upstream `index.yaml` via `curl` (with proxy if set).
3. Parse `index.yaml` to extract all `(version, tarball_url)` pairs for chart `NAME`.
4. **If `VERSION` given:** skip auto-detect, use it.
5. **Else auto-detect:**
   - Download every available version's tarball to `.cache/<name>-<ver>.tgz` (skip if already cached).
   - Extract each to `.work/candidates/<ver>/`.
   - For each candidate: compute total diff size against the local chart dir, summing changed line counts across all files (using `difflib.unified_diff` line counts, or equivalent).
   - Sort ascending by diff size. Print top 3:
     ```
     Detected base version candidates for istio-ingressgateway:
       1. gateway 1.21.0  →  127 lines / 4 files changed   ★ best match
       2. gateway 1.20.3  →  235 lines / 6 files changed
       3. gateway 1.21.1  →  298 lines / 5 files changed
     Use which? (1/2/3, or q to quit) [1]:
     ```
   - User picks (or accepts default 1).
6. Build an **orphan vendor commit**:
   - Materialize the pristine chosen version into a tree where the only directory is `<local-dir>/`, containing pristine chart files.
   - Use `git write-tree` + `git commit-tree` with no parent → orphan commit SHA.
   - `git tag vendor/<local-dir>/<version> <commit-sha>`.
   - Working tree and current branch (e.g. `main`) are untouched.
7. Add entry to `charts.json`.
8. Print summary: `vendor/<local-dir>/<version>` created; verify with `make diff CHART=<local-dir>`.

**Invariants:**
- Does not touch the working tree or HEAD.
- Idempotent re-run will fail (or warn) if the tag already exists.

### 7.2 `make diff CHART=<local-dir>`

Print `git diff vendor/<local-dir>/<current-version> HEAD -- <local-dir>/`.

### 7.3 `make patch CHART=<local-dir> [SPLIT=1]`

- Default (`SPLIT` unset): emit single squashed patch to stdout. Equivalent to `git diff <vendor-tag> HEAD -- <local-dir>/`.
- `SPLIT=1`: run `git format-patch <vendor-tag>..HEAD -- <local-dir>/`, output `.patch` files into `.work/patches/<local-dir>/`. Print the paths.

### 7.4 `make rebase CHART=<local-dir> VERSION=<new-version>`

The core operation. Replays local mods onto a new upstream version.

**Precondition checks:**
- Working tree is clean (no unstaged or staged changes to any tracked file). Abort with clear message if not.
- `vendor/<local-dir>/<current-version>` tag exists (chart has been `adopt`-ed).
- `<new-version>` exists in upstream `index.yaml`.

**Steps:**
1. **Capture squash diff:**
   ```
   git diff vendor/<local-dir>/<current-version> HEAD -- <local-dir>/ > .work/<local-dir>-localmods.patch
   ```
2. **Download new upstream:**
   - Fetch `index.yaml`, find tarball URL for `<name>` at `<new-version>`.
   - Download via `curl` to `.cache/<name>-<new-version>.tgz` (skip if cached).
   - Extract to `.work/new/`.
3. **Build new orphan vendor commit + tag:**
   - Same plumbing as `adopt`: build tree with `<local-dir>/` containing pristine new content, `git commit-tree`, `git tag vendor/<local-dir>/<new-version>`.
   - Does not touch working tree or HEAD yet.
4. **Replace working tree's `<local-dir>/` with pristine new:**
   - `rm -rf <local-dir>` then copy `.work/new/<NAME>/` → `<local-dir>/`.
   - These changes are unstaged.
5. **Apply local mods with 3-way merge:**
   - `git apply --3way --whitespace=nowarn .work/<local-dir>-localmods.patch`
   - On clean apply: all changes are unstaged in working tree; print "ready, run `make finish-rebase CHART=<local-dir>`".
   - On conflicts: files have `<<<<<<<` / `=======` / `>>>>>>>` markers; print conflict file list and instruct user to resolve, then run `make finish-rebase`.
6. **Update `charts.json`:** set `version` to `<new-version>`. Stage `charts.json`.
7. **Do NOT commit.** User reviews and runs `make finish-rebase`.

**Resulting state mid-rebase:**
- New orphan vendor tag exists.
- `charts.json` modified (staged).
- `<local-dir>/` has new upstream + applied local mods + possibly conflict markers (unstaged).
- HEAD is unchanged.

### 7.5 `make finish-rebase CHART=<local-dir>`

**Steps:**
1. Verify no `<<<<<<< ` / `=======` / `>>>>>>>` markers in `<local-dir>/` files. Abort if any found, listing them.
2. `git add <local-dir>/ charts.json`
3. `git commit -m "chore(<local-dir>): rebase onto upstream <new-version>"`

### 7.6 `make abort-rebase CHART=<local-dir>`

Roll back a `make rebase` that was started but not yet finished.

**Steps:**
1. `git checkout HEAD -- <local-dir>/ charts.json` — restores tracked files and reverts staged `charts.json` changes.
2. `git clean -fd -- <local-dir>/` — removes any files the new upstream introduced that are not in HEAD. Safe because `make rebase` requires a clean working tree as a precondition, so no untracked work is at risk.
3. `git tag -d vendor/<local-dir>/<new-version>` — delete the orphan vendor tag. The orphan commit becomes unreachable and will be GC'd by git.
4. Print summary.

Detection of "is a rebase in progress": the `vendor/<local-dir>/<X>` tag where `X != current version in charts.json` indicates an unfinished rebase. `make abort-rebase` finds and removes that tag.

### 7.7 `make status`

List each entry in `charts.json`:
- Chart name (local dir)
- Current base version
- Whether `<local-dir>/` has uncommitted changes
- Whether a `vendor/<local-dir>/<next>` tag exists without a corresponding finish-rebase commit (indicates an in-progress rebase)

### 7.8 `make help`

Print short usage for each target, including the `CHART_REBASE_PROXY` env var.

## 8. Key Algorithms

### 8.1 Parsing `index.yaml` (no YAML library available)

Helm `index.yaml` has a stable, predictable structure produced by `helm repo index`. We write a small targeted parser in `_lib.py` that:

- Reads the file line by line.
- Locates the `entries:` top-level key.
- For each chart name under `entries:` (2-space indent, ends with `:`), enters a chart section.
- Within a chart section, recognizes each entry starting with `    - ` (4-space indent + dash). For each entry:
  - Within the entry block (continuation lines at 6-space indent), looks for `      version: X` and `      urls:` followed by `        - <url>` lines.
  - Captures `(version, first_url)`.
- Returns `{chart_name: [(version, url), ...]}`.

**Assumed constraints (sufficient for helm-generated index.yaml):**
- 2-space indentation.
- No flow style (`{...}`, `[...]`).
- No anchors / aliases.
- No multi-line scalars in the fields we read.
- URLs are single-line strings.

If a parse encounters an unexpected line for our state machine, it logs a warning and skips. We do not aim to be a general YAML parser.

If the URL is relative (no scheme), resolve against the repo URL.

### 8.2 Orphan Vendor Commit Creation

Given pristine chart content in `.work/pristine/` and the target tree path `<local-dir>/`:

```python
def make_vendor_commit(local_dir: str, pristine_root: Path, version: str) -> str:
    # Build a temp index containing only <local-dir>/<files from pristine_root>
    index_file = Path(".work") / f".idx.{os.getpid()}"
    env = {**os.environ, "GIT_INDEX_FILE": str(index_file)}

    # Stage pristine into temp index under <local-dir>/
    staging = Path(".work") / "staging"
    if staging.exists(): shutil.rmtree(staging)
    shutil.copytree(pristine_root, staging / local_dir)
    subprocess.run(["git", "--work-tree", str(staging), "add", "-A", local_dir], env=env, check=True)

    tree = subprocess.run(["git", "write-tree"], env=env, capture_output=True, text=True, check=True).stdout.strip()
    commit = subprocess.run(
        ["git", "commit-tree", tree, "-m", f"vendor: {local_dir} {version}"],
        capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(["git", "tag", f"vendor/{local_dir}/{version}", commit], check=True)
    index_file.unlink()
    return commit
```

The orphan commit has no parent. Its tree contains only `<local-dir>/` with pristine content. The tag is the only ref keeping it reachable; `git gc` will not collect tagged commits.

### 8.3 3-way Merge During Rebase

We use `git apply --3way` which does the following:
- For each hunk, if the file in the working tree matches what the patch expects (pre-image), apply directly.
- If not, looks up the pre-image blob by hash. The blob is in our repo because it came from the old vendor commit. Performs a 3-way merge between (pre-image blob, current working tree file, post-image from patch).
- On conflict: leaves conflict markers in the file.

This works because:
- The squash diff was generated with `git diff <vendor-tag> HEAD -- <local-dir>/`, so all referenced pre-image blobs exist in our git object database.
- The working tree at apply-time contains the new pristine version (post step 4 of rebase), which differs from the pre-image (old pristine). `--3way` triggers the merge.

### 8.4 Base Version Auto-Detection

For each candidate version V:
- Extract pristine chart for V to `.work/candidates/V/`.
- For each file path P present in either `<local-dir>/` or `.work/candidates/V/`:
  - Read both sides (empty string if missing on one side).
  - Compute `len(list(difflib.unified_diff(a_lines, b_lines, n=0)))` to count diff hunk lines (excludes context).
- Sum across all files → V's score.

Rank ascending. Lowest score = best match. Show top 3 to user.

We assume that helm chart repos retain historical versions in `index.yaml`. If a chart's `index.yaml` doesn't include the actual base (extremely old / gc'd), we can only pick the closest available.

## 9. Conflict Handling

- On conflict during `make rebase`:
  - Affected files contain standard conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`).
  - Print a numbered list of conflict files.
  - Print: "Resolve conflicts, then run `make finish-rebase CHART=<local-dir>`. Or `make abort-rebase CHART=<local-dir>` to roll back."
- `make finish-rebase` rejects any remaining markers, listing the files.

## 10. Safety / Error Handling

- All commands verify working tree state before destructive operations (`rebase`, `finish-rebase` require various preconditions).
- `adopt` refuses to overwrite an existing `vendor/<dir>/<ver>` tag.
- `rebase` refuses if `vendor/<dir>/<new-ver>` already exists (would collide).
- All `curl` calls use `-fsSL --fail` so HTTP errors abort cleanly.
- All scripts print one-line "what I'm doing" trace lines so the user understands progress; failures point to the failing step.

## 11. Bootstrap Behavior

On any command:
1. Verify required system tools exist (`python3`, `curl`, `git`, `tar`). Missing → clear error.
2. Create `.cache/` and `.work/` if missing.
3. Verify `.gitignore` includes `.cache/`, `.work/`, `__pycache__/`. If file missing or entries absent, append.
4. Verify `charts.json` exists; if missing on a command that requires it, error.

This makes the tool self-bootstrapping on first run.

## 12. Open Questions / Future Work

- **Charts with subcharts / dependencies:** the current design treats a chart as a single directory tree. If upstream `Chart.yaml` lists `dependencies:` that get vendored under `charts/` subdirectory, our 3-way merge will treat them as plain files. This is fine but worth flagging.
- **Binary files in chart:** rare but possible (icons, etc.). `git apply --3way` handles them; diff size computation in auto-detect uses raw byte diff for non-text files.
- **Multiple proxies:** if different charts need different proxies, current design forces re-export of `CHART_REBASE_PROXY` between commands. Acceptable for v1.

## 13. Example Session

```bash
# Set proxy once for the shell
export CHART_REBASE_PROXY=http://corp-proxy.internal:3128

# Bootstrap an existing chart whose base version we don't know
make adopt CHART=istio-ingressgateway REPO=https://istio-release.storage.googleapis.com/charts
# (interactive: pick from top-3 candidates)

# Day-to-day: edit, commit normally
vim istio-ingressgateway/templates/deployment.yaml
git commit -am "feat(istio-ingressgateway): add PDB"

# See current local mods vs upstream baseline
make diff CHART=istio-ingressgateway

# Send to upstream as a PR
make patch CHART=istio-ingressgateway > my-changes.patch

# Track a new upstream release
make rebase CHART=istio-ingressgateway VERSION=1.22.0
# (resolve conflicts in working tree)
make finish-rebase CHART=istio-ingressgateway

# Or back out if conflicts are too bad
make abort-rebase CHART=istio-ingressgateway
```
