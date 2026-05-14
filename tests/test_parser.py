"""Tests for codesentinel.parser — unified-diff and GitHub PR parsing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from codesentinel.models import FileDiff
from codesentinel.parser import (
    detect_language,
    parse_patch,
    parse_pr,
    parse_pr_url,
    parse_raw_diff,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


class TestDetectLanguage:
    """detect_language maps file extensions to language labels."""

    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("foo.py", "python"),
            ("Foo.PY", "python"),
            ("bar.js", "javascript"),
            ("bar.jsx", "javascript"),
            ("comp.ts", "typescript"),
            ("comp.tsx", "typescript"),
            ("main.go", "go"),
            ("lib.rs", "rust"),
            ("Main.java", "java"),
            ("page.html", "html"),
            ("config.yml", "yaml"),
            ("config.yaml", "yaml"),
            ("notes.md", "markdown"),
            ("Makefile", "unknown"),
            ("no_extension", "unknown"),
            ("weird.xyz", "unknown"),
        ],
    )
    def test_extension_mapping(self, filename: str, expected: str) -> None:
        assert detect_language(filename) == expected

    def test_tsx_beats_ts(self) -> None:
        # Both ".ts" and ".tsx" would technically match "foo.tsx" via endswith;
        # the longer extension should win.
        assert detect_language("foo.tsx") == "typescript"


class TestParsePatch:
    """parse_patch handles a single file's hunk body."""

    def test_empty_patch_returns_empty(self) -> None:
        assert parse_patch("") == []
        assert parse_patch(None) == []  # type: ignore[arg-type]

    def test_basic_single_hunk(self) -> None:
        patch = (
            "@@ -10,3 +10,4 @@\n"
            " def foo():\n"
            "-    return 1\n"
            "+    return 2\n"
            "+    # added\n"
            " # end\n"
        )
        hunks = parse_patch(patch)
        assert len(hunks) == 1
        h = hunks[0]
        assert h.old_start == 10
        assert h.new_start == 10
        assert [c.type for c in h.changes] == ["context", "remove", "add", "add", "context"]

    def test_line_numbers_track_new_file(self) -> None:
        patch = (
            "@@ -10,3 +20,4 @@\n"
            " ctx1\n"
            "-removed\n"
            "+added1\n"
            "+added2\n"
            " ctx2\n"
        )
        h = parse_patch(patch)[0]
        nums = [(c.type, c.line_number) for c in h.changes]
        # new_start is 20; context starts at 20, removed has no new-file line,
        # adds get 21 and 22, trailing context is 23.
        assert nums == [
            ("context", 20),
            ("remove", None),
            ("add", 21),
            ("add", 22),
            ("context", 23),
        ]

    def test_multiple_hunks(self) -> None:
        hunks = parse_patch(_load("multi_hunk.diff").split("@@", 1)[1])
        # Re-parse the whole patch body from the fixture (strip diff --git header).
        full = _load("multi_hunk.diff")
        body = full[full.index("@@") :]
        hunks = parse_patch(body)
        assert len(hunks) == 2
        assert hunks[0].new_start == 1
        assert hunks[1].new_start == 20

    def test_skips_no_newline_marker(self) -> None:
        patch = (
            "@@ -1,2 +1,2 @@\n"
            " x\n"
            "-y\n"
            "+z\n"
            "\\ No newline at end of file\n"
        )
        h = parse_patch(patch)[0]
        types = [c.type for c in h.changes]
        assert "remove" in types and "add" in types
        assert all(not c.content.startswith("\\") for c in h.changes)


class TestParseRawDiff:
    """parse_raw_diff handles multi-file git diff output."""

    def test_single_python_file(self) -> None:
        diffs = parse_raw_diff(_load("python_simple.diff"))
        assert len(diffs) == 1
        d = diffs[0]
        assert d.filename == "src/api/routes.py"
        assert d.language == "python"
        assert d.status == "modified"
        assert len(d.hunks) == 1
        # Confirms ±3 context lines are preserved from the unified diff.
        assert sum(1 for c in d.hunks[0].changes if c.type == "context") >= 3

    def test_multifile_diff_finds_text_files(self) -> None:
        diffs = parse_raw_diff(_load("multifile.diff"))
        # Expect: auth.py (modified), index.js (modified), README.md→docs/README.md
        # (renamed), src/legacy.py (removed). Binary logo.png is skipped.
        filenames = {d.filename for d in diffs}
        assert "src/auth.py" in filenames
        assert "web/index.js" in filenames
        assert "docs/README.md" in filenames
        assert "src/legacy.py" in filenames
        assert "logo.png" not in filenames

    def test_language_detection_across_files(self) -> None:
        diffs = parse_raw_diff(_load("multifile.diff"))
        by_name = {d.filename: d for d in diffs}
        assert by_name["src/auth.py"].language == "python"
        assert by_name["web/index.js"].language == "javascript"
        assert by_name["docs/README.md"].language == "markdown"

    def test_status_detection(self) -> None:
        diffs = parse_raw_diff(_load("multifile.diff"))
        by_name = {d.filename: d for d in diffs}
        assert by_name["src/auth.py"].status == "modified"
        assert by_name["docs/README.md"].status == "renamed"
        assert by_name["src/legacy.py"].status == "removed"

    def test_empty_and_blank_input(self) -> None:
        assert parse_raw_diff("") == []
        assert parse_raw_diff("   \n\n") == []

    def test_input_without_diff_headers_returns_empty(self) -> None:
        assert parse_raw_diff("just some random text\nwith no headers") == []


