"""Tests for codesentinel.graph.nodes — each node in isolation, LLM mocked."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codesentinel.graph.nodes import (
    _parse_review_response,
    _render_diff_blob,
    _strip_code_fences,
    format_output_node,
    parse_pr_node,
    quality_review_node,
)
from codesentinel.models import Change, FileDiff, Hunk, ReviewItem, Severity

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


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
                    Change(type="add", content="    return db.exec(f'... {uid}')", line_number=11),
                ],
            )
        ],
    )


# ---------------------------------------------------------------------------
# parse_pr_node
# ---------------------------------------------------------------------------


class TestParsePrNode:
    """parse_pr_node dispatches to the parser and surfaces errors via metadata."""

    def test_uses_raw_diff_when_provided(self) -> None:
        with patch("codesentinel.graph.nodes.parse_pr") as mock_parse:
            mock_parse.return_value = [_sample_diff()]
            out = parse_pr_node({"raw_diff": "diff --git a/x b/x\n@@ -1 +1 @@\n-a\n+b\n"})
        mock_parse.assert_called_once()
        assert mock_parse.call_args.kwargs == {"raw_diff": "diff --git a/x b/x\n@@ -1 +1 @@\n-a\n+b\n"}
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
        with patch("codesentinel.graph.nodes.parse_pr", side_effect=RuntimeError("api down")):
            out = parse_pr_node({"pr_url": "https://github.com/o/r/pull/1"})
        assert out["diff_files"] == []
        assert "api down" in out["metadata"]["parse_error"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class TestRenderDiffBlob:
    def test_includes_filename_and_language(self) -> None:
        blob = _render_diff_blob([_sample_diff()])
        assert "src/api/routes.py" in blob
        assert "(python)" in blob

    def test_renders_change_prefixes(self) -> None:
        blob = _render_diff_blob([_sample_diff()])
        assert "+    return db.exec" in blob
        assert "-    return db.query" in blob
        assert " def get_user" in blob

    def test_truncates_hunks_beyond_max(self) -> None:
        big = FileDiff(
            filename="big.py",
            language="python",
            hunks=[Hunk(old_start=i, new_start=i, changes=[]) for i in range(10)],
        )
        blob = _render_diff_blob([big], max_hunks_per_file=3)
        assert "7 more hunks truncated" in blob


class TestStripCodeFences:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("[]", "[]"),
            ("```json\n[]\n```", "[]"),
            ("```\n[1, 2]\n```", "[1, 2]"),
            ("  ```json\n[1]\n```  ", "[1]"),
        ],
    )
    def test_strips_variants(self, raw: str, expected: str) -> None:
        assert _strip_code_fences(raw) == expected


class TestParseReviewResponse:
    def test_valid_array(self) -> None:
        raw = (FIXTURE_DIR / "ollama_quality_response.json").read_text()
        items, err = _parse_review_response(raw, agent="quality")
        assert err is None
        assert len(items) == 2
        assert all(isinstance(i, ReviewItem) for i in items)
        assert all(i.agent == "quality" for i in items)

    def test_strips_code_fences_before_parsing(self) -> None:
        raw = '```json\n[{"file":"a.py","line_start":1,"severity":"info","category":"x","message":"y","agent":"quality"}]\n```'
        items, err = _parse_review_response(raw, agent="quality")
        assert err is None
        assert len(items) == 1

    def test_invalid_json_returns_error(self) -> None:
        items, err = _parse_review_response("not json at all", agent="quality")
        assert items == []
        assert err is not None and "json_decode_error" in err

    def test_object_with_no_recognized_keys_is_an_error(self) -> None:
        items, err = _parse_review_response('{"unrelated": "data"}', agent="quality")
        assert items == []
        assert err == "expected_list_got_dict"

    def test_single_finding_dict_wrapped_into_list(self) -> None:
        """Regression: live test showed the LLM returns a single object when there's
        only one finding. Wrap it instead of dropping the finding."""
        raw = '{"file": "a.py", "line_start": 1, "severity": "critical", "category": "x", "message": "sqli"}'
        items, err = _parse_review_response(raw, agent="quality")
        assert err is None
        assert len(items) == 1
        assert items[0].file == "a.py"

    def test_dict_with_findings_key_unwrapped(self) -> None:
        """Some prompts elicit {"findings": [...]} — accept that shape too."""
        raw = '{"findings": [{"file": "a.py", "line_start": 1, "severity": "info", "category": "x", "message": "y"}]}'
        items, err = _parse_review_response(raw, agent="quality")
        assert err is None
        assert len(items) == 1

    def test_empty_dict_treated_as_no_findings(self) -> None:
        """Regression: ChatOllama(format='json') returns `{}` for the empty case."""
        items, err = _parse_review_response("{}", agent="quality")
        assert err is None
        assert items == []

    def test_empty_string(self) -> None:
        items, err = _parse_review_response("", agent="quality")
        assert items == [] and err == "empty_response"

    def test_skips_invalid_entries_but_keeps_valid(self) -> None:
        raw = json.dumps(
            [
                {"file": "a.py", "line_start": 1, "severity": "info", "category": "x", "message": "y"},
                {"this": "is bogus"},  # missing required fields — dropped
                "not even a dict",
            ]
        )
        items, err = _parse_review_response(raw, agent="quality")
        assert err is None  # only individual entries failed
        assert len(items) == 1


# ---------------------------------------------------------------------------
# quality_review_node
# ---------------------------------------------------------------------------


class TestQualityReviewNode:
    def test_empty_diff_skips_llm(self) -> None:
        with patch("codesentinel.graph.nodes.ChatOllama") as ollama_cls:
            out = quality_review_node({"diff_files": []})
        ollama_cls.assert_not_called()
        assert out == {"agent_reviews": {"quality": []}}

    def test_happy_path_parses_response(self) -> None:
        raw = (FIXTURE_DIR / "ollama_quality_response.json").read_text()
        fake_resp = MagicMock()
        fake_resp.content = raw
        fake_llm = MagicMock()
        fake_llm.invoke.return_value = fake_resp

        with patch("codesentinel.graph.nodes.ChatOllama", return_value=fake_llm):
            out = quality_review_node(
                {"diff_files": [_sample_diff()], "pr_url": "https://github.com/o/r/pull/1"}
            )

        fake_llm.invoke.assert_called_once()
        items = out["agent_reviews"]["quality"]
        assert len(items) == 2
        assert all(isinstance(i, ReviewItem) for i in items)
        # No metadata on a clean parse.
        assert "metadata" not in out

    def test_llm_returns_garbage_records_metadata(self) -> None:
        fake_resp = MagicMock()
        fake_resp.content = "this is not json at all"
        fake_llm = MagicMock()
        fake_llm.invoke.return_value = fake_resp

        with patch("codesentinel.graph.nodes.ChatOllama", return_value=fake_llm):
            out = quality_review_node({"diff_files": [_sample_diff()], "pr_url": "x"})

        assert out["agent_reviews"]["quality"] == []
        assert "quality_review_error" in out["metadata"]

    def test_llm_call_exception_caught(self) -> None:
        fake_llm = MagicMock()
        fake_llm.invoke.side_effect = ConnectionError("ollama down")

        with patch("codesentinel.graph.nodes.ChatOllama", return_value=fake_llm):
            out = quality_review_node({"diff_files": [_sample_diff()], "pr_url": "x"})

        assert out["agent_reviews"]["quality"] == []
        assert "llm_call_failed" in out["metadata"]["quality_review_error"]


# ---------------------------------------------------------------------------
# format_output_node
# ---------------------------------------------------------------------------


def _item(file: str, sev: Severity, msg: str, agent: str = "quality", line: int = 1) -> ReviewItem:
    return ReviewItem(
        file=file,
        line_start=line,
        severity=sev,
        category="cat",
        message=msg,
        agent=agent,
    )


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
                    "security": [_item("a.py", Severity.CRITICAL, "sql injection", agent="security")],
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
