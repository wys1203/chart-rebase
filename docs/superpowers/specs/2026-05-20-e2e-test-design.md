# e2e Test — Design

Date: 2026-05-20

## Goal

Add a hermetic end-to-end test that exercises every `chart-rebase` workflow
command against a real-shaped scenario: adopt the istio `gateway` chart at
`1.12.9`, layer local modifications on it (a new template, a `values.yaml`
edit, a helper addition), then rebase onto `1.24.6` — covering the clean
rebase, the abort path, and the conflict-resolution path.

## Approach

A Python `unittest` file. Each test builds a throwaway git repo in a temp
directory, copies the real `Makefile` and `scripts/` into it, and drives the
actual `make` targets via subprocess. Charts are served from vendored fixture
tarballs over `file://` URLs — no network, fully deterministic.

`curl` (used by `_lib` for both `fetch_index` and `curl_download`) supports the
`file://` scheme, so a local directory acts as a Helm repo with no code change.

## Components

### 1. Fixtures — `tests/fixtures/e2e-repo/`

- `gateway-1.12.9.tgz` — the real istio `gateway` chart, version 1.12.9.
- `gateway-1.24.6.tgz` — the real istio `gateway` chart, version 1.24.6.
- `index.yaml` — a minimal Helm repo index listing both versions for the
  `gateway` chart. URLs are **relative** (`gateway-1.12.9.tgz`,
  `gateway-1.24.6.tgz`) so the index is location-independent; `_lib.resolve_url`
  joins them against the `file://` REPO path computed at test time.

`_lib.resolve_url` treats only `http://`/`https://` as absolute, so the index
URLs MUST be relative — an absolute `file://` URL there would be mis-joined.

The two tarballs are downloaded once (during implementation) from
`https://istio-release.storage.googleapis.com/charts` and committed to the repo
as test fixtures.

### 2. `tests/test_e2e.py`

One `unittest.TestCase` subclass, `E2ETests`. Discovered by
`python3 -m unittest discover tests` alongside `test_lib.py`.

**Helpers:**

- `_new_scratch_repo()` — create a temp dir, `git init`, set `user.name` /
  `user.email`, copy the real `Makefile` and `scripts/` directory into it,
  return the path. Registered for cleanup.
- `_seed_gateway(scratch, version)` — extract the fixture tarball for `version`
  and place its inner `gateway/` directory into `scratch/gateway/`, then
  `git add gateway/ && git commit` so `HEAD` exists.
- `_run_make(scratch, target, **vars)` — run `make <target> VAR=val ...` via
  subprocess with `cwd=scratch`, capture stdout/stderr and return code.
- `REPO_URL` — `file://` + absolute path to `tests/fixtures/e2e-repo`.

**Test methods:**

| Method | Workflow | Key assertions |
| --- | --- | --- |
| `test_versions` | `make versions REPO=<file://> NAME=gateway`; then after adopt, `make versions CHART=gateway` | output table lists `1.12.9` and `1.24.6`; both invocation forms work; exit 0 |
| `test_clean_rebase_workflow` | adopt 1.12.9 → apply clean mods → `diff` → `patch` → `status` → rebase 1.24.6 → finish-rebase | see below |
| `test_abort_rebase` | adopt 1.12.9 → mods → rebase 1.24.6 → abort-rebase | see below |
| `test_conflict_rebase` | adopt 1.12.9 → conflicting mod → rebase 1.24.6 → finish (refused) → resolve → finish | see below |

#### `test_clean_rebase_workflow`

1. `_seed_gateway(scratch, "1.12.9")`.
2. `make adopt CHART=gateway REPO=<file://> VERSION=1.12.9` → exit 0;
   `charts.json` records `gateway` at `1.12.9`; tag `vendor/gateway/1.12.9`
   exists. Commit `charts.json`.
3. Apply the **clean local modifications** (see "Local modifications"), then
   `git add gateway/ && git commit`.
4. `make diff CHART=gateway` → exit 0; output references the modified/added
   files.
5. `make patch CHART=gateway` → exit 0; stdout is a non-empty patch mentioning
   the new template file.
6. `make status` → exit 0; output lists `gateway` at version `1.12.9`,
   reported `clean` (working tree was just committed).
7. `make rebase CHART=gateway VERSION=1.24.6` → exit 0; output indicates a
   clean 3-way apply (no `CONFLICT`); tag `vendor/gateway/1.24.6` exists;
   `charts.json` now records `1.24.6`.
8. `make finish-rebase CHART=gateway` → exit 0; a commit is created.
9. Final assertions on `scratch/gateway/`: the new template file is present;
   the `values.yaml` edit is present; content unique to upstream `1.24.6` is
   present (confirming the base was advanced); no conflict markers anywhere.

