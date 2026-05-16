"""End-to-end tests for the compiled review graph (LLM mocked)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from codesentinel.graph.builder import build_review_graph

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


def _patch_ollama(content: str):
    fake_resp = MagicMock()
    fake_resp.content = content
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = fake_resp
    return patch("codesentinel.graph.nodes.ChatOllama", return_value=fake_llm)


class TestEndToEnd:
    """build_review_graph().invoke(...) — happy path and failure paths."""

    def test_invoke_with_raw_diff_produces_markdown(self) -> None:
        raw_diff = (FIXTURE_DIR / "python_simple.diff").read_text()
        ollama_response = (FIXTURE_DIR / "ollama_quality_response.json").read_text()

        with _patch_ollama(ollama_response):
            graph = build_review_graph()
            result = graph.invoke({"raw_diff": raw_diff, "pr_url": ""})

        md = result["final_output"]
        assert "# CodeSentinel Review" in md
        # Findings from the fixture should be rendered.
        assert "src/api/routes.py" in md
        assert "## Quality Agent" in md
        # diff_files should have been populated by parse_pr_node.
        assert len(result["diff_files"]) == 1
        assert result["diff_files"][0].filename == "src/api/routes.py"
        # agent_reviews populated by quality_review_node.
        assert len(result["agent_reviews"]["quality"]) == 2

    def test_invoke_with_unparseable_llm_response(self) -> None:
        raw_diff = (FIXTURE_DIR / "python_simple.diff").read_text()

        with _patch_ollama("this is definitely not JSON"):
            graph = build_review_graph()
            result = graph.invoke({"raw_diff": raw_diff, "pr_url": ""})

        # Graph still completes; markdown reports no findings; metadata has the error.
        assert "No issues found" in result["final_output"]
        assert "quality_review_error" in result["metadata"]

    def test_invoke_with_missing_inputs_does_not_crash(self) -> None:
        with _patch_ollama("[]"):
            graph = build_review_graph()
            result = graph.invoke({"pr_url": "", "raw_diff": None})

        # Graph completes; the parse-error path runs.
        assert "Diff parse error" in result["final_output"]
        assert "parse_error" in result["metadata"]

    def test_no_findings_renders_clean_message(self) -> None:
        raw_diff = (FIXTURE_DIR / "python_simple.diff").read_text()
        with _patch_ollama("[]"):
            graph = build_review_graph()
            result = graph.invoke({"raw_diff": raw_diff, "pr_url": ""})
        assert "No issues found" in result["final_output"]
        assert result["agent_reviews"]["quality"] == []
