"""Tests for codesentinel.agents.base — the shared BaseAgent contract.

Every concrete agent (Quality, Security, Performance, Architecture) inherits
this behaviour, so the contract tests here are the foundation for all 4.
LLM calls are mocked throughout; live integration belongs in test_quality.py.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from codesentinel.agents.base import BaseAgent
from codesentinel.models import Change, FileDiff, Hunk, ReviewItem, Severity


# ---------------------------------------------------------------------------
# Test agent — a minimal BaseAgent subclass used to exercise the base contract
# ---------------------------------------------------------------------------


class _DummyAgent(BaseAgent):
    name = "dummy"
    default_system_prompt = "You are a dummy reviewer. Return JSON."


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


def _mock_llm_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    llm = MagicMock()
    llm.invoke.return_value = resp
    return llm


# ---------------------------------------------------------------------------
# Class-level constraints — subclasses must declare name + prompt
# ---------------------------------------------------------------------------


class TestBaseAgentSubclassContract:
    """A subclass without `name` or `default_system_prompt` must fail loudly."""

    def test_missing_name_raises(self) -> None:
        class NoName(BaseAgent):
            default_system_prompt = "x"

        with pytest.raises(TypeError, match="name"):
            NoName()

    def test_missing_default_prompt_raises_without_override(self) -> None:
        class NoPrompt(BaseAgent):
            name = "noprompt"

        with pytest.raises(TypeError, match="default_system_prompt"):
            NoPrompt()

    def test_can_pass_system_prompt_to_override_default(self) -> None:
        class NoPrompt(BaseAgent):
            name = "noprompt"

        # If the subclass has no default but the caller provides one, that's fine.
        agent = NoPrompt(system_prompt="Custom prompt")
        assert agent.system_prompt == "Custom prompt"

    def test_constructor_defaults_pull_from_class_and_config(self) -> None:
        agent = _DummyAgent()
        assert agent.name == "dummy"
        assert agent.system_prompt == _DummyAgent.default_system_prompt
        assert 0.0 <= agent.confidence_threshold <= 1.0

    def test_constructor_accepts_overrides(self) -> None:
        agent = _DummyAgent(
            model="custom-model",
            temperature=0.5,
            system_prompt="custom prompt",
            confidence_threshold=0.9,
        )
        assert agent.model == "custom-model"
        assert agent.temperature == 0.5
        assert agent.system_prompt == "custom prompt"
        assert agent.confidence_threshold == 0.9


# ---------------------------------------------------------------------------
# Diff rendering — line-number prefixes prevent hallucination
# ---------------------------------------------------------------------------


class TestDiffRendering:
    def test_diff_blob_includes_filename_and_language(self) -> None:
        agent = _DummyAgent()
        blob = agent._render_diff_blob([_sample_diff()])
        assert "src/api/routes.py" in blob
        assert "(python)" in blob

    def test_each_line_is_prefixed_with_its_new_file_number(self) -> None:
        """The hallucination-prevention measure — line numbers visible to the LLM."""
        agent = _DummyAgent()
        blob = agent._render_diff_blob([_sample_diff()])
        # The context line is at new-file line 10, the add at 11.
        assert "L  10 " in blob or "L 10 " in blob or "L10 " in blob
        assert "L  11 " in blob or "L 11 " in blob or "L11 " in blob

    def test_remove_lines_have_no_line_number_label(self) -> None:
        agent = _DummyAgent()
        blob = agent._render_diff_blob([_sample_diff()])
        # The remove line should still appear, with the placeholder label.
        assert "-    return db.query(uid)" in blob

    def test_truncates_hunks_beyond_max(self) -> None:
        agent = _DummyAgent()
        big = FileDiff(
            filename="big.py",
            language="python",
            hunks=[Hunk(old_start=i, new_start=i, changes=[]) for i in range(10)],
        )
        blob = agent._render_diff_blob([big], max_hunks_per_file=3)
        assert "7 more hunks truncated" in blob


# ---------------------------------------------------------------------------
# Code-fence stripping
# ---------------------------------------------------------------------------


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
    def test_strip_variants(self, raw: str, expected: str) -> None:
        assert BaseAgent._strip_code_fences(raw) == expected


# ---------------------------------------------------------------------------
# Response parsing — the various LLM output shapes
# ---------------------------------------------------------------------------


class TestParseResponse:
    def _valid_finding(self) -> dict:
        return {
            "file": "a.py",
            "line_start": 1,
            "severity": "info",
            "category": "naming",
            "message": "x",
        }

    def test_happy_path(self) -> None:
        agent = _DummyAgent()
        raw = json.dumps([self._valid_finding(), self._valid_finding()])
        items, err = agent._parse_response(raw)
        assert err is None
        assert len(items) == 2
        assert all(i.agent == "dummy" for i in items)

    def test_empty_array(self) -> None:
        agent = _DummyAgent()
        items, err = agent._parse_response("[]")
        assert err is None
        assert items == []

    def test_empty_dict_treated_as_empty_array(self) -> None:
        agent = _DummyAgent()
        items, err = agent._parse_response("{}")
        assert err is None
        assert items == []

    def test_single_finding_dict_wrapped(self) -> None:
        agent = _DummyAgent()
        items, err = agent._parse_response(json.dumps(self._valid_finding()))
        assert err is None
        assert len(items) == 1

    def test_findings_key_wrapped_list(self) -> None:
        agent = _DummyAgent()
        items, err = agent._parse_response(
            json.dumps({"findings": [self._valid_finding()]})
        )
        assert err is None
        assert len(items) == 1

    def test_invalid_json(self) -> None:
        agent = _DummyAgent()
        items, err = agent._parse_response("not json")
        assert items == []
        assert err is not None and "json_decode_error" in err

    def test_object_with_no_recognized_keys(self) -> None:
        agent = _DummyAgent()
        items, err = agent._parse_response(json.dumps({"unrelated": "data"}))
        assert items == []
        assert err == "expected_list_got_dict"

    def test_invalid_entries_dropped_but_valid_kept(self) -> None:
        agent = _DummyAgent()
        raw = json.dumps([self._valid_finding(), {"bogus": "entry"}, "string"])
        items, err = agent._parse_response(raw)
        assert err is None
        assert len(items) == 1


# ---------------------------------------------------------------------------
# Full review() flow — the public entrypoint, end-to-end
# ---------------------------------------------------------------------------


class TestReviewHappyPath:
    def test_empty_diff_skips_llm(self) -> None:
        agent = _DummyAgent()
        with patch("codesentinel.agents.base.ChatOllama") as ollama_cls:
            items, metadata = agent.review([])
        ollama_cls.assert_not_called()
        assert items == []
        assert metadata == {}

    def test_clean_response_returns_items(self) -> None:
        agent = _DummyAgent(confidence_threshold=0.0)
        payload = json.dumps(
            [
                {
                    "file": "src/api/routes.py",
                    "line_start": 11,
                    "severity": "critical",
                    "category": "security",
                    "message": "issue",
                    "confidence": 0.9,
                }
            ]
        )
        with patch(
            "codesentinel.agents.base.ChatOllama",
            return_value=_mock_llm_response(payload),
        ):
            items, metadata = agent.review([_sample_diff()])
        assert len(items) == 1
        assert items[0].agent == "dummy"
        assert metadata == {}


class TestReviewConfidenceFiltering:
    def test_drops_findings_below_threshold(self) -> None:
        agent = _DummyAgent(confidence_threshold=0.8)
        payload = json.dumps(
            [
                {
                    "file": "src/api/routes.py",
                    "line_start": 11,
                    "severity": "info",
                    "category": "naming",
                    "message": "low",
                    "confidence": 0.4,
                },
                {
                    "file": "src/api/routes.py",
                    "line_start": 11,
                    "severity": "critical",
                    "category": "x",
                    "message": "high",
                    "confidence": 0.95,
                },
            ]
        )
        with patch(
            "codesentinel.agents.base.ChatOllama",
            return_value=_mock_llm_response(payload),
        ):
            items, _ = agent.review([_sample_diff()])
        assert len(items) == 1
        assert items[0].message == "high"

    def test_zero_threshold_keeps_everything(self) -> None:
        agent = _DummyAgent(confidence_threshold=0.0)
        payload = json.dumps(
            [
                {
                    "file": "src/api/routes.py",
                    "line_start": 11,
                    "severity": "info",
                    "category": "x",
                    "message": "y",
                    "confidence": 0.1,
                }
            ]
        )
        with patch(
            "codesentinel.agents.base.ChatOllama",
            return_value=_mock_llm_response(payload),
        ):
            items, _ = agent.review([_sample_diff()])
        assert len(items) == 1


class TestReviewLineNumberHallucination:
    def test_drops_finding_with_line_far_beyond_diff(self) -> None:
        """If the LLM invents `line_start: 9999` on a file whose diff stops at line 11,
        the sanitiser drops it. Within +50 of the max line is tolerated to account for
        normal LLM nearby-line guesses."""
        agent = _DummyAgent(confidence_threshold=0.0)
        payload = json.dumps(
            [
                {
                    "file": "src/api/routes.py",
                    "line_start": 9999,
                    "severity": "info",
                    "category": "x",
                    "message": "hallucinated",
                    "confidence": 0.9,
                },
                {
                    "file": "src/api/routes.py",
                    "line_start": 11,
                    "severity": "info",
                    "category": "x",
                    "message": "real",
                    "confidence": 0.9,
                },
            ]
        )
        with patch(
            "codesentinel.agents.base.ChatOllama",
            return_value=_mock_llm_response(payload),
        ):
            items, metadata = agent.review([_sample_diff()])
        assert len(items) == 1
        assert items[0].message == "real"
        assert metadata["dummy_dropped_hallucinated"] == 1


# ---------------------------------------------------------------------------
# Repair retry — the LLM returns junk on attempt 1, valid JSON on attempt 2
# ---------------------------------------------------------------------------


class TestReviewRepairRetry:
    def test_retries_with_repair_prompt_on_parse_failure(self) -> None:
        agent = _DummyAgent(confidence_threshold=0.0, max_parse_retries=2)
        valid_payload = json.dumps(
            [
                {
                    "file": "src/api/routes.py",
                    "line_start": 11,
                    "severity": "info",
                    "category": "x",
                    "message": "good",
                    "confidence": 0.9,
                }
            ]
        )

        # First call returns junk, second call returns valid JSON.
        junk_resp = MagicMock()
        junk_resp.content = "this is not json"
        good_resp = MagicMock()
        good_resp.content = valid_payload
        llm = MagicMock()
        llm.invoke.side_effect = [junk_resp, good_resp]

        with patch("codesentinel.agents.base.ChatOllama", return_value=llm):
            items, metadata = agent.review([_sample_diff()])

        assert len(items) == 1
        assert metadata["dummy_parse_retries"] == 1
        # Two invocations: original + one repair.
        assert llm.invoke.call_count == 2

    def test_exhausts_retries_and_returns_empty(self) -> None:
        agent = _DummyAgent(confidence_threshold=0.0, max_parse_retries=2)
        bad_resp = MagicMock()
        bad_resp.content = "still not json"
        llm = MagicMock()
        llm.invoke.return_value = bad_resp

        with patch("codesentinel.agents.base.ChatOllama", return_value=llm):
            items, metadata = agent.review([_sample_diff()])

        assert items == []
        # Original + 2 repair attempts.
        assert llm.invoke.call_count == 3
        assert "dummy_error" in metadata


# ---------------------------------------------------------------------------
# Network retry — Ollama transport errors get exponential backoff
# ---------------------------------------------------------------------------


class TestReviewNetworkRetry:
    def test_retries_on_network_error_then_succeeds(self) -> None:
        agent = _DummyAgent(confidence_threshold=0.0, max_network_retries=2)
        good_payload = json.dumps([])
        good_resp = MagicMock()
        good_resp.content = good_payload
        llm = MagicMock()
        llm.invoke.side_effect = [ConnectionError("transient"), good_resp]

        with (
            patch("codesentinel.agents.base.ChatOllama", return_value=llm),
            patch("codesentinel.agents.base.time.sleep") as sleep_mock,
        ):
            items, metadata = agent.review([_sample_diff()])

        assert items == []
        assert metadata == {}
        sleep_mock.assert_called_once()  # one backoff between the two attempts

    def test_exhausts_network_retries_and_records_metadata(self) -> None:
        agent = _DummyAgent(max_network_retries=2)
        llm = MagicMock()
        llm.invoke.side_effect = ConnectionError("ollama down")

        with (
            patch("codesentinel.agents.base.ChatOllama", return_value=llm),
            patch("codesentinel.agents.base.time.sleep"),
        ):
            items, metadata = agent.review([_sample_diff()])

        assert items == []
        assert "llm_call_failed" in metadata["dummy_error"]
        # 1 initial + 2 retries = 3 attempts.
        assert llm.invoke.call_count == 3
