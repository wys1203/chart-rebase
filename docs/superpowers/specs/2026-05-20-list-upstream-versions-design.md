# List Upstream Versions — Design

Date: 2026-05-20

## Goal

Add a command that lists the chart versions available in an upstream Helm
repository, so a maintainer can see what is publishable before running `adopt`
or `rebase`.

## Interface

```
make versions REPO=<url> NAME=<chart>     # arbitrary repo
make versions CHART=<dir>                 # adopted chart, read repo/name from charts.json
```

Resolution rules:

- `CHART` given: load `charts.json`, look up the entry, use its `repo` and
  `name`. If the chart is not in `charts.json`, print an error and exit 1.
- `REPO` and `NAME` both given: use them directly.
- Arguments incomplete (e.g. `REPO` without `NAME`, or nothing): print a usage
  error and exit 1.
- If both `CHART` and `REPO`/`NAME` are given, `CHART` takes precedence.

## Output

Versions are listed in the order they appear in `index.yaml` (no semver sort).

```
fetching index.yaml ...
VERSION             APP VERSION
1.30.0-rc.0         1.30.0-rc.0
1.30.0-beta.0       1.30.0-beta.0
1.22.0              1.22.0
3 versions
```

- `appVersion` is optional in `index.yaml`; when absent the column is left
  blank for that row.

## Library changes (`scripts/_lib.py`)

- Add a dataclass `ChartVersion(version: str, app_version: str, url: str)`.
- Add `list_versions_detailed(index_yaml_text, chart_name) -> List[ChartVersion]`.
  This is the existing `list_versions` line-by-line state machine extended with
  one rule: a `    appVersion: ` continuation line sets `app_version` on the
  current version block. Missing `appVersion` defaults to `""`.
- Rewrite `list_versions` as a thin wrapper:
  `return [(v.version, v.url) for v in list_versions_detailed(...)]`.
  Its signature and behavior are unchanged, so `adopt.py` and `rebase.py` need
  no edits.

## New file (`scripts/versions.py`)

CLI entry point. Parses `--chart`, `--repo`, `--name`. Resolves repo/name per
the rules above, calls `_lib.fetch_index`, `_lib.list_versions_detailed`,
prints the table.

## Error handling

- Chart name not present in the index: print
  `error: no versions found for chart <name>` to stderr, exit 1.
- curl failure: surfaced via the existing `subprocess.CalledProcessError` from
  `_lib`, consistent with `adopt`/`rebase`.

## Makefile

Add a `versions` target mirroring the existing target style:

```
versions: ## List upstream chart versions
	@python3 scripts/versions.py $(if $(CHART),--chart $(CHART),) \
		$(if $(REPO),--repo $(REPO),) $(if $(NAME),--name $(NAME),)
```

Add `versions` to `.PHONY` and to the `help` usage block.

## Testing (`tests/test_lib.py`)

- Add tests for `list_versions_detailed` against the `istio_index.yaml`
  fixture for `gateway` and `ambient`: assert `version` and `app_version` for
  each returned `ChartVersion`.
- The 3 existing `list_versions` tests must still pass after the rewrite,
  confirming the wrapper preserves behavior.

## Out of scope

- semver sorting of the output.
- A `created` column (the project's `index.yaml` fixture has no `created`
  field).
- Marking the current base version when in `--chart` mode.
