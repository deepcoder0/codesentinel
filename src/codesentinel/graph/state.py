"""ReviewState — the single object passed through every node in the review graph.

LangGraph requires graph state to be a TypedDict (not a Pydantic model). Fields
that multiple nodes write to are wrapped in ``Annotated[..., reducer]`` so
LangGraph merges per-node return values instead of clobbering them — needed
once we fan out to parallel agents in CS-006+.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict

from codesentinel.models import FileDiff, ReviewItem


def _merge_dicts(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Shallow dict merge — right wins on key collision.

    Used as the reducer for ``agent_reviews`` and ``metadata``. Each agent owns
    its own top-level key (``"quality"``, ``"security"``, …), so collisions
    only happen if the same agent runs twice, in which case "last write wins"
    is the right behaviour. We don't use ``operator.or_`` directly because it
    rejects ``None`` and other edge inputs; this helper makes the contract
    explicit.
    """
    return {**(left or {}), **(right or {})}


class ReviewState(TypedDict, total=False):
    """State carried through the LangGraph review pipeline.

    ``total=False`` lets nodes return partial dicts — LangGraph merges them
    into the running state. Keys are populated as the graph progresses:

    - ``parse_pr_node`` writes ``diff_files``.
    - ``quality_review_node`` writes ``agent_reviews["quality"]``.
    - ``format_output_node`` writes ``final_output``.

    Any node may write to ``metadata`` to record errors or trace data.
    """

    pr_url: str
    raw_diff: str | None
    diff_files: list[FileDiff]
    agent_reviews: Annotated[dict[str, list[ReviewItem]], _merge_dicts]
    final_output: str
    metadata: Annotated[dict[str, Any], _merge_dicts]
