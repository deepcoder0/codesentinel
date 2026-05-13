"""Shared pytest fixtures for CodeSentinel tests."""

from __future__ import annotations

import pytest

from codesentinel.models import Change, FileDiff, Hunk, ReviewItem, Severity


@pytest.fixture
def sample_python_diff() -> FileDiff:
    """A sample Python file diff with a few changes."""
    return FileDiff(
        filename="src/api/routes.py",
        language="python",
        status="modified",
        hunks=[
            Hunk(
                old_start=10,
                new_start=10,
                changes=[
                    Change(type="context", content="def get_user(user_id):", line_number=10),
                    Change(type="remove", content="    return db.query(user_id)", line_number=11),
                    Change(
                        type="add",
                        content='    return db.execute(f"SELECT * FROM users WHERE id={user_id}")',
                        line_number=11,
                    ),
                    Change(type="context", content="", line_number=12),
                ],
            )
        ],
    )


@pytest.fixture
def sample_review_item() -> ReviewItem:
    """A sample review finding."""
    return ReviewItem(
        file="src/api/routes.py",
        line_start=11,
        severity=Severity.CRITICAL,
        category="sql_injection",
        message="SQL injection risk: user input interpolated directly into query string",
        suggestion="Use parameterized queries: db.execute('SELECT * FROM users WHERE id=?', (user_id,))",
        confidence=0.92,
        agent="security",
    )
