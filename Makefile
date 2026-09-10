VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
IMAGE   := limpet:latest
ARGS    ?=

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'

# ---- local dev -------------------------------------------------------------

$(VENV): pyproject.toml
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	@touch $(VENV)

.PHONY: install
install: $(VENV) ## Create the venv and install the package (editable, with dev deps)

.PHONY: test
test: $(VENV) ## Run the test suite
	$(PY) -m pytest

.PHONY: lint
lint: $(VENV) ## Lint with ruff
	$(VENV)/bin/ruff check src tests
	$(VENV)/bin/ruff format --check src tests

.PHONY: fmt
fmt: $(VENV) ## Auto-format and auto-fix with ruff
	$(VENV)/bin/ruff check --fix src tests
	$(VENV)/bin/ruff format src tests

.PHONY: check
check: lint test ## Lint + test

.PHONY: run
run: $(VENV) ## Run the CLI: make run ARGS="matches --limit 5"
	$(VENV)/bin/limpet $(ARGS)

.PHONY: clean
clean: ## Remove caches and build artifacts (keeps the venv)
	rm -rf .pytest_cache .ruff_cache .mypy_cache dist build src/limpet.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

.PHONY: distclean
distclean: clean ## Also remove the venv
	rm -rf $(VENV)

# ---- docker --------------------------------------------------------------

.PHONY: docker-build
docker-build: ## Build the container image
	docker build -t $(IMAGE) .

.PHONY: docker-run
docker-run: ## Run a one-off command in the container: make docker-run ARGS="sync"
	docker run --rm -it --env-file .env -v limpet-data:/data $(IMAGE) $(ARGS)

.PHONY: docker-shell
docker-shell: ## Open a shell in the image
	docker run --rm -it --env-file .env -v limpet-data:/data --entrypoint /bin/bash $(IMAGE)

.PHONY: up
up: ## Start the watch poller via docker compose
	docker compose up -d --build

.PHONY: down
down: ## Stop the docker compose stack
	docker compose down
