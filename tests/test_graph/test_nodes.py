"""Tests for codesentinel.graph.nodes — each node in isolation.

The review nodes are thin wrappers over agent classes (CS-004). These tests
verify the wrapper plumbs state in and out correctly. The agent's own logic
(prompts, retries, parsing) is covered in tests/test_agents/.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from codesentinel.graph.nodes import (
    format_output_node,
    parse_pr_node,
    quality_review_node,
)
from codesentinel.models import Change, FileDiff, Hunk, ReviewItem, Severity


def _sample_diff() -> FileDiff:
    return FileDiff(
        filename="src/api/routes.py",
        language="python",
        status="modified",
        hunks=[
            Hunk(
                old_start=10,
                new_start=10,
                changes=[
                    Change(type="context", content="def get_user(uid):", line_number=10),
                    Change(type="remove", content="    return db.query(uid)"),
                    Change(type="add", content="    return db.exec(uid)", line_number=11),
                ],
            )
        ],
    )


def _item(file: str, sev: Severity, msg: str, agent: str = "quality", line: int = 1) -> ReviewItem:
    return ReviewItem(
        file=file,
        line_start=line,
        severity=sev,
        category="cat",
        message=msg,
        agent=agent,
    )


# ---------------------------------------------------------------------------
# parse_pr_node
# ---------------------------------------------------------------------------


class TestParsePrNode:
    """parse_pr_node dispatches to the parser and surfaces errors via metadata."""

    def test_uses_raw_diff_when_provided(self) -> None:
        with patch("codesentinel.graph.nodes.parse_pr") as mock_parse:
            mock_parse.return_value = [_sample_diff()]
            out = parse_pr_node(
                {"raw_diff": "diff --git a/x b/x\n@@ -1 +1 @@\n-a\n+b\n"}
            )
        mock_parse.assert_called_once()
        assert mock_parse.call_args.kwargs == {
            "raw_diff": "diff --git a/x b/x\n@@ -1 +1 @@\n-a\n+b\n"
        }
        assert len(out["diff_files"]) == 1

    def test_uses_pr_url_when_no_raw_diff(self) -> None:
        with patch("codesentinel.graph.nodes.parse_pr") as mock_parse:
            mock_parse.return_value = []
            parse_pr_node({"pr_url": "https://github.com/o/r/pull/1"})
        mock_parse.assert_called_once_with(pr_url="https://github.com/o/r/pull/1")

    def test_missing_input_yields_metadata_error(self) -> None:
        out = parse_pr_node({})
        assert out["diff_files"] == []
        assert "parse_error" in out["metadata"]

    def test_parser_exception_caught(self) -> None:
        with patch(
            "codesentinel.graph.nodes.parse_pr", side_effect=RuntimeError("api down")
        ):
            out = parse_pr_node({"pr_url": "https://github.com/o/r/pull/1"})
        assert out["diff_files"] == []
        assert "api down" in out["metadata"]["parse_error"]


# ---------------------------------------------------------------------------
# quality_review_node — wrapper around QualityAgent.review()
# ---------------------------------------------------------------------------


class TestQualityReviewNode:
    """Verify the node plumbs state into and out of the agent correctly."""

    def test_empty_diff_skips_agent(self) -> None:
        """If diff_files is empty, don't even construct the agent."""
        with patch("codesentinel.graph.nodes.QualityAgent") as agent_cls:
            out = quality_review_node({"diff_files": []})
        agent_cls.assert_not_called()
        assert out == {"agent_reviews": {"quality": []}}

    def test_passes_diff_and_pr_url_to_agent(self) -> None:
        fake_agent = MagicMock()
        fake_agent.review.return_value = ([], {})
        with patch(
            "codesentinel.graph.nodes.QualityAgent", return_value=fake_agent
        ):
            quality_review_node(
                {
                    "diff_files": [_sample_diff()],
                    "pr_url": "https://github.com/o/r/pull/1",
                }
            )
        fake_agent.review.assert_called_once_with(
            [_sample_diff()], pr_url="https://github.com/o/r/pull/1"
        )

    def test_returns_findings_under_quality_key(self) -> None:
        fake_agent = MagicMock()
        findings = [_item("a.py", Severity.WARNING, "msg")]
        fake_agent.review.return_value = (findings, {})
        with patch(
            "codesentinel.graph.nodes.QualityAgent", return_value=fake_agent
        ):
            out = quality_review_node({"diff_files": [_sample_diff()], "pr_url": ""})
        assert out["agent_reviews"]["quality"] == findings
        assert "metadata" not in out  # no metadata = no extra key

    def test_agent_metadata_propagates_to_state(self) -> None:
        fake_agent = MagicMock()
        fake_agent.review.return_value = ([], {"quality_error": "boom"})
        with patch(
            "codesentinel.graph.nodes.QualityAgent", return_value=fake_agent
        ):
            out = quality_review_node({"diff_files": [_sample_diff()], "pr_url": ""})
        assert out["metadata"] == {"quality_error": "boom"}


# ---------------------------------------------------------------------------
# format_output_node
# ---------------------------------------------------------------------------


class TestFormatOutputNode:
    def test_no_findings_returns_clean_message(self) -> None:
        out = format_output_node({"agent_reviews": {"quality": []}})
        assert "No issues found" in out["final_output"]

    def test_empty_state_returns_clean_message(self) -> None:
        out = format_output_node({})
        assert "No issues found" in out["final_output"]

    def test_renders_finding_per_agent(self) -> None:
        out = format_output_node(
            {
                "pr_url": "https://github.com/o/r/pull/1",
                "agent_reviews": {
                    "quality": [_item("a.py", Severity.WARNING, "naming issue")],
                    "security": [
                        _item("a.py", Severity.CRITICAL, "sql injection", agent="security")
                    ],
                },
            }
        )
        md = out["final_output"]
        assert "# CodeSentinel Review" in md
        assert "## Quality Agent" in md
        assert "## Security Agent" in md
        assert "naming issue" in md
        assert "sql injection" in md
        assert "Critical" in md and "Warning" in md

    def test_severity_summary_counts(self) -> None:
        out = format_output_node(
            {
                "agent_reviews": {
                    "quality": [
                        _item("a.py", Severity.CRITICAL, "x"),
                        _item("a.py", Severity.CRITICAL, "y"),
                        _item("a.py", Severity.INFO, "z"),
                    ]
                }
            }
        )
        md = out["final_output"]
        assert "2 critical" in md
        assert "1 info" in md

    def test_parse_error_surfaces_in_output(self) -> None:
        out = format_output_node(
            {
                "agent_reviews": {},
                "metadata": {"parse_error": "404 from GitHub"},
            }
        )
        assert "Diff parse error" in out["final_output"]
        assert "404 from GitHub" in out["final_output"]
