"""Shared Pydantic models for CodeSentinel."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    """Finding severity levels."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class Change(BaseModel):
    """A single line change within a diff hunk."""

    type: str = Field(description="add, remove, or context")
    content: str = Field(description="The line content")
    line_number: int | None = Field(default=None, description="Line number in the new file")


class Hunk(BaseModel):
    """A contiguous block of changes in a diff."""

    old_start: int
    new_start: int
    changes: list[Change] = Field(default_factory=list)


class FileDiff(BaseModel):
    """Parsed diff for a single file."""

    filename: str
    language: str = Field(default="unknown")
    hunks: list[Hunk] = Field(default_factory=list)
    status: str = Field(default="modified", description="added, removed, modified, renamed")


class ReviewItem(BaseModel):
    """A single finding from an agent review."""

    file: str = Field(description="Filename where the issue was found")
    line_start: int = Field(description="Starting line number")
    line_end: int | None = Field(default=None, description="Ending line number")
    severity: Severity
    category: str = Field(description="e.g., naming, sql_injection, n_plus_one")
    message: str = Field(description="Description of the issue")
    suggestion: str | None = Field(default=None, description="How to fix it")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    agent: str = Field(description="Which agent produced this finding")
    enriched_by: str | None = Field(default=None, description="A2A enrichment source")


class QueryType(str, Enum):
    """Types of agent-to-agent queries."""

    CONVENTION_CHECK = "convention_check"
    PATTERN_VERIFY = "pattern_verify"
    CONTEXT_REQUEST = "context_request"
    IMPACT_ASSESS = "impact_assess"


class A2AMessage(BaseModel):
    """Agent-to-agent communication message."""

    from_agent: str
    to_agent: str
    query_type: QueryType
    query: str
    context: dict = Field(default_factory=dict)
    priority: int = Field(default=1, ge=1, le=3)
    response: str | None = Field(default=None)
