---
date: 2026-07-03
round: 12
gaps_to_fix: 8
source_report: docs/gap-reports/2026-07-03-gap-round-12.md
---

# Fix Plan — Round 12

## Summary
Fix 8 gaps across 5 files. All are minor deviations or incomplete implementations with clear, bounded fixes.

## Task 1: Read context_window from model config instead of hardcoding 1M
- **Gap IDs**: gap-r12-01, gap-r12-05
- **Files**: `myagent/agent/engine.py`
- **Approach**: 
  - Replace hardcoded `context_window = 1_000_000` with a lookup that reads from the active model via LLMProvider or falls back to 1M.
  - Replace `total_chars / 3` with `self.llm.token_count(messages)` if available, else fall back to character-based estimate with language-aware ratio (default 3.5 for mixed, configurable).
  - Add a `_CONTEXT_WINDOW_MAP` dict for known model context windows (deepseek-v4-pro→1M, gpt-4o→128K, etc.) with a default fallback.
  - The actual context window is derived from `self.config.model.model` if available and looked up in the map; otherwise falls back to 1_000_000.
- **Verification**: `pytest tests/ -v` — existing context tests should pass; verify that changing `config.model.model` changes the estimated usage ratio.

## Task 2: Apply minimum_savings debounce to overall compression
- **Gap IDs**: gap-r12-02
- **Files**: `myagent/context/compression.py`
- **Approach**:
  - Compute total pre-compression size before any layer runs.
  - Compute total post-compression size after all layers.
  - If `(pre_size - post_size) / pre_size < minimum_savings`, roll back all changes and return original messages unchanged.
  - Remove the Layer-2-only savings check, since the global check subsumes it.
- **Verification**: `pytest tests/ -v` — compression tests should pass; verify with a test case where individual layers produce small savings that sum to < 10% and confirm the result is discarded.

## Task 3: Remove 20K truncation in summarizer; use full result with file reference
- **Gap IDs**: gap-r12-03
- **Files**: `myagent/agent/engine.py`
- **Approach**:
  - Remove the `[:20000]` truncation from `_summarize_via_subagent`.
  - For results <= 20000 chars: pass the full result inline as before.
  - For results > 20000 chars: point the summarizer to the persisted file on disk (tools/call-{call_id}.json) and instruct it to read the file via its `read` tool.
  - Use a file reference in the prompt so the sub-agent can read the complete result.
- **Verification**: `pytest tests/ -v` — existing summarizer tests should pass.

## Task 4: Remove dead 'goal' entry from _CLI_MAPPING
- **Gap IDs**: gap-r12-04
- **Files**: `myagent/config/loader.py`
- **Approach**:
  - Remove the `"goal": ("session._goal", None),` line from `_CLI_MAPPING`.
  - The goal is already handled correctly by `main.py` via `goal_tracker.set_goal(args.goal)`.
- **Verification**: `pytest tests/ -v` — config tests should pass.

## Task 5: Update dream state file at session start, not just dream completion
- **Gap IDs**: gap-r12-06
- **Files**: `myagent/memory/dream.py`
- **Approach**:
  - Add a `touch_session_start()` method that updates `last_dream.json` with a `session_started_at` timestamp when a new session begins.
  - In `should_run()`, use `min(last_run, session_started_at)` when computing elapsed time, so the hours counter resets on each fresh session.
  - Call `touch_session_start()` from the main entry point (CLI session start).
- **Verification**: `pytest tests/ -v` — dream tests should pass.

## Task 6: Emit user notification when Layer 3 is degraded after 3 failures
- **Gap IDs**: gap-r12-07
- **Files**: `myagent/context/compression.py`, `myagent/agent/engine.py`
- **Approach**:
  - In `CompressionEngine.compact()`, when `_layer3_failures` reaches 3, set a `_layer3_degraded` flag and return a notification string through the `CompactResult`.
  - Add a `degradation_notice: str | None = None` field to `CompactResult`.
  - In `engine.py`'s compaction handling, yield a `TextChunk` with the degradation notice to inform the user.
- **Verification**: `pytest tests/ -v` — compression tests should pass.

## Task 7: Write provisional duration on every transcript save
- **Gap IDs**: gap-r12-08
- **Files**: `myagent/context/persistence.py`
- **Approach**:
  - In `_write_transcripts`, compute `duration = (datetime.now() - session.created_at).total_seconds()` instead of hardcoding `0`.
  - The `_write_closed_session` method already computes the final duration; this change ensures crash-recovery yields a reasonable estimate.
- **Verification**: `pytest tests/ -v` — persistence tests should pass.

