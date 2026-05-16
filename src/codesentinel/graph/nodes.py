"""Node functions for the review graph.

Each node is ``def node(state: ReviewState) -> dict`` — reads from state,
returns a partial dict for LangGraph to merge. Nodes never mutate state in
place. LLM and network failures are caught and surfaced via the ``metadata``
state field so the graph keeps running.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from pydantic import ValidationError

from codesentinel.config import OLLAMA_HOST, OLLAMA_MODEL
from codesentinel.graph.state import ReviewState
from codesentinel.models import FileDiff, ReviewItem
from codesentinel.parser import parse_pr

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# parse_pr_node
# ---------------------------------------------------------------------------


def parse_pr_node(state: ReviewState) -> dict:
    """Fetch and parse the PR's diff into ``ReviewState.diff_files``.

    Accepts either ``state["pr_url"]`` (GitHub fetch) or ``state["raw_diff"]``
    (local diff string, used by tests). Catches any exception from the parser
    and records it under ``metadata["parse_error"]`` rather than letting the
    graph crash — the format node will still produce an output.
    """
    pr_url = state.get("pr_url")
    raw_diff = state.get("raw_diff")
    log.info("parse_pr_node_start", pr_url=pr_url, has_raw_diff=bool(raw_diff))

    try:
        if raw_diff is not None:
            diffs = parse_pr(raw_diff=raw_diff)
        elif pr_url:
            diffs = parse_pr(pr_url=pr_url)
        else:
            raise ValueError("ReviewState requires pr_url or raw_diff")
    except Exception as exc:
        log.error("parse_pr_node_error", error=str(exc), exc_info=True)
        return {
            "diff_files": [],
            "metadata": {"parse_error": str(exc)},
        }

    log.info("parse_pr_node_done", n_files=len(diffs))
    return {"diff_files": diffs}


# ---------------------------------------------------------------------------
# quality_review_node
# ---------------------------------------------------------------------------

_QUALITY_SYSTEM_PROMPT = (
    "You are a senior code reviewer focused on code quality (naming, "
    "structure, dead code, complexity, error handling). Return ONLY a JSON "
    "array of findings. No prose, no markdown fences. Schema per finding: "
    '{"file": str, "line_start": int, "line_end": int|null, "severity": '
    '"critical"|"warning"|"info", "category": str, "message": str, '
    '"suggestion": str|null, "confidence": float in [0,1]}. If there are '
    "no issues, return []."
)

_LEADING_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?", re.IGNORECASE)
_TRAILING_FENCE_RE = re.compile(r"\n?```\s*$")


def _render_diff_blob(diff_files: list[FileDiff], max_hunks_per_file: int = 5) -> str:
    """Compact textual rendering of parsed diffs for the LLM prompt.

    Truncates to ``max_hunks_per_file`` hunks per file to stay inside the
    7B model's ~8K context budget. Proper chunking is a later story.
    """
    parts: list[str] = []
    for f in diff_files:
        parts.append(f"=== {f.filename} ({f.language}) ===")
        for hunk in f.hunks[:max_hunks_per_file]:
            parts.append(f"@@ -{hunk.old_start} +{hunk.new_start} @@")
            for ch in hunk.changes:
                prefix = {"add": "+", "remove": "-", "context": " "}.get(ch.type, " ")
                parts.append(f"{prefix}{ch.content}")
        if len(f.hunks) > max_hunks_per_file:
            parts.append(f"... ({len(f.hunks) - max_hunks_per_file} more hunks truncated)")
        parts.append("")
    return "\n".join(parts)


def _strip_code_fences(text: str) -> str:
    """Strip leading ```json / trailing ``` that Ollama sometimes adds despite JSON mode."""
    s = text.strip()
    s = _LEADING_FENCE_RE.sub("", s)
    s = _TRAILING_FENCE_RE.sub("", s)
    return s.strip()


def _parse_review_response(content: str, agent: str) -> tuple[list[ReviewItem], str | None]:
    """Parse LLM response into a list of ``ReviewItem``.

    Returns ``(items, error_string)``. On any failure ``items`` is empty and
    ``error_string`` describes what went wrong — caller writes it to
    ``metadata`` rather than crashing.
    """
    cleaned = _strip_code_fences(content)
    if not cleaned:
        return [], "empty_response"

    try:
        raw = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return [], f"json_decode_error: {exc.msg}"

    if not isinstance(raw, list):
        return [], f"expected_list_got_{type(raw).__name__}"

    items: list[ReviewItem] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        entry.setdefault("agent", agent)
        try:
            items.append(ReviewItem.model_validate(entry))
        except ValidationError as exc:
            log.warning("review_item_validation_failed", entry=entry, error=str(exc))
            continue
    return items, None


def quality_review_node(state: ReviewState) -> dict:
    """Send the parsed diff to Ollama and return a quality review.

    Writes ``agent_reviews["quality"]``. If the LLM call fails or the response
    can't be parsed, returns an empty list and records the error in metadata —
    never propagates an exception.
    """
    diff_files: list[FileDiff] = state.get("diff_files", []) or []
    pr_url = state.get("pr_url", "<local>")

    if not diff_files:
        log.info("quality_review_node_no_diff")
        return {"agent_reviews": {"quality": []}}

    log.info("quality_review_node_start", n_files=len(diff_files), pr_url=pr_url)

    diff_blob = _render_diff_blob(diff_files)
    user_msg = f"PR: {pr_url}\nFiles changed:\n\n{diff_blob}\n\nReview the changes above. Output JSON only."

    llm = ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_HOST,
        temperature=0.0,
        format="json",
    )

    metadata: dict[str, Any] = {}
    try:
        response = llm.invoke(
            [
                SystemMessage(content=_QUALITY_SYSTEM_PROMPT),
                HumanMessage(content=user_msg),
            ]
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
    except Exception as exc:
        log.error("quality_review_node_llm_error", error=str(exc), exc_info=True)
        return {
            "agent_reviews": {"quality": []},
            "metadata": {"quality_review_error": f"llm_call_failed: {exc}"},
        }

    items, parse_error = _parse_review_response(content, agent="quality")
    if parse_error:
        log.warning("quality_review_node_parse_error", error=parse_error, raw=content[:500])
        metadata["quality_review_error"] = parse_error

    log.info("quality_review_node_done", n_findings=len(items))
    result: dict[str, Any] = {"agent_reviews": {"quality": items}}
    if metadata:
        result["metadata"] = metadata
    return result


# ---------------------------------------------------------------------------
# format_output_node
# ---------------------------------------------------------------------------

_SEVERITY_EMOJI = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
_SEVERITY_ORDER = ["critical", "warning", "info"]


def format_output_node(state: ReviewState) -> dict:
    """Render ``agent_reviews`` as a markdown PR comment in ``final_output``."""
    reviews = state.get("agent_reviews", {}) or {}
    pr_url = state.get("pr_url", "")
    metadata = state.get("metadata", {}) or {}

    all_items: list[ReviewItem] = [item for items in reviews.values() for item in items]
    log.info("format_output_node_start", n_items=len(all_items), agents=list(reviews.keys()))

    lines: list[str] = []
    lines.append("# CodeSentinel Review")
    if pr_url:
        lines.append(f"PR: {pr_url}")
    lines.append("")

    if metadata.get("parse_error"):
        lines.append(f"> ⚠️ Diff parse error: `{metadata['parse_error']}`")
        lines.append("")

    if not all_items:
        lines.append("✅ **No issues found.**")
        return {"final_output": "\n".join(lines)}

    # Summary line: counts by severity.
    counts = {sev: sum(1 for i in all_items if i.severity.value == sev) for sev in _SEVERITY_ORDER}
    summary = " · ".join(
        f"{_SEVERITY_EMOJI[sev]} {counts[sev]} {sev}" for sev in _SEVERITY_ORDER if counts[sev]
    )
    lines.append(f"**Summary:** {summary}")
    lines.append("")

    for agent_name, items in reviews.items():
        if not items:
            continue
        lines.append(f"## {agent_name.title()} Agent")
        # Group by severity for readability.
        for sev in _SEVERITY_ORDER:
            sev_items = [i for i in items if i.severity.value == sev]
            if not sev_items:
                continue
            lines.append(f"### {_SEVERITY_EMOJI[sev]} {sev.title()}")
            for item in sev_items:
                line_range = (
                    f"L{item.line_start}"
                    if item.line_end is None or item.line_end == item.line_start
                    else f"L{item.line_start}–L{item.line_end}"
                )
                lines.append(f"- **`{item.file}:{line_range}`** ({item.category}) — {item.message}")
                if item.suggestion:
                    lines.append(f"  - 💡 {item.suggestion}")
            lines.append("")

    return {"final_output": "\n".join(lines).rstrip() + "\n"}
