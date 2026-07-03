---
date: 2026-07-03
round: 11
gaps_to_fix: 6
source_report: D:\code\myagentcli\docs\gap-reports\2026-07-03-gap-round-11.md
---

# Fix Plan — Round 11

## Summary
Fix 6 gaps across 5 files. One gap (gap-r11-05) is partially improved within the constraints of the inline fallback path; the sub-agent path already uses full LLM reasoning.

## Task 1: Use LiteLLM built-in retry instead of custom loop
- **Gap IDs**: gap-r11-01
- **Files**: `myagent/llm/provider.py`
- **Approach**: 
  1. In `LLMProvider.__init__`, configure `litellm.num_retries = MAX_RETRIES` to delegate retries to litellm's internal mechanism.
  2. Remove the manual `for attempt in range(MAX_RETRIES + 1)` retry loop from `_complete_with_model`. The method will now make a single `litellm.acompletion` call (per model), with litellm handling retries internally.
  3. Keep error classification (mapping litellm exceptions to `LLMError`) but simplify since retries are handled by litellm.
  4. Use litellm's `failure_hook` or `logging` callback to notify the retry_callback for UI updates.
  5. Exponential backoff parameters are configured via `litellm.num_retries` and `litellm.request_timeout`.
- **Verification**: Run `pytest tests/ -v` and verify no regressions. Check that litellm.num_retries is set and that the custom retry loop is removed.

## Task 2: Remove arbitrary 4000-character skill content truncation
- **Gap IDs**: gap-r11-02
- **Files**: `myagent/context/builder.py`
- **Approach**: Remove the `content[:4000]` hard cap in `_format_skill_content`. Load full SKILL.md content (no truncation). The spec explicitly says "加载完整 SKILL.md 注入 system prompt". Context window management is handled by the compression system at higher layers.
- **Verification**: Code review of `_format_skill_content` to confirm no truncation remains.

## Task 3: Add missing dependency/metadata fields to task tools
- **Gap IDs**: gap-r11-03
- **Files**: `myagent/tools/builtin/session_tools.py`
- **Approach**:
  1. Add `blocks`, `blockedBy`, `owner`, and `metadata` as optional parameters to `TaskCreateTool.parameters`.
  2. Add the same fields to `TaskUpdateTool.parameters`.
  3. Update `TaskCreateTool.execute` to extract and pass these fields to `TaskList.create`.
  4. Update `TaskUpdateTool.execute` to handle these fields (map `blockedBy` -> `blocked_by`).
  5. Update `TaskList.create` to accept the new optional parameters.
- **Verification**: Run `pytest tests/ -v -k task` to verify task tools work correctly.

## Task 4: Parse compound permission patterns in apply_runtime_rule
- **Gap IDs**: gap-r11-04
- **Files**: `myagent/permissions/controller.py`
- **Approach**:
  1. Add parsing for "除了 X 之外都放行" pattern (Chinese "allow all except X").
  2. Add parsing for English equivalent "allow all except X" / "allow everything except X".
  3. When matched, call `set_mode("allow_all")` AND `auto_deny.commands.append(X)`.
  4. Handle single and multiple exceptions: "除了 X 和 Y 之外都放行".
- **Verification**: Run `pytest tests/ -v -k permission` and verify compound patterns are parsed correctly.

## Task 5: Improve dream inline fallback contradiction detection
- **Gap IDs**: gap-r11-05
- **Files**: `myagent/memory/dream.py`
- **Approach**:
  1. Replace simple keyword-pair matching with sentence-level analysis.
  2. Extract sentence-level assertions from each memory (split on `.`, `。`, `\n`).
  3. For each pair of memories, check for negation patterns ("not", "do not", "never", "不", "禁止", etc.) in one memory against positive assertions in the other.
  4. Use fuzzy name matching (token overlap) to identify memories that are likely related.
  5. Add more comprehensive contradiction patterns covering categorical opposites.
  6. Add similarity-based grouping: memories with overlapping significant words are more likely to be contradictory candidates.
  7. The sub-agent path already uses full LLM reasoning and is the preferred path; this improves the fallback.
- **Verification**: Run `pytest tests/ -v -k dream` to verify dream engine still works.

## Task 6: Fold --config into CLI args layer (7 levels, not 8)
- **Gap IDs**: gap-r11-06
- **Files**: `myagent/config/loader.py`
- **Approach**:
  1. Remove the separate `custom_config` merge layer from `ConfigLoader.load`.
  2. Instead, when `--config` is specified, load that file's content and apply it as part of the CLI args layer (highest priority after runtime overrides).
  3. The `_config_path` is still accepted in `__init__` but processed as a CLI-arg-level override, not a separate layer.
  4. Update the docstring and code comments to say "7 levels" consistently.
- **Verification**: Run `pytest tests/ -v -k config` to verify config loading works correctly with 7 levels.
