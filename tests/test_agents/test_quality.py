"""Tests for codesentinel.agents.quality — the Quality Agent class.

Most behaviour comes from BaseAgent (covered in test_base.py). This file
verifies the subclass-specific bits: prompt content, class attributes,
and integration via the BaseAgent.review() path with mocked LLM responses.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from codesentinel.agents.quality import QUALITY_SYSTEM_PROMPT, QualityAgent
from codesentinel.models import Change, FileDiff, Hunk


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
                    Change(type="context", content="def f(x):", line_number=10),
                    Change(type="add", content="    return x + 1", line_number=11),
                ],
            )
        ],
    )


def _mock_llm(content: str) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    llm = MagicMock()
    llm.invoke.return_value = resp
    return llm


class TestQualityAgentClassAttrs:
    """The class is the public API — verify its declared shape."""

    def test_name(self) -> None:
        assert QualityAgent.name == "quality"

    def test_has_default_prompt(self) -> None:
        assert QualityAgent.default_system_prompt is QUALITY_SYSTEM_PROMPT
        assert len(QUALITY_SYSTEM_PROMPT) > 500  # nontrivial

    def test_default_confidence_threshold_is_0_6(self) -> None:
        assert QualityAgent.confidence_threshold == 0.6


class TestQualityAgentPromptContents:
    """The v1 prompt must contain the calibration scaffolding — these tests
    lock the structural promises in place so accidental edits don't strip them.
    """

    def test_prompt_includes_role_anchor(self) -> None:
        assert "senior" in QUALITY_SYSTEM_PROMPT.lower()
        assert "code quality" in QUALITY_SYSTEM_PROMPT.lower()

    def test_prompt_excludes_other_agent_domains(self) -> None:
        # The "out of scope" section is crucial for keeping the agents separate.
        prompt_lower = QUALITY_SYSTEM_PROMPT.lower()
        assert "out of scope" in prompt_lower
        assert "security" in prompt_lower
        assert "performance" in prompt_lower

    def test_prompt_includes_three_few_shot_examples(self) -> None:
        # Severity calibration mechanism — without these the model labels
        # everything "warning". One example per severity level.
        assert QUALITY_SYSTEM_PROMPT.count("EXAMPLE ") >= 3
        assert '"severity":"critical"' in QUALITY_SYSTEM_PROMPT
        assert '"severity":"warning"' in QUALITY_SYSTEM_PROMPT
        assert '"severity":"info"' in QUALITY_SYSTEM_PROMPT

    def test_prompt_specifies_json_array_output(self) -> None:
        assert "JSON array" in QUALITY_SYSTEM_PROMPT
        assert "[]" in QUALITY_SYSTEM_PROMPT

    def test_prompt_references_line_number_prefix(self) -> None:
        # The L-prefix convention is what prevents line-number hallucination.
        assert "L-prefixed" in QUALITY_SYSTEM_PROMPT or "L  " in QUALITY_SYSTEM_PROMPT


class TestQualityAgentReview:
    """End-to-end via review() with mocked LLM — verifies wiring through BaseAgent."""

    def _valid_finding(self, severity: str = "info") -> dict:
        return {
            "file": "src/api/routes.py",
            "line_start": 11,
            "severity": severity,
            "category": "naming",
            "message": "Single-letter function name `f`.",
            "suggestion": "Use a descriptive name.",
            "confidence": 0.85,
        }

    def test_uses_default_prompt(self) -> None:
        agent = QualityAgent()
        assert agent.system_prompt == QUALITY_SYSTEM_PROMPT

    def test_findings_get_quality_agent_stamp(self) -> None:
        agent = QualityAgent()
        payload = json.dumps([self._valid_finding()])
        with patch(
            "codesentinel.agents.base.ChatOllama", return_value=_mock_llm(payload)
        ):
            items, _metadata = agent.review([_sample_diff()])
        assert len(items) == 1
        assert items[0].agent == "quality"

    def test_clean_response_returns_no_findings(self) -> None:
        agent = QualityAgent()
        with patch(
            "codesentinel.agents.base.ChatOllama", return_value=_mock_llm("[]")
        ):
            items, _ = agent.review([_sample_diff()])
        assert items == []

    def test_confidence_filter_at_0_6_drops_low_confidence(self) -> None:
        agent = QualityAgent()
        low = self._valid_finding()
        low["confidence"] = 0.3
        high = self._valid_finding()
        high["confidence"] = 0.9
        payload = json.dumps([low, high])
        with patch(
            "codesentinel.agents.base.ChatOllama", return_value=_mock_llm(payload)
        ):
            items, _ = agent.review([_sample_diff()])
        assert len(items) == 1
        assert items[0].confidence == 0.9

    def test_constructor_overrides_apply(self) -> None:
        agent = QualityAgent(
            model="custom-model",
            temperature=0.5,
            confidence_threshold=0.0,
            system_prompt="custom prompt for this test only",
        )
        assert agent.model == "custom-model"
        assert agent.temperature == 0.5
        assert agent.confidence_threshold == 0.0
        assert agent.system_prompt == "custom prompt for this test only"
