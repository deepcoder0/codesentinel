# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What CodeSentinel Is

A multi-agent AI code review system. Pipeline: **GitHub PR → parse diff → fan-out to 4 agents (Quality, Security, Performance, Architecture) in parallel → A2A enrichment → conflict resolution → posted PR comment.** Built on LangGraph (state machine), Ollama (local LLM), and ChromaDB (RAG). Owner is learning these by building from scratch — keep the core logic hand-written; lean on Claude for boilerplate only.

## Source of Truth for Work

**`codesentinelbreakdown.html` at the repo root is the canonical sprint plan.** Every story (CS-001 through CS-024) has a description, acceptance criteria, a "How — Step by Step" subtask list, "Concepts You'll Learn", and a "Gotchas & Tips" section. When the user names a story (e.g. "do CS-002"), open that file, find the matching `<span class="story-id">CS-XXX</span>` block, and treat the acceptance criteria as the contract for "done." The deep-dive's How/Gotchas lists describe the intended implementation approach — follow them unless there's a clear reason not to.

## Current Repo State

Sprint 1 in progress. **Implemented:** `models.py`, `config.py`, `hello_graph.py`, `parser.py` (CS-002), `graph/state.py` + `graph/nodes.py` + `graph/builder.py` (CS-003 — 3-node review pipeline, ChatOllama-backed quality_review, markdown formatter), `cli.py` (entrypoint for `make review`). **Empty scaffolds:** `agents/`, `rag/`, `api/`, `mcp/`, `output/` subpackages — only `__init__.py` files. Do not assume a file exists just because it appears in a planned layout — read or `find` first.

## Dev Environment

- **Always** activate `.venv` before any Python work: `source .venv/bin/activate`. System Python is 3.9.6 and will break things; this project requires 3.11+.
- Ollama must be running at `http://localhost:11434` with `qwen2.5-coder:7b` pulled. `python -m codesentinel.hello_graph` is the smoke test — it runs a minimal 2-node graph against Ollama and prints success.
- First-time setup: `python3.11 -m venv .venv && source .venv/bin/activate && make dev && ollama pull qwen2.5-coder:7b`.

## Commands

```bash
make dev           # pip install -e ".[dev]"
make dev-full      # also installs observability + mcp extras
make test          # pytest tests/ -v --tb=short
make test-cov      # adds coverage report (term + htmlcov/)
make lint          # ruff check src/ tests/
make format        # ruff format src/ tests/
make typecheck     # mypy src/codesentinel/
make serve         # uvicorn codesentinel.main:app --reload --port 8000  (main.py not yet implemented)
make ingest        # python -m codesentinel.rag.ingest  (not yet implemented)
make review URL=…  # python -m codesentinel.cli $(URL)  (not yet implemented)
make clean         # remove build artifacts
```

Single test: `pytest tests/test_models.py::TestReviewItem::test_confidence_bounds -v`. Tests auto-discover from `tests/` with `pythonpath = ["src"]` set in `pyproject.toml`, so no install step is needed between code edits in editable mode.

## Architecture Notes That Span Files

