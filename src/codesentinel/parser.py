"""GitHub PR diff parser.

Fetches a PR's changed files (or accepts a raw unified diff string) and parses
them into the structured ``FileDiff`` / ``Hunk`` / ``Change`` models defined in
``codesentinel.models``. This is the input stage of the review pipeline — every
agent downstream reads from these objects, so the parsing must preserve hunk
boundaries, line numbers, and surrounding context lines accurately.

Public entry point:
    parse_pr(pr_url=..., raw_diff=...) -> list[FileDiff]
"""

from __future__ import annotations

import re

import structlog
from github import Auth, Github
from github.GithubException import GithubException

from codesentinel.config import GITHUB_TOKEN
from codesentinel.models import Change, FileDiff, Hunk

log = structlog.get_logger()


LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".sql": "sql",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
}

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_PR_URL_RE = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")
_DIFF_GIT_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)


def detect_language(filename: str) -> str:
    """Return a coarse language label for ``filename`` based on its extension.

    Falls back to ``"unknown"`` for unrecognised extensions. The label is used
    by agents to tune prompts (e.g. SQL-injection checks only on .py/.js, not
    on .md). Case-insensitive on the extension.
    """
    lowered = filename.lower()
    # Sort by length so multi-char extensions like .tsx beat .ts when both match.
    for ext in sorted(LANGUAGE_BY_EXTENSION, key=len, reverse=True):
        if lowered.endswith(ext):
            return LANGUAGE_BY_EXTENSION[ext]
    return "unknown"


def parse_patch(patch: str) -> list[Hunk]:
    """Parse a single file's unified-diff patch body into ``Hunk`` objects.

    ``patch`` is the body returned by the GitHub API for a single file — it
    starts with a ``@@ -... +... @@`` hunk header, contains ``+``/``-``/`` ``
    prefixed change lines, and may contain multiple hunks. ``new_line``
    increments on adds and context (the destination file), so ``Change.line_number``
    reflects the line as it appears in the post-merge file. Removed lines have
    ``line_number=None`` because they don't exist in the new file.
    """
    if not patch:
        return []

    hunks: list[Hunk] = []
    current: Hunk | None = None
    new_line = 0

    for raw_line in patch.splitlines():
        header = _HUNK_HEADER_RE.match(raw_line)
        if header:
            old_start = int(header.group(1))
            new_start = int(header.group(2))
            current = Hunk(old_start=old_start, new_start=new_start, changes=[])
            hunks.append(current)
            new_line = new_start
            continue

        if current is None:
            continue

        if raw_line.startswith("\\"):
            # `\ No newline at end of file` marker — not a real change.
            continue

        if raw_line.startswith("+"):
            current.changes.append(
                Change(type="add", content=raw_line[1:], line_number=new_line)
            )
            new_line += 1
        elif raw_line.startswith("-"):
            current.changes.append(
                Change(type="remove", content=raw_line[1:], line_number=None)
            )
        elif raw_line.startswith(" "):
            current.changes.append(
                Change(type="context", content=raw_line[1:], line_number=new_line)
            )
            new_line += 1
        elif raw_line == "":
            # Treat a bare empty line inside a hunk as empty context.
            current.changes.append(
                Change(type="context", content="", line_number=new_line)
            )
            new_line += 1

    return hunks


def parse_raw_diff(diff_text: str) -> list[FileDiff]:
    """Parse a multi-file ``git diff`` output into a list of ``FileDiff``.

    Recognises ``diff --git a/PATH b/PATH`` boundaries between files, the
    ``new file mode`` / ``deleted file mode`` / ``rename from`` markers for
    status detection, and the ``Binary files ... differ`` marker for binary
    files (which are skipped). Each file's hunks are parsed via ``parse_patch``.
    """
    if not diff_text or not diff_text.strip():
        return []

    files: list[FileDiff] = []
    # Use the header regex to find every file's start offset, then slice between them.
    headers = list(_DIFF_GIT_HEADER_RE.finditer(diff_text))
    if not headers:
        return []

    for idx, header in enumerate(headers):
        start = header.end()
        end = headers[idx + 1].start() if idx + 1 < len(headers) else len(diff_text)
        section = diff_text[start:end]
        a_path, b_path = header.group(1), header.group(2)

        status = "modified"
        if re.search(r"^new file mode", section, re.MULTILINE):
            status = "added"
        elif re.search(r"^deleted file mode", section, re.MULTILINE):
            status = "removed"
        elif re.search(r"^rename (from|to)", section, re.MULTILINE):
            status = "renamed"

        if re.search(r"^Binary files .* differ", section, re.MULTILINE):
            log.info("parser_skip_binary", filename=b_path)
            continue

        # The actual patch body starts at the first hunk header.
        hunk_start = section.find("\n@@")
        patch_body = section[hunk_start + 1 :] if hunk_start != -1 else ""

        filename = b_path if status != "removed" else a_path
        files.append(
            FileDiff(
                filename=filename,
                language=detect_language(filename),
                status=status,
                hunks=parse_patch(patch_body),
            )
        )

    return files


