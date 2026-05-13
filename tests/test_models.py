"""Tests for CodeSentinel Pydantic models — verifies schema validation works correctly."""

from __future__ import annotations

import pytest

from codesentinel.models import (
    A2AMessage,
    Change,
    FileDiff,
    Hunk,
    QueryType,
    ReviewItem,
    Severity,
)


class TestFileDiff:
    """Tests for the FileDiff model."""

    def test_basic_file_diff_creation(self) -> None:
        diff = FileDiff(filename="test.py", language="python")
        assert diff.filename == "test.py"
        assert diff.language == "python"
        assert diff.hunks == []
        assert diff.status == "modified"

    def test_file_diff_with_hunks(self, sample_python_diff: FileDiff) -> None:
        assert sample_python_diff.filename == "src/api/routes.py"
        assert len(sample_python_diff.hunks) == 1
        assert len(sample_python_diff.hunks[0].changes) == 4

    def test_file_diff_defaults(self) -> None:
        diff = FileDiff(filename="unknown_file")
        assert diff.language == "unknown"
        assert diff.status == "modified"


class TestReviewItem:
    """Tests for the ReviewItem model."""

    def test_basic_review_item(self, sample_review_item: ReviewItem) -> None:
        assert sample_review_item.severity == Severity.CRITICAL
        assert sample_review_item.agent == "security"
        assert sample_review_item.confidence == 0.92

    def test_confidence_bounds(self) -> None:
        with pytest.raises(Exception):
            ReviewItem(
                file="test.py",
                line_start=1,
                severity=Severity.INFO,
                category="test",
                message="test",
                confidence=1.5,  # Out of bounds
                agent="quality",
            )

    def test_severity_enum_values(self) -> None:
        assert Severity.CRITICAL.value == "critical"
        assert Severity.WARNING.value == "warning"
        assert Severity.INFO.value == "info"

    def test_review_item_serialization(self, sample_review_item: ReviewItem) -> None:
        data = sample_review_item.model_dump()
        assert isinstance(data, dict)
        assert data["severity"] == "critical"
        assert data["agent"] == "security"

        # Round-trip
        restored = ReviewItem.model_validate(data)
        assert restored.file == sample_review_item.file


class TestA2AMessage:
    """Tests for the A2AMessage model."""

    def test_basic_a2a_message(self) -> None:
        msg = A2AMessage(
            from_agent="security",
            to_agent="architecture",
            query_type=QueryType.CONVENTION_CHECK,
            query="Is hardcoded config a team convention violation?",
        )
        assert msg.from_agent == "security"
        assert msg.response is None
        assert msg.priority == 1

    def test_query_type_enum(self) -> None:
        assert QueryType.CONVENTION_CHECK.value == "convention_check"
        assert QueryType.PATTERN_VERIFY.value == "pattern_verify"
        assert QueryType.CONTEXT_REQUEST.value == "context_request"
        assert QueryType.IMPACT_ASSESS.value == "impact_assess"
