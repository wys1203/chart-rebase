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

adopt: ## Adopt an existing chart and auto-detect base version
	@python3 scripts/adopt.py --chart $(CHART) --repo $(REPO) \
		$(if $(NAME),--name $(NAME),) $(if $(VERSION),--version $(VERSION),)

diff: ## Show local mods vs vendor baseline
	@python3 scripts/diff.py --chart $(CHART)

patch: ## Emit local mods as patch (default squash; SPLIT=1 for per-commit)
	@python3 scripts/patch.py --chart $(CHART) $(if $(SPLIT),--split,)

rebase: ## Rebase chart onto a new upstream version
	@python3 scripts/rebase.py --chart $(CHART) --version $(VERSION)

finish-rebase: ## Finalize a successful rebase (commit applied result)
	@python3 scripts/finish_rebase.py --chart $(CHART)

abort-rebase: ## Roll back an in-progress rebase
	@python3 scripts/abort_rebase.py --chart $(CHART)

status: ## Show all charts and their current state
	@python3 scripts/status.py
