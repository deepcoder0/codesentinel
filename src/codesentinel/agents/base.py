"""BaseAgent — abstract base shared by all 4 specialised review agents.

Each agent (Quality, Security, Performance, Architecture) subclasses this and
provides:
- ``name``: the agent's identifier (used as the key in ``agent_reviews``).
- ``default_system_prompt``: a class-level prompt template.
- Optionally overrides ``confidence_threshold`` if a different filter level
  makes sense for the domain (e.g. Security might run looser to catch more).

The base class handles the boring parts: rendering the diff for the LLM,
calling Ollama with retry, parsing the JSON response (with a repair retry on
malformed output), validating into ``ReviewItem``, filtering by confidence,
and stamping ``agent=self.name`` on every finding. Subclasses stay tiny.
"""

from __future__ import annotations

import json
import re
import time
from abc import ABC
from typing import Any, ClassVar

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from pydantic import ValidationError

from codesentinel.config import LLM_TEMPERATURE, OLLAMA_HOST, OLLAMA_MODEL
from codesentinel.models import FileDiff, ReviewItem

log = structlog.get_logger()

_LEADING_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?", re.IGNORECASE)
_TRAILING_FENCE_RE = re.compile(r"\n?```\s*$")


class BaseAgent(ABC):
    """Abstract base for all review agents.

    Concrete subclasses must set ``name`` and ``default_system_prompt`` as
    class attributes. They may override ``confidence_threshold``.
    """

    name: ClassVar[str] = ""
    default_system_prompt: ClassVar[str] = ""
    confidence_threshold: ClassVar[float] = 0.6

    def __init__(
        self,
        model: str | None = None,
        temperature: float | None = None,
        system_prompt: str | None = None,
        confidence_threshold: float | None = None,
        max_parse_retries: int = 2,
        max_network_retries: int = 2,
    ) -> None:
        if not self.name:
            raise TypeError(
                f"{type(self).__name__} must set a class-level `name` attribute"
            )
        if not (system_prompt or self.default_system_prompt):
            raise TypeError(
                f"{type(self).__name__} must set `default_system_prompt` or pass "
                "`system_prompt=` to the constructor"
            )
        self.model = model or OLLAMA_MODEL
        self.temperature = temperature if temperature is not None else LLM_TEMPERATURE
        self.system_prompt = system_prompt or self.default_system_prompt
        self.confidence_threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else self.confidence_threshold
        )
        self.max_parse_retries = max_parse_retries
        self.max_network_retries = max_network_retries

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------

    def review(
        self, diff_files: list[FileDiff], pr_url: str = ""
    ) -> tuple[list[ReviewItem], dict[str, Any]]:
        """Run the agent against a parsed diff.

        Returns ``(findings, metadata)``. ``findings`` is already filtered by
        confidence and stamped with ``agent=self.name``. ``metadata`` contains
        any error or retry information — empty dict if everything was clean.
        """
        if not diff_files:
            log.info(f"{self.name}_agent_no_diff")
            return [], {}

        log.info(
            f"{self.name}_agent_start", n_files=len(diff_files), model=self.model
        )

        diff_blob = self._render_diff_blob(diff_files)
        user_msg = self._build_user_message(diff_blob, pr_url)
        max_line_by_file = self._max_line_numbers(diff_files)

        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=user_msg),
        ]

        metadata: dict[str, Any] = {}
        content, network_error = self._call_llm_with_retry(messages)
        if network_error:
            metadata[f"{self.name}_error"] = f"llm_call_failed: {network_error}"
            return [], metadata

        items, parse_error = self._parse_with_repair(content, messages, metadata)
        items = self._sanitize_line_numbers(items, max_line_by_file, metadata)
        items = self._filter_by_confidence(items)

        log.info(f"{self.name}_agent_done", n_findings=len(items))
        if parse_error and not metadata.get(f"{self.name}_error"):
            metadata[f"{self.name}_error"] = parse_error
        return items, metadata

    # ------------------------------------------------------------------
    # Steps — overridable, but the defaults are good
    # ------------------------------------------------------------------

    def _build_user_message(self, diff_blob: str, pr_url: str) -> str:
        return (
            f"PR: {pr_url or '<local>'}\n"
            f"Files changed:\n\n{diff_blob}\n\n"
            "Review the changes above. Output JSON only."
        )

    def _render_diff_blob(
        self, diff_files: list[FileDiff], max_hunks_per_file: int = 5
    ) -> str:
        """Render parsed diffs as text for the prompt.

        Each non-context line is prefixed with its NEW-file line number. This
        massively reduces line-number hallucination — the model literally sees
        ``L42: bad code`` and is far less likely to invent ``line_start: 137``.
        """
        parts: list[str] = []
        for f in diff_files:
            parts.append(f"=== {f.filename} ({f.language}) ===")
            for hunk in f.hunks[:max_hunks_per_file]:
                parts.append(f"@@ -{hunk.old_start} +{hunk.new_start} @@")
                for ch in hunk.changes:
                    prefix_sym = {"add": "+", "remove": "-", "context": " "}.get(
                        ch.type, " "
                    )
                    line_label = (
                        f"L{ch.line_number:>4} " if ch.line_number is not None else "L     "
                    )
                    parts.append(f"{line_label}{prefix_sym}{ch.content}")
            if len(f.hunks) > max_hunks_per_file:
                parts.append(
                    f"... ({len(f.hunks) - max_hunks_per_file} more hunks truncated)"
                )
            parts.append("")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # LLM call with exponential backoff
    # ------------------------------------------------------------------

    def _call_llm_with_retry(
        self, messages: list[Any]
    ) -> tuple[str, str | None]:
        """Call Ollama with exponential backoff on network/transport errors.

        Returns ``(content, error_string)``. On success ``error_string`` is
        ``None``. On exhausted retries it returns ``("", error)``.
        """
        llm = ChatOllama(
            model=self.model,
            base_url=OLLAMA_HOST,
            temperature=self.temperature,
            format="json",
        )

        delay = 1.0
        last_err: str | None = None
        for attempt in range(self.max_network_retries + 1):
            try:
                response = llm.invoke(messages)
                content = (
                    response.content
                    if isinstance(response.content, str)
                    else str(response.content)
                )
                return content, None
            except Exception as exc:  # noqa: BLE001 — boundary to LangChain/Ollama
                last_err = str(exc)
                log.warning(
                    f"{self.name}_agent_llm_attempt_failed",
                    attempt=attempt + 1,
                    error=last_err,
                )
                if attempt < self.max_network_retries:
                    time.sleep(delay)
                    delay *= 2
        return "", last_err or "unknown_llm_error"

    # ------------------------------------------------------------------
    # JSON parsing with repair retry
    # ------------------------------------------------------------------

    def _parse_with_repair(
        self,
        first_content: str,
        original_messages: list[Any],
        metadata: dict[str, Any],
    ) -> tuple[list[ReviewItem], str | None]:
        """Parse the LLM response, retrying with a repair prompt on failure."""
        content = first_content
        last_error: str | None = None

        for attempt in range(self.max_parse_retries + 1):
            items, err = self._parse_response(content)
            if err is None:
                if attempt > 0:
                    metadata[f"{self.name}_parse_retries"] = attempt
                return items, None
            last_error = err
            log.warning(
                f"{self.name}_agent_parse_failed",
                attempt=attempt + 1,
                error=err,
                raw_preview=content[:200],
            )
            if attempt >= self.max_parse_retries:
                break
            repair_msg = HumanMessage(content=self._repair_prompt(content, err))
            content, net_err = self._call_llm_with_retry(
                [*original_messages, repair_msg]
            )
            if net_err:
                metadata[f"{self.name}_error"] = f"repair_llm_failed: {net_err}"
                return [], last_error

        return [], last_error

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        s = text.strip()
        s = _LEADING_FENCE_RE.sub("", s)
        s = _TRAILING_FENCE_RE.sub("", s)
        return s.strip()

    def _parse_response(self, content: str) -> tuple[list[ReviewItem], str | None]:
        """Parse LLM response into a list of ``ReviewItem``.

        Tolerates the common LLM shapes:
          - ``[]``                        — empty list
          - ``[{...}]``                   — the happy path
          - ``{}``                        — LLM's stand-in for empty list
          - ``{"findings": [...]}``       — wrapped list
          - ``{"file": ..., ...}``        — single finding object
        """
        cleaned = self._strip_code_fences(content)
        if not cleaned:
            return [], "empty_response"

        try:
            raw = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            return [], f"json_decode_error: {exc.msg}"

        if isinstance(raw, dict):
            if not raw:
                raw = []
            elif "findings" in raw and isinstance(raw["findings"], list):
                raw = raw["findings"]
            elif "file" in raw or "message" in raw:
                raw = [raw]
            else:
                return [], f"expected_list_got_{type(raw).__name__}"

        if not isinstance(raw, list):
            return [], f"expected_list_got_{type(raw).__name__}"

        items: list[ReviewItem] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            entry.setdefault("agent", self.name)
            try:
                items.append(ReviewItem.model_validate(entry))
            except ValidationError as exc:
                log.warning(
                    f"{self.name}_agent_invalid_item", entry=entry, error=str(exc)
                )
                continue
        return items, None

    def _repair_prompt(self, previous_output: str, error: str) -> str:
        return (
            "Your previous response could not be parsed as a JSON array of "
            f"findings. The error was: {error}\n\n"
            "Your previous output was:\n"
            f"{previous_output[:1000]}\n\n"
            "Return ONLY a valid JSON array, no prose, no markdown fences. "
            "Even for a single finding, wrap it in []. For no findings, "
            "return []. Schema per finding: "
            '{"file": str, "line_start": int, "line_end": int|null, '
            '"severity": "critical"|"warning"|"info", "category": str, '
            '"message": str, "suggestion": str|null, "confidence": float}.'
        )

    # ------------------------------------------------------------------
    # Post-processing
    # ------------------------------------------------------------------

    @staticmethod
    def _max_line_numbers(diff_files: list[FileDiff]) -> dict[str, int]:
        """Largest valid new-file line number per filename, for hallucination check."""
        result: dict[str, int] = {}
        for f in diff_files:
            max_line = 0
            for hunk in f.hunks:
                for ch in hunk.changes:
                    if ch.line_number is not None and ch.line_number > max_line:
                        max_line = ch.line_number
            result[f.filename] = max_line
        return result

    def _sanitize_line_numbers(
        self,
        items: list[ReviewItem],
        max_by_file: dict[str, int],
        metadata: dict[str, Any],
    ) -> list[ReviewItem]:
        """Drop findings whose line_start exceeds the file's max line in the diff."""
        kept: list[ReviewItem] = []
        dropped = 0
        for item in items:
            max_line = max_by_file.get(item.file, 0)
            if max_line and item.line_start > max_line + 50:
                dropped += 1
                log.warning(
                    f"{self.name}_agent_drop_hallucinated_line",
                    file=item.file,
                    line_start=item.line_start,
                    max_line=max_line,
                )
                continue
            kept.append(item)
        if dropped:
            metadata[f"{self.name}_dropped_hallucinated"] = dropped
        return kept

    def _filter_by_confidence(self, items: list[ReviewItem]) -> list[ReviewItem]:
        if self.confidence_threshold <= 0:
            return items
        return [i for i in items if i.confidence >= self.confidence_threshold]
