"""LangGraph StateGraph wiring for the review pipeline.

CS-003 ships a 3-node skeleton: parse_pr → quality_review → format_output.
Future stories add more agent nodes via ``add_node`` and connect them with
``add_conditional_edges`` / ``Send()`` for parallel fan-out.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from codesentinel.graph.nodes import (
    format_output_node,
    parse_pr_node,
    quality_review_node,
)
from codesentinel.graph.state import ReviewState


def build_review_graph() -> CompiledStateGraph:
    """Build and compile the 3-node review graph.

    Returns a compiled graph; call ``.invoke({"pr_url": ...})`` or
    ``.invoke({"raw_diff": ...})`` to run it end-to-end.
    """
    g: StateGraph = StateGraph(ReviewState)
    g.add_node("parse_pr", parse_pr_node)
    g.add_node("quality_review", quality_review_node)
    g.add_node("format_output", format_output_node)

    g.add_edge(START, "parse_pr")
    g.add_edge("parse_pr", "quality_review")
    g.add_edge("quality_review", "format_output")
    g.add_edge("format_output", END)

    return g.compile()
