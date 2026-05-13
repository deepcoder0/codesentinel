When writing Python code in this project:
- All functions must have type hints for parameters AND return types
- Use `from __future__ import annotations` at the top of every module
- Prefer `str | None` over `Optional[str]` (Python 3.11+)
- Use Pydantic v2 BaseModel for all data classes — never use dataclasses or plain dicts for structured data
- Use `model_dump()` not `.dict()`, `model_validate()` not `.parse_obj()` (Pydantic v2 syntax)
- Import order: stdlib → third-party → local, each group separated by a blank line
- Use structlog for all logging: `import structlog; log = structlog.get_logger()`
