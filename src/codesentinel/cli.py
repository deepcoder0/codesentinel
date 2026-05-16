"""CLI entrypoint — runs the review graph against a PR URL or a local diff file.

Used by ``make review URL=…`` and as the live integration harness for CS-003.

    python -m codesentinel.cli https://github.com/owner/repo/pull/123
    python -m codesentinel.cli --diff path/to/local.diff
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from codesentinel.graph.builder import build_review_graph

if TYPE_CHECKING:
    from codesentinel.graph.state import ReviewState

log = structlog.get_logger()


def _build_initial_state(pr_url: str | None, diff_path: str | None) -> ReviewState:
    if diff_path:
        raw = Path(diff_path).read_text()
        return {"pr_url": pr_url or "", "raw_diff": raw}
    if pr_url:
        return {"pr_url": pr_url}
    raise SystemExit("Provide either a PR URL or --diff <path>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="codesentinel",
        description="Run a CodeSentinel review against a PR URL or a local diff.",
    )
    parser.add_argument("pr_url", nargs="?", help="GitHub PR URL")
    parser.add_argument("--diff", help="Path to a local unified-diff file (skips GitHub API)")
    args = parser.parse_args(argv)

    state = _build_initial_state(args.pr_url, args.diff)

    log.info("cli_invoke_start", pr_url=state.get("pr_url"), has_raw_diff=bool(state.get("raw_diff")))
    graph = build_review_graph()
    result = graph.invoke(state)

    print(result["final_output"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