def parse_pr_url(pr_url: str) -> tuple[str, str, int]:
    """Extract ``(owner, repo, pr_number)`` from a GitHub PR URL.

    Accepts URLs with or without scheme, with or without trailing path
    components. Raises ``ValueError`` if the URL is not a PR URL.
    """
    match = _PR_URL_RE.search(pr_url)
    if not match:
        raise ValueError(f"Not a GitHub PR URL: {pr_url}")
    return match.group(1), match.group(2), int(match.group(3))


def _file_to_diff(file_obj: object) -> FileDiff | None:
    """Convert a PyGithub ``File`` object to a ``FileDiff`` (or ``None`` for binary)."""
    filename: str = getattr(file_obj, "filename", "")
    patch: str | None = getattr(file_obj, "patch", None)
    status: str = getattr(file_obj, "status", "modified")
    previous: str | None = getattr(file_obj, "previous_filename", None)

    if patch is None:
        log.info("parser_skip_binary_or_empty", filename=filename, status=status)
        return None

    diff = FileDiff(
        filename=filename,
        language=detect_language(filename),
        status=status,
        hunks=parse_patch(patch),
    )
    if previous:
        log.info("parser_renamed_file", from_=previous, to=filename)
    return diff


def _fetch_pr_files(owner: str, repo: str, pr_number: int, token: str) -> list[FileDiff]:
    """Fetch a PR's changed files via the GitHub API and parse each one."""
    auth = Auth.Token(token) if token else None
    gh = Github(auth=auth) if auth else Github()
    try:
        repo_obj = gh.get_repo(f"{owner}/{repo}")
        pull = repo_obj.get_pull(pr_number)
        files = list(pull.get_files())
    except GithubException as exc:
        log.error(
            "parser_github_api_error",
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            status=exc.status,
            data=str(exc.data),
        )
        raise

    log.info("parser_fetched_pr", owner=owner, repo=repo, pr=pr_number, n_files=len(files))

    diffs: list[FileDiff] = []
    for f in files:
        parsed = _file_to_diff(f)
        if parsed is not None:
            diffs.append(parsed)
    return diffs


def parse_pr(
    pr_url: str | None = None,
    raw_diff: str | None = None,
    github_token: str | None = None,
) -> list[FileDiff]:
    """Parse a GitHub PR (by URL) or a raw unified diff into ``FileDiff`` objects.

    Args:
        pr_url: A GitHub PR URL like ``https://github.com/owner/repo/pull/123``.
            Mutually exclusive with ``raw_diff``.
        raw_diff: A complete ``git diff`` output covering one or more files,
            including ``diff --git`` headers. Useful for local testing without
            hitting the GitHub API. Mutually exclusive with ``pr_url``.
        github_token: Personal access token. Defaults to the ``GITHUB_TOKEN``
            env var loaded in ``config.py``. Required for private repos and
            recommended for public ones (lifts rate limit to 5000/hr).

    Returns:
        A list of ``FileDiff`` objects, one per changed text file. Binary files
        are skipped with a structlog ``parser_skip_binary`` event.

    Raises:
        ValueError: If neither or both of ``pr_url``/``raw_diff`` are provided,
            or if ``pr_url`` is not a recognisable GitHub PR URL.
        GithubException: If the GitHub API call fails (e.g. 404, 403).
    """
    if pr_url and raw_diff:
        raise ValueError("Provide either pr_url or raw_diff, not both")
    if not pr_url and not raw_diff:
        raise ValueError("Must provide pr_url or raw_diff")

    if raw_diff is not None:
        return parse_raw_diff(raw_diff)

    assert pr_url is not None  # for type narrowing
    owner, repo, pr_number = parse_pr_url(pr_url)
    token = github_token if github_token is not None else GITHUB_TOKEN
    return _fetch_pr_files(owner, repo, pr_number, token)
