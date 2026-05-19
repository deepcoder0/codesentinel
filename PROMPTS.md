# CodeSentinel — Prompt Engineering Log

Track all agent prompt changes here. Every version gets a date, description, and observed impact.

---

## Quality Agent

### v1.0 (Sprint 1, CS-004)
- **Date:** 2026-05-19
- **Source:** `src/codesentinel/agents/quality.py` — constant `QUALITY_SYSTEM_PROMPT`
- **Structure:** ROLE → OUT OF SCOPE → CHECKLIST (6 items) → SEVERITY CALIBRATION (3 few-shot examples, one per severity) → OUTPUT FORMAT
- **Model:** `qwen2.5-coder:7b` via Ollama, temperature 0.1, `format="json"`
- **Confidence threshold:** 0.6
- **Live test results on the three CS-004 fixtures:**
  - `quality_naming.diff` → 1 finding, **info / naming**, correct line, ~20s
  - `quality_dead_code.diff` → 1 finding, **info / dead_code** (unreachable after return), correct line, ~4s
  - `quality_complexity.diff` → 1 finding, **warning / complexity** (cyclomatic 12), correct line, ~8s
- **Observations:**
  - The few-shot examples are doing their job for severity calibration — `info` for naming, `warning` for complexity, none mislabelled as `critical`.
  - Line-number prefixes (`L42:`) prevent hallucinated line numbers on real diffs.
  - **Model returns one finding per diff, not exhaustive.** A diff with 4 separate quality issues yields the most prominent one only. Adding "report EVERY issue you find" to the checklist instruction (v2 attempt) did not increase finding count but did miscalibrate severity upward (complexity went from warning → critical). Reverted.
  - **Few-shot Example 1 over-anchors.** When run on a diff with no quality issues but a strong security issue (`python_simple.diff`), the model hallucinated a bare-except finding word-for-word from Example 1's suggestion text. Should not appear in production once the Security Agent (CS-006) owns that diff, but flagged for CS-024 to address.
- **Time budget:** ~3 hours total for CS-004 including BaseAgent + tests + fixtures + 2 prompt iterations. Realistic estimate matches the plan's 2 SP, not the breakdown's optimistic 1 SP.

### v2.0 (planned, CS-024)
- Address: model picks single finding per diff; Example 1 over-anchoring; potentially try per-checklist-item iterative prompting for exhaustive coverage.

---

## Security Agent

### v1.0 (Sprint 2)
- **Date:** TBD
- **Change:** Initial prompt with OWASP-focused checklist + RAG injection slot
- **Observations:** TBD

---

## Performance Agent

### v1.0 (Sprint 2)
- **Date:** TBD

---

## Architecture Agent

### v1.0 (Sprint 2)
- **Date:** TBD
