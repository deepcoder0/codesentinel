"""Node functions for the review graph.

Each node is ``def node(state: ReviewState) -> dict`` — reads from state,
returns a partial dict for LangGraph to merge. Nodes never mutate state in
place. LLM and network failures are caught and surfaced via the ``metadata``
state field so the graph keeps running.

The review nodes themselves are *thin wrappers* — the real work lives on the
``BaseAgent`` subclasses in ``codesentinel.agents``. Future agents
(Security/Performance/Architecture) will follow the same template.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from codesentinel.agents.quality import QualityAgent

# Runtime import (not TYPE_CHECKING): LangGraph's add_node() runs
# typing.get_type_hints() on each node function, which forces resolution of
# every name in its signature. ReviewState appears in node signatures, so
# moving it under TYPE_CHECKING breaks the graph at build time.
from codesentinel.graph.state import ReviewState  # noqa: TC001
from codesentinel.parser import parse_pr

if TYPE_CHECKING:
    from codesentinel.models import ReviewItem

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
# quality_review_node — delegates to QualityAgent
# ---------------------------------------------------------------------------


def quality_review_node(state: ReviewState) -> dict:
    """Run the Quality Agent against ``state["diff_files"]``.

    Thin wrapper: the prompt, retry logic, and confidence filtering all live
    on ``QualityAgent`` / ``BaseAgent``. This node just plumbs state in and
    out. Future agents (Security/Performance/Architecture) get their own
    wrapper nodes following this exact shape.
    """
    diff_files = state.get("diff_files", []) or []
    if not diff_files:
        log.info("quality_review_node_no_diff")
        return {"agent_reviews": {"quality": []}}

    agent = QualityAgent()
    items, agent_metadata = agent.review(
        diff_files, pr_url=state.get("pr_url", "")
    )

    result: dict[str, Any] = {"agent_reviews": {"quality": items}}
    if agent_metadata:
        result["metadata"] = agent_metadata
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
