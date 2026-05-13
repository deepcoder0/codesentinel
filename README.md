# 🛡️ CodeSentinel

**Multi-agent AI code review system** — 4 specialized agents review your GitHub PRs for quality, security, performance, and architecture issues using LangGraph, RAG, and agent-to-agent communication.

> 🚧 Under active development — Sprint 1 in progress

## Architecture

```
GitHub PR → Parse Diff → Fan-out to 4 Agents → A2A Enrichment → Conflict Resolution → PR Comment
                              │
                    ┌─────────┼─────────┐──────────┐
                    ▼         ▼         ▼          ▼
                 Quality   Security  Performance  Architecture
                 Agent     Agent     Agent        Agent
                    │         │         │          │
                    └── RAG (ChromaDB) ──┘──────────┘
```

## Tech Stack

- **Orchestration:** LangGraph (state machine, fan-out/fan-in, interrupt)
- **LLM:** Ollama (local, free) — Qwen 2.5 Coder 7B
- **Vector DB:** ChromaDB + sentence-transformers (local embeddings)
- **API:** FastAPI with webhook support
- **Integration:** MCP server for Claude Desktop, GitHub API
- **Observability:** LangFuse tracing, structlog

## Quick Start

```bash
# Clone and install
git clone https://github.com/YOUR_USERNAME/codesentinel.git
cd codesentinel
make dev

# Pull the LLM model
ollama pull qwen2.5-coder:7b

# Run a review
make serve
# Then POST to http://localhost:8000/api/review with {"pr_url": "..."}
```

## Project Status

| Sprint | Focus | Status |
|--------|-------|--------|
| 1 | Foundation & First Agent | 🔨 In Progress |
| 2 | RAG + Remaining Agents | ⏳ Upcoming |
| 3 | A2A, API & Integration | ⏳ Upcoming |
| 4 | MCP, Observability & HITL | ⏳ Upcoming |
| 5 | Polish, Deploy & Ship | ⏳ Upcoming |
| 6 | Evaluation & Benchmarking | ⏳ Upcoming |

## License

MIT
