# CodeSentinel — Project Context for Claude Code

## What Is This Project?

CodeSentinel is a multi-agent AI code review system. It takes a GitHub PR URL, runs 4 specialized agents (Quality, Security, Performance, Architecture) in parallel using LangGraph, enriches findings via agent-to-agent communication, and posts a structured review comment on the PR.

**Tech stack:** Python 3.11+ · LangGraph · LangChain · Ollama (local LLM) · ChromaDB (vector DB) · FastAPI · MCP SDK · Pydantic · structlog · LangFuse

**Owner:** Deepanshu — learning LangGraph, RAG, and multi-agent patterns by building this from scratch. The goal is ~80% hand-written core logic, with Claude handling boilerplate.

---

## Dev Environment

- **Python:** 3.11+ via Homebrew (`brew install python@3.11`)
- **Virtual env:** `.venv/` in project root — always activate before working
- **Activate:** `source .venv/bin/activate`
- **macOS system Python is 3.9.6 — do NOT use it**
- **Ollama:** local LLM server at `http://localhost:11434`
- **Model:** `qwen2.5-coder:7b` — pull with `ollama pull qwen2.5-coder:7b`

### First-time setup
```bash
brew install python@3.11
python3.11 -m venv .venv
source .venv/bin/activate
make dev
ollama pull qwen2.5-coder:7b
python -m codesentinel.hello_graph  # Verify everything works
```

---

## Project Structure

```
src/codesentinel/
├── __init__.py
├── main.py              # FastAPI app entrypoint
├── config.py            # Settings, env vars, model config
├── hello_graph.py       # CS-001 verification script
├── agents/
│   ├── __init__.py
│   ├── base.py          # BaseAgent ABC
│   ├── quality.py       # Quality Agent
│   ├── security.py      # Security Agent
│   ├── performance.py   # Performance Agent
│   └── architecture.py  # Architecture Agent
├── graph/
│   ├── __init__.py
│   ├── state.py         # ReviewState TypedDict
│   ├── nodes.py         # Graph node functions
│   └── builder.py       # StateGraph construction
├── rag/
│   ├── __init__.py
│   ├── ingest.py        # Knowledge ingestion CLI
│   └── retriever.py     # ChromaDB retrieval
├── api/
│   ├── __init__.py
│   ├── routes.py        # FastAPI endpoints
│   └── webhook.py       # GitHub webhook handler
├── mcp/
│   ├── __init__.py
│   └── server.py        # MCP server with 5 tools
├── output/
│   ├── __init__.py
│   ├── formatter.py     # Markdown review formatter
│   └── github_poster.py # PR comment poster
└── models.py            # Shared Pydantic models (FileDiff, ReviewItem, A2AMessage)
```

---

## Coding Standards

### Python Style
- Python 3.11+ with type hints on ALL function signatures
- Pydantic v2 for all data models — use `model_validator`, not legacy `validator`
- Use `structlog` for logging, never bare `print()` — format: `log.info("event_name", key=value)`
- Imports: stdlib → third-party → local, separated by blank lines
- Docstrings: Google style, required on all public functions and classes
- Max line length: 100 characters
- Use `pathlib.Path` over `os.path`

### Architecture Rules
- Every agent MUST extend `BaseAgent` (in `agents/base.py`)
- State mutations: nodes return partial dicts, LangGraph merges them — never mutate state directly
- All LLM calls go through `langchain_ollama.ChatOllama` — no direct HTTP to Ollama
- RAG retrieval returns `List[Document]` with metadata — agents inject these into prompts via `{rag_context}` template variable
- Pydantic models for all data boundaries: API input/output, agent input/output, graph state fields
- Config via environment variables loaded in `config.py` — never hardcode secrets or model names

### Testing
- pytest for all tests
- Test files mirror source: `tests/test_agents/test_quality.py` for `src/codesentinel/agents/quality.py`
- Mock all LLM calls in unit tests — use `@patch("codesentinel.agents.quality.ChatOllama")`
- Fixtures in `tests/fixtures/` — sample diffs, sample agent outputs, sample A2A messages
- Run: `make test` or `pytest tests/ -v`

### Error Handling
- Never let LLM parse failures crash the pipeline — wrap in try/except, return empty results + error metadata
- Use retry with exponential backoff for Ollama calls (max 2 retries)
- Log all errors with structlog including full context (agent name, pr_url, node name)

---

## Sprint Context

We are building this project in 6 sprints (see `docs/codesentinel.html` for the full blueprint).

**Current sprint:** Sprint 1 — Foundation & First Agent (CS-001 through CS-004)

### CS-001: Project scaffolding & dev environment setup
- [x] Create repo structure with /src/codesentinel package
- [x] pyproject.toml with all dependencies
- [x] Makefile with dev commands
- [x] README.md with project description and setup
- [x] Pydantic models (FileDiff, ReviewItem, A2AMessage)
- [x] config.py with env var loading
- [x] hello_graph.py verification script
- [x] First tests (test_models.py with 9 test cases)
- [ ] Ollama running locally with qwen2.5-coder:7b
- [ ] hello_graph runs end-to-end successfully

### CS-002: PR diff parser
### CS-003: LangGraph orchestrator with single agent node
### CS-004: Quality Agent with structured output

---

## Key Design Decisions

1. **Ollama over cloud LLM** — Zero cost during development, privacy, no API key needed. Will add Claude API as optional backend for deployment.
2. **LangGraph over LangChain agents** — Explicit state machine, debuggable, supports fan-out/fan-in and interrupt(). No magic routing.
3. **ChromaDB over Pinecone** — Local, free, no account needed. Embeddings via sentence-transformers/all-MiniLM-L6-v2 (also local).
4. **Pydantic v2 everywhere** — Strict validation at all data boundaries. Catches schema drift early.
5. **4 specialized agents over 1 general agent** — Each has focused knowledge, specific RAG collections, and calibrated severity defaults. Produces better reviews.

---

## Commands Reference

```bash
source .venv/bin/activate   # Always do this first!
make dev          # Install dependencies in editable mode
make test         # Run pytest
make lint         # Run ruff check
make format       # Run ruff format
make typecheck    # Run mypy
make serve        # Start FastAPI dev server (uvicorn)
make ingest       # Run RAG knowledge ingestion
make review URL=  # Quick CLI review of a PR
make clean        # Remove build artifacts
```

---

## Model Configuration

- **Default model:** `qwen2.5-coder:7b` via Ollama
- **Ollama host:** `http://localhost:11434` (configurable via `OLLAMA_HOST`)
- **Temperature:** `0.1` for reviews (deterministic), `0.0` for structured output parsing
- **Context window:** ~8K tokens. Budget: 2K for RAG context, 4K for diff, 2K for prompt/output

---

## What NOT to Do

- Do NOT use system Python (3.9.6) — always activate `.venv` first
- Do NOT use `langchain.agents.AgentExecutor` — we use LangGraph StateGraph directly
- Do NOT install or use OpenAI/Anthropic SDKs unless explicitly asked — this runs on Ollama locally
- Do NOT create database tables without checking existing schema in `models.py`
- Do NOT add new dependencies without adding them to `pyproject.toml`
- Do NOT use `async` patterns unless the specific module requires it (FastAPI routes = async, agents = sync)
- Do NOT commit `.env` files or any file containing tokens/secrets
