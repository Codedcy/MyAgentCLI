---
date: 2026-07-03
round: 17
gaps_to_fix: 5
source_report: D:\code\myagentcli\docs\gap-reports\2026-07-03-gap-round-17.md
---

# Fix Plan — Round 17

## Summary
Fix 5 gaps across 4 files. All gaps are implementation-complete fixes with no stubs or TODOs.

## Task 1: web_fetch — LLM-based prompt answering instead of regex extraction
- **Gap IDs**: gap-17-01
- **Files**: `myagent/tools/builtin/web_tools.py`
- **Approach**: After fetching and converting HTML to markdown/text, the tool will attempt to call a lightweight LLM (Non-think mode, non-streaming) with the fetched content + user's prompt. If the LLM call succeeds, return the model's answer. If the LLM call fails (no provider, network error), fall back to the existing `_extract_relevant()` keyword-based extraction as a graceful degradation path.
- **Verification**: Code review of the modified `execute()` method. The logic flow is: fetch HTML -> convert to markdown -> attempt LLM answer -> fall back to regex extraction on failure.

## Task 2: config_set — add `type` field to `value` parameter JSON Schema
- **Gap IDs**: gap-17-02
- **Files**: `myagent/tools/builtin/config_tools.py`
- **Approach**: Add `"type": ["string", "number", "boolean", "array", "object"]` to the `value` property in the `parameters` JSON Schema dict. This makes the schema complete per the unified Tool protocol in the spec.
- **Verification**: Visual inspection of the schema. The `value` parameter now has an explicit `type` field alongside `description`.

## Task 3: TOOL_LEVEL_MAP — add missing tool entries
- **Gap IDs**: gap-17-03
- **Files**: `myagent/permissions/controller.py`
- **Approach**: Add three missing entries to `TOOL_LEVEL_MAP`:
  - `"mcp_read_resource": 0` — read-only MCP resource access
  - `"mcp_get_prompt": 0` — read-only MCP prompt template invocation
  - `"config_set": 1` — runtime config write (analogous to `memory_write`)
- **Verification**: `grep` for the tool names in `TOOL_LEVEL_MAP` to confirm they are present.

## Task 4: Dream engine — auto-correct factual errors in memories
- **Gap IDs**: gap-17-04
- **Files**: `myagent/memory/dream.py`
- **Approach**: Two-part fix:
  1. **Inline path** (`_run_inline`): After `_check_factual_errors()` detects discrepancies, automatically update the affected memory file by constructing a corrected version of the content (replacing outdated tool/version references with the detected ones). Track these as `memories_updated` in the result.
  2. **Sub-agent path** (`_run_as_subagent`): Before building the sub-agent prompt, run `_check_factual_errors()` and inject the detected discrepancies into the prompt's analysis section so the LLM-driven sub-agent can apply corrections via `memory_write` calls.
- **Verification**: Code review the dream engine run paths. The inline path now calls an `_auto_correct_factual_errors()` method that updates memory files. The sub-agent path now passes factual error information into the prompt.

## Task 5: L0 system prompt — dynamic model name instead of hardcoded string
- **Gap IDs**: gap-17-05
- **Files**: `myagent/context/builder.py`
- **Approach**: Replace the static `L0_SYSTEM_PROMPT` class constant with a method `_build_l0_system_prompt()` that dynamically templates the system prompt. If `self.config` is available, use `self.config.model.provider` and `self.config.model.model` to construct the model description. If `self.config` is None, use a generic phrase "a large language model". The method is called from `build()` to produce the actual L0 text.
- **Verification**: Check that `system_parts[0]` in `build()` calls `self._build_l0_system_prompt()` and the returned text reflects the configured model.
