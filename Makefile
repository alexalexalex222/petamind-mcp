.PHONY: setup smoke run_public run_private export test clean

# Default run ID for exports
RUN_ID ?= latest

setup:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -e ".[dev]"
	. .venv/bin/activate && playwright install chromium
	@echo "Setup complete. Activate with: source .venv/bin/activate"

smoke:
	@echo "Running smoke test (3 tasks)..."
	titan-factory run --max-tasks 3 --run-id smoke-test

run_public:
	@echo "Running full pipeline (public models only)..."
	titan-factory run --public-only

run_private:
	@echo "Running full pipeline (all models)..."
	titan-factory run

resume:
	@echo "Resuming run $(RUN_ID)..."
	titan-factory run --resume $(RUN_ID)

export:
	@echo "Exporting training data for run $(RUN_ID)..."
	titan-factory export --run-id $(RUN_ID)

test:
	pytest tests/ -v

lint:
	ruff check src/ tests/
	ruff format --check src/ tests/

format:
	ruff format src/ tests/
	ruff check --fix src/ tests/

typecheck:
	mypy src/

clean:
	rm -rf out/
	rm -rf prompts/niches.json prompts/tasks.jsonl
	rm -rf .pytest_cache
	rm -rf src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Generate niches and tasks only (useful for inspection)
generate-prompts:
	titan-factory generate-prompts

# Show task counts
stats:
	titan-factory stats --run-id $(RUN_ID)