#### `test_abort_rebase`

1–3. As above through applying mods and committing.
4. `make rebase CHART=gateway VERSION=1.24.6` → exit 0.
5. `make abort-rebase CHART=gateway` → exit 0.
6. Assertions: `scratch/gateway/` and `charts.json` are byte-identical to their
   post-commit (pre-rebase) state — `charts.json` back to `1.12.9`, the local
   mods still present, the upstream-`1.24.6`-only content gone; tag
   `vendor/gateway/1.24.6` deleted; `git status --porcelain -- gateway/`
   reports no changes (working tree restored to `HEAD`).

#### `test_conflict_rebase`

1. `_seed_gateway(scratch, "1.12.9")`; adopt; commit `charts.json`.
2. Apply the **conflict modification** (edit a line that differs between
   `1.12.9` and `1.24.6`); `git add gateway/ && git commit`.
3. `make rebase CHART=gateway VERSION=1.24.6` → exit 0; output contains
   `CONFLICT`; at least one file under `gateway/` contains `<<<<<<< ` markers.
4. `make finish-rebase CHART=gateway` → exit 1; stderr names the file with
   unresolved markers; no commit created.
5. Resolve: rewrite the conflicted file removing the `<<<<<<<` / `=======` /
   `>>>>>>>` markers, keeping a chosen resolution.
6. `make finish-rebase CHART=gateway` → exit 0; commit created; no markers
   remain.

## Local modifications

The clean modifications must 3-way-merge onto `1.24.6` with **no conflict**;
the conflict modification must produce a conflict **deterministically**. Both
are pinned against the fixed fixture tarballs. During implementation, diff the
two extracted charts (`gateway-1.12.9` vs `gateway-1.24.6`) to pick exact
regions:

- **New template** — add `gateway/templates/e2e-extra.yaml`, a small valid
  manifest. A newly added file can never produce a 3-way conflict.
- **`values.yaml` edit** — change a key whose value is **identical** in both
  `1.12.9` and `1.24.6`. "Ours changed it, theirs unchanged" merges cleanly.
- **Helper** — add a new named template via a `{{- define ... }}` block. Place
  it in `_helpers.tpl` if a region identical between both versions exists;
  otherwise add it as a new file `gateway/templates/_e2e-helpers.tpl` (Helm
  loads every `_*.tpl`). A new file is conflict-free.
- **Conflict modification** — edit a single line that **differs** between the
  two versions (for example `Chart.yaml`'s `appVersion`, which is `1.12.9` vs
  `1.24.6`). Both base→ours and base→theirs touch the same line → guaranteed
  conflict.

The implementer verifies these choices against the actual fixture content; the
invariant is: clean tests produce zero conflict markers, the conflict test
produces markers.

## Driving `make`

The `scripts/*.py` use `Path.cwd()` as the repo root, and the `Makefile`
recipes call `python3 scripts/<name>.py` relative to the working directory.
Copying both the `Makefile` and `scripts/` into the scratch repo lets the test
invoke the literal `make` commands a user runs, with the scratch repo as cwd.

The copied `Makefile` and `scripts/` sit untracked in the scratch repo. Every
command scopes its git operations to `gateway/` and `charts.json`
(`git status --porcelain -- gateway/`, `git diff <tag> HEAD -- gateway/`,
`git clean -fd -- gateway/`), so the untracked files never interfere.

## Known risk — status / abort tag detection

`status.py` and `abort_rebase.py` identify an in-progress rebase as "the newest
`vendor/<chart>/<v>` tag whose version differs from the current version". After
a successful `finish-rebase` to `1.24.6`, the old `vendor/gateway/1.12.9` tag
still exists, so `make status` may report `rebase-in-progress->1.12.9` when
nothing is in progress.

The e2e test asserts **actual** behavior. `test_clean_rebase_workflow` checks
`make status` only at step 6 (before any rebase), where the state is
unambiguous — it does not assert the `STATE` column after `finish-rebase`. If
implementation confirms the post-finish mis-report, it is recorded as a
finding and reported to the user; fixing it is out of scope for this task.

## Out of scope

- `make check-tools` — an environment probe (`command -v`), no chart logic.
- Fixing the status/abort lingering-tag quirk.
- Testing the auto-detect (interactive) `adopt` path — the test always passes
  an explicit `VERSION` to avoid the `input()` prompt.

## Testing

`tests/test_e2e.py` is itself the test. It runs under
`python3 -m unittest discover tests -v`. Each method is independent (own
scratch repo, own cleanup). Expected runtime is a few seconds — tar extraction
and local git operations only, no network.
