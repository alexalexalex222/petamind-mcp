.PHONY: setup install test lint format typecheck clean smoke run_public run_private

setup:
	./scripts/setup.sh

install:
	pip install -e .[dev]
	playwright install chromium

test:
	pytest tests/ -v

lint:
	ruff check src/ tests/

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

smoke:
	titan-factory run --max-tasks 3 --config config/config-smoke-qwen-2x-dec30.yaml

run_public:
	titan-factory run --public-only

run_private:
	titan-factory run

help:
	@echo "Available commands:"
	@echo "  make setup        - Run setup script"
	@echo "  make install      - Install dependencies and playwright browsers"
	@echo "  make test         - Run unit tests"
	@echo "  make lint         - Run ruff linter"
	@echo "  make format       - Run ruff formatter and auto-fix"
	@echo "  make typecheck    - Run mypy type checker"
	@echo "  make clean        - Remove build artifacts and caches"
	@echo "  make smoke        - Run a small smoke test"
	@echo "  make run_public   - Run pipeline for public models"
	@echo "  make run_private  - Run pipeline for all models"
