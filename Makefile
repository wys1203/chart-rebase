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
