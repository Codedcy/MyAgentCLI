---
date: 2026-07-03
round: 15
gaps_to_fix: 4
source_report: D:\code\myagentcli\docs\gap-reports\2026-07-03-gap-round-15.md
---

# Fix Plan -- Round 15

## Summary
Fix 4 gaps across 3 files.

## Task 1: Expose detected environment fields in L3 project context
- **Gap IDs**: gap-15-01
- **Files**: `myagent/context/builder.py` (modify `_format_project_context`)
- **Approach**: Extend `_format_project_context()` to output `package_manager`, `python_version`, `build_system`, `test_framework`, and `linter` fields from `ProjectContext` when they are non-None. These are critical for the model to give context-appropriate advice (e.g., suggesting `uv add` instead of `pip install`). The detected fields already exist on the `ProjectContext` object -- we just need to format them into the L3 system prompt.
- **Verification**: Read the modified method to confirm all fields are included; run `pytest tests/ -v` to ensure no regressions.

## Task 2: Configure explicit LLM retry backoff parameters
- **Gap IDs**: gap-15-02
- **Files**: `myagent/llm/provider.py` (modify `__init__` and `_complete_with_model`)
- **Approach**: Disable LiteLLM's built-in retry (`litellm.num_retries = 0`) and implement an explicit exponential backoff retry loop in `_complete_with_model` using the same pattern as `subagent/worker.py`: BASE_DELAY = 2.0, MAX_DELAY = 30.0, MAX_RETRIES = 3. Each retry waits `min(BASE_DELAY * (2 ** attempt), MAX_DELAY)` seconds before retrying. This matches the spec exactly: "指数退避重试，最多 3 次，初始间隔 2s，上限 30s".
- **Verification**: Inspect the retry logic to verify `delay = min(2.0 * (2 ** attempt), 30.0)` for attempt 0, 1, 2; run `pytest tests/ -v`.

## Task 3: Align log rotation implementation with spec -- compose stdlib handlers
- **Gap IDs**: gap-15-03
- **Files**: `myagent/logging/logger.py` (rewrite `TimedSizeRotatingFileHandler`)
- **Approach**: Replace the custom `TimedSizeRotatingFileHandler` (which subclasses `RotatingFileHandler`) with a new design that subclasses `TimedRotatingFileHandler` and composes size-based rotation inside it. The primary axis is time (daily rotation via `TimedRotatingFileHandler`), with secondary size-based rotation within each day. On each `emit()`, after the parent's time-based check, verify the file size does not exceed `maxBytes`; if it does, trigger size-based rotation using the same backup numbering scheme as `RotatingFileHandler`. This matches the spec: "TimedRotatingFileHandler 按天轮转 + RotatingFileHandler 按大小轮转组合".
- **Verification**: Inspect the new class to confirm it subclasses `TimedRotatingFileHandler` and includes size-based rotation checks; run `pytest tests/ -v`.

## Task 4: Add project-convention cross-reference to inline dream path
- **Gap IDs**: gap-15-04
- **Files**: `myagent/memory/dream.py` (modify `_run_inline` and add new method)
- **Approach**: Add a `_check_factual_errors` method to the inline dream path that cross-references individual memory content against project conventions detected at startup. The method receives detected environment facts (python_version, package_manager, linter, etc.) and flags any memory whose content contradicts these facts. For example, a memory saying "this project uses Python 3.8" when the detected version is 3.12 would be flagged for manual review. The DreamEngine will accept an optional `project_context` parameter to receive these detected facts. The inline path's analysis sections will include any flagged factual discrepancies.
- **Verification**: Inspect the new method to confirm it checks version strings, package manager names, and linter names against memory content; run `pytest tests/ -v`.