- **Graph state is `TypedDict`, not Pydantic** (LangGraph requirement). For fields multiple nodes write to, use `Annotated[list, operator.add]` so LangGraph's reducer merges them. Nodes are plain functions returning a partial dict — never mutate state in place. See `hello_graph.py` for the minimal pattern.
- **Data boundaries are Pydantic v2.** `src/codesentinel/models.py` defines the shared types: `FileDiff`/`Hunk`/`Change` (diff parser output), `ReviewItem` with `Severity` enum (agent output), and `A2AMessage` with `QueryType` enum (agent-to-agent comms). Anything crossing a module boundary should be one of these or extend them — don't pass raw dicts.
- **All LLM calls go through `langchain_ollama.ChatOllama`.** No direct HTTP to Ollama, no other LLM SDKs (OpenAI/Anthropic). Host and model come from `config.py` (env vars `OLLAMA_HOST`, `OLLAMA_MODEL`, `LLM_TEMPERATURE`), never hardcoded.
- **Agents will share a `BaseAgent` ABC** in `agents/base.py` (not yet written). Each of the 4 agents has its own focused RAG collection — knowledge sources live under `knowledge/{quality,security,performance,architecture}/` and are ingested into ChromaDB at `data/chroma/`. RAG retrieval returns `List[Document]`; agents inject these via a `{rag_context}` prompt template variable.
- **Context budget** for the 7B model is ~8K tokens: roughly 2K RAG, 4K diff, 2K prompt+output. Diff chunking will be needed for large PRs.
- **TypedDict state imports must stay at runtime, not under `TYPE_CHECKING`.** LangGraph's `StateGraph(ReviewState)` and `g.add_node(...)` call `typing.get_type_hints()`, which forces resolution of every name in the TypedDict and in each node's signature. If you move model imports under `TYPE_CHECKING`, graph construction crashes with `NameError`. The `TCH`/`TC001` ruff rule will flag these — suppress with `# noqa: TC001` and a comment explaining why. See `src/codesentinel/graph/state.py` and `nodes.py`.

## Project Rules (Auto-loaded)

`.claude/rules/` contains rules Claude Code auto-applies — don't restate them, but be aware they exist:

- `python-types.md` — `from __future__ import annotations` everywhere, `str | None` over `Optional`, Pydantic v2 syntax (`model_dump`/`model_validate`), structlog over `print`.
- `langgraph.md` — TypedDict state, reducer-annotated fields, partial-dict returns, `add_conditional_edges` for branching, `Send()` for parallelism.
- `tests.md` — mock all LLM calls with `@patch`, JSON fixtures in `tests/fixtures/`, parametrize diff inputs, descriptive test names.

`.claude/settings.json` has a PostToolUse hook that runs `ruff check --fix` after every Write/Edit. Expect your edits to be auto-linted.

## Non-obvious Constraints

- **No `langchain.agents.AgentExecutor`** — orchestration is LangGraph `StateGraph` directly. No magic routing.
- **Sync by default.** Only FastAPI routes are async; agents and graph nodes are sync functions.
- **LLM parse failures must not crash the pipeline.** Wrap parsing in try/except, return empty results plus error metadata. Retry Ollama calls with exponential backoff, max 2 retries.
- **New dependencies go in `pyproject.toml`** (not `requirements.txt` — there isn't one). Optional groups: `dev`, `observability` (langfuse), `mcp`.
- **Ruff config** in `pyproject.toml` selects `E,F,I,N,W,UP,B,SIM,TCH` and targets py311. Line length 100 but E501 is ignored.

## Subagents and Slash Commands

`.claude/agents/` defines project-specific subagents (`code-reviewer`, `prompt-tuner`, `test-writer`) — use them for their stated purposes. `.claude/commands/` defines slash commands (`/review`, `/run-tests`, `/sprint-status`). `PROMPTS.md` is the changelog for agent prompt versions — update it when you change an agent's system prompt.

## Story Workflow

When working a CS-XXX story, follow this loop. It came out of CS-002, where unit tests passed but a live diff exposed a real parser bug.

1. **Read the contract.** Open `codesentinelbreakdown.html`, find the story block, treat acceptance criteria as the contract for "done" and the deep-dive How/Gotchas as the intended approach.
2. **Branch.** `git checkout -b feature/CS-XXX` off `main`. One story per branch.
3. **Implement + unit tests.** Mock all LLM/network calls. Tests live alongside the new module (e.g. `tests/test_<module>.py`); fixtures in `tests/fixtures/`.
4. **Live integration test.** Don't stop at green unit tests — run the new code against real data once (a real diff, real Ollama call, real PR, etc.) and cross-check the output against an external source of truth (e.g. `git diff --numstat`, the GitHub UI, a hand-counted expected value). Unit tests prove the code matches your mental model; live tests prove your mental model matches reality.
5. **Push, open PR, merge.** Commit messages use the `CS-XXX: <title>` prefix. After merge, update the "Current Repo State" block in this file so future Claude sees the new shape of the codebase.