class TestParsePrUrl:
    """parse_pr_url splits a GitHub PR URL into owner/repo/number."""

    def test_https_url(self) -> None:
        assert parse_pr_url("https://github.com/foo/bar/pull/42") == ("foo", "bar", 42)

    def test_http_url(self) -> None:
        assert parse_pr_url("http://github.com/foo/bar/pull/7") == ("foo", "bar", 7)

    def test_url_with_trailing_path(self) -> None:
        assert parse_pr_url("https://github.com/foo/bar/pull/99/files") == ("foo", "bar", 99)

    def test_invalid_url_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_pr_url("https://example.com/not/a/pr")
        with pytest.raises(ValueError):
            parse_pr_url("https://github.com/foo/bar/issues/1")


class TestParsePr:
    """parse_pr is the top-level dispatcher."""

    def test_requires_one_of_url_or_raw_diff(self) -> None:
        with pytest.raises(ValueError):
            parse_pr()
        with pytest.raises(ValueError):
            parse_pr(pr_url="x", raw_diff="y")

    def test_dispatches_to_raw_diff(self) -> None:
        diffs = parse_pr(raw_diff=_load("python_simple.diff"))
        assert len(diffs) == 1
        assert diffs[0].filename == "src/api/routes.py"

    def test_fetches_via_github_when_given_url(self) -> None:
        """parse_pr should call PyGithub when given a pr_url."""
        fake_file = MagicMock()
        fake_file.filename = "src/api/routes.py"
        fake_file.status = "modified"
        fake_file.previous_filename = None
        fake_file.patch = (
            "@@ -1,2 +1,3 @@\n"
            " x\n"
            "+y\n"
            " z\n"
        )

        fake_binary = MagicMock()
        fake_binary.filename = "logo.png"
        fake_binary.status = "added"
        fake_binary.previous_filename = None
        fake_binary.patch = None  # binary files have no patch

        fake_pr = MagicMock()
        fake_pr.get_files.return_value = [fake_file, fake_binary]
        fake_repo = MagicMock()
        fake_repo.get_pull.return_value = fake_pr
        fake_gh = MagicMock()
        fake_gh.get_repo.return_value = fake_repo

        with patch("codesentinel.parser.Github", return_value=fake_gh) as gh_cls:
            diffs = parse_pr(
                pr_url="https://github.com/foo/bar/pull/123",
                github_token="fake-token",
            )

        gh_cls.assert_called_once()
        fake_gh.get_repo.assert_called_once_with("foo/bar")
        fake_repo.get_pull.assert_called_once_with(123)
        # Binary file is skipped; only the text file makes it through.
        assert len(diffs) == 1
        assert isinstance(diffs[0], FileDiff)
        assert diffs[0].filename == "src/api/routes.py"
        assert diffs[0].language == "python"

    def test_github_renamed_file_is_preserved(self) -> None:
        fake_file = MagicMock()
        fake_file.filename = "docs/README.md"
        fake_file.previous_filename = "README.md"
        fake_file.status = "renamed"
        fake_file.patch = "@@ -1,1 +1,1 @@\n-old\n+new\n"

        fake_pr = MagicMock()
        fake_pr.get_files.return_value = [fake_file]
        fake_repo = MagicMock()
        fake_repo.get_pull.return_value = fake_pr
        fake_gh = MagicMock()
        fake_gh.get_repo.return_value = fake_repo

        with patch("codesentinel.parser.Github", return_value=fake_gh):
            diffs = parse_pr(
                pr_url="https://github.com/foo/bar/pull/9",
                github_token="t",
            )

        assert len(diffs) == 1
        assert diffs[0].filename == "docs/README.md"
        assert diffs[0].status == "renamed"
