"""Tests for codesentinel.graph.state — verifies the TypedDict reducer behaviour.

The reducer on ``agent_reviews`` and ``metadata`` is what lets parallel agent
nodes (CS-006+) write to the same state field without clobbering each other.
These tests lock in the merge contract before we wire any real nodes.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from codesentinel.graph.state import ReviewState, _merge_dicts
from codesentinel.models import ReviewItem, Severity


def _make_item(file: str, agent: str) -> ReviewItem:
    return ReviewItem(
        file=file,
        line_start=1,
        severity=Severity.INFO,
        category="test",
        message=f"finding from {agent}",
        agent=agent,
    )


class TestMergeDictsReducer:
    """The reducer is a plain function — test it in isolation first."""

    def test_disjoint_keys_are_unioned(self) -> None:
        left = {"quality": [_make_item("a.py", "quality")]}
        right = {"security": [_make_item("a.py", "security")]}
        merged = _merge_dicts(left, right)
        assert set(merged.keys()) == {"quality", "security"}

    def test_right_wins_on_collision(self) -> None:
        left = {"quality": [_make_item("a.py", "quality-v1")]}
        right = {"quality": [_make_item("a.py", "quality-v2")]}
        merged = _merge_dicts(left, right)
        assert merged["quality"][0].agent == "quality-v2"

    def test_empty_left(self) -> None:
        right = {"quality": [_make_item("a.py", "quality")]}
        assert _merge_dicts({}, right) == right

    def test_none_inputs_treated_as_empty(self) -> None:
        # LangGraph occasionally passes None for an unset annotated field.
        assert _merge_dicts(None, {"x": 1}) == {"x": 1}  # type: ignore[arg-type]
        assert _merge_dicts({"x": 1}, None) == {"x": 1}  # type: ignore[arg-type]


class TestStateMergingInGraph:
    """End-to-end: two nodes both write to agent_reviews; LangGraph applies the reducer."""

    def test_two_nodes_merging_agent_reviews(self) -> None:
        def node_a(_state: ReviewState) -> dict:
            return {"agent_reviews": {"quality": [_make_item("a.py", "quality")]}}

        def node_b(_state: ReviewState) -> dict:
            return {"agent_reviews": {"security": [_make_item("a.py", "security")]}}

        g = StateGraph(ReviewState)
        g.add_node("a", node_a)
        g.add_node("b", node_b)
        g.add_edge(START, "a")
        g.add_edge("a", "b")
        g.add_edge("b", END)
        compiled = g.compile()

        result = compiled.invoke({"pr_url": "x"})
        # Both keys survive because the reducer unions them.
        assert set(result["agent_reviews"].keys()) == {"quality", "security"}

    def test_metadata_field_uses_reducer(self) -> None:
        def node_a(_state: ReviewState) -> dict:
            return {"metadata": {"node_a": "ran"}}

        def node_b(_state: ReviewState) -> dict:
            return {"metadata": {"node_b": "ran"}}

        g = StateGraph(ReviewState)
        g.add_node("a", node_a)
        g.add_node("b", node_b)
        g.add_edge(START, "a")
        g.add_edge("a", "b")
        g.add_edge("b", END)
        compiled = g.compile()

        result = compiled.invoke({"pr_url": "x"})
        assert result["metadata"] == {"node_a": "ran", "node_b": "ran"}
