.PHONY: dev test lint format serve ingest review clean

# Install in editable mode with dev dependencies
dev:
	pip install -e ".[dev]"

# Install all optional deps
dev-full:
	pip install -e ".[dev,observability,mcp]"

# Run tests
test:
	pytest tests/ -v --tb=short

# Run tests with coverage
test-cov:
	pytest tests/ -v --cov=codesentinel --cov-report=term-missing --cov-report=html

# Lint
lint:
	ruff check src/ tests/

# Format
format:
	ruff format src/ tests/

# Type check
typecheck:
	mypy src/codesentinel/

# Start FastAPI dev server
serve:
	uvicorn codesentinel.main:app --reload --host 0.0.0.0 --port 8000

# Ingest RAG knowledge base
ingest:
	python -m codesentinel.rag.ingest

# Quick CLI review (usage: make review URL=https://github.com/... OR make review DIFF=path/to.diff)
review:
ifdef DIFF
	python -m codesentinel.cli --diff $(DIFF)
else
	python -m codesentinel.cli $(URL)
endif

# Clean build artifacts
clean:
	rm -rf dist/ build/ *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} +
