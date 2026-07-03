---
date: 2026-07-03
round: 7
gaps_to_fix: 9
source_report: D:\code\myagentcli\docs\gap-reports\2026-07-03-gap-round-7.md
---

# Fix Plan — Round 7

## Summary
Fix 9 gaps across 7 files. One gap (gap-7-1) is already resolved — all 6 built-in SKILL.md files exist.

## Task 1: Wire natural language permission adjustments (gap-7-2)
- **Gap IDs**: gap-7-2
- **Files**: `myagent/tools/builtin/config_tools.py`
- **Approach**: Extend `ConfigSetTool` to handle `permissions.auto_allow.commands`, `permissions.auto_deny.commands`, `permissions.auto_allow.paths`, `permissions.auto_deny.paths`, and `permissions.auto_allow.levels` config keys. When one of these is set, update both the ConfigLoader and the live PermissionController (accessed via `context.permissions`). For `permissions.default_mode`, also call `context.permissions.set_mode()`. This enables the Agent to call `config_set` after interpreting the user's natural language permission request.
- **Verification**: The Agent can now interpret "git 命令不再问我了" → call `config_set(key="permissions.auto_allow.commands", value=["git *"])` → updates both config and live controller.

## Task 2: Fix session listing to use compact line-based format (gap-7-3)
- **Gap IDs**: gap-7-3
- **Files**: `myagent/cli/main.py`
- **Approach**: Rewrite `_print_sessions_rich` to output the spec format: one line per session with "✅/📋/— status_icon session_id "first_message_snippet" duration tk_count goal_status". Keep Rich for styling colors/icons but use a simple console.print() line-per-session layout instead of a Table.
- **Verification**: Output matches the spec format shown in the design doc.

## Task 3: Fix /exit --force and /quit --force not working (gap-7-4)
- **Gap IDs**: gap-7-4
- **Files**: `myagent/cli/repl.py`
- **Approach**: In `REPLEngine.process_input()`, after dispatching a slash command, check `result.exit_requested`. If True, set `self._running = False` and return immediately. Also remove the direct `/exit` and `/quit` string check at the top of `process_input` since it will now be handled by the dispatcher + exit_requested flag flow.
- **Verification**: `/exit --force` and `/quit --force` exit the REPL. `/exit` without `--force` shows the confirmation message but does not exit.

## Task 4: Surface wiki-link cross-references in memory recall (gap-7-5)
- **Gap IDs**: gap-7-5
- **Files**: `myagent/memory/store.py`, `myagent/memory/recall.py`
- **Approach**: 
  1. In `MemoryStore.read()`, also extract wiki links from the content body and include them in the returned `MemoryFile.metadata["links"]`.
  2. In `memory/recall.py`, add a `follow_links` step: after initial recall, for each recalled memory that has `metadata["links"]`, look up those linked memories and include them in the result set (with lower priority). This provides the cross-reference resolution specified in the design doc.
- **Verification**: When a memory contains `[[other-name]]`, recalling for a related query returns both the directly matching memory AND the linked memory.

## Task 5: Add file reference to tool result summaries (gap-7-6)
- **Gap IDs**: gap-7-6
- **Files**: `myagent/agent/engine.py`
- **Approach**: In `_summarize_via_subagent`, after the tool call is persisted to the session store (already done in `_execute_tool`), capture the file path and include it in the summary output: `"[Summarized from {len} chars. Full result: tools/call-{call_id}.json]\n{summary}"`. Extract the call_id from the tool call and construct the relative path.
- **Verification**: Summarized tool results include a reference to the persisted full result file.

## Task 6: Fix prompt log filename to ISO 8601 T-separated format (gap-7-7)
- **Gap IDs**: gap-7-7
- **Files**: `myagent/llm/provider.py`
- **Approach**: Change the date format string in `_write_prompt_logs` and `_write_response_log` from `"%Y%m%d-%H%M%S"` to `"%Y-%m-%dT%H-%M-%S"`. This produces filenames like `2026-07-03T14-32-01-nosession-request-0001.json`.
- **Verification**: Prompt log filenames match the design spec format.

## Task 7: Fix grep head_limit to apply globally in ripgrep path (gap-7-8)
- **Gap IDs**: gap-7-8
- **Files**: `myagent/tools/builtin/search_tools.py`
- **Approach**: Remove the `-m` flag from the ripgrep command (line 172). Instead, after collecting rg output, apply offset + head_limit as a global post-processing step (similar to how offset is already handled in the rg path). Split output into lines, apply offset, then truncate to head_limit.
- **Verification**: Both ripgrep and Python fallback paths produce identical result counts for the same head_limit.

## Task 8: Fix Python version detection to try python3 first (gap-7-9)
- **Gap IDs**: gap-7-9
- **Files**: `myagent/agent/project.py`
- **Approach**: Modify `_detect_python_version()` to try `python3 --version` first, then `python --version` as fallback, then `sys.version` as final fallback. Use `sys.version` from the running interpreter since we know it's Python 3.12+.
- **Verification**: On systems with both Python 2 and Python 3, the correct Python 3 version is detected.

## Task 9: gap-7-1 is already fixed
- **Gap IDs**: gap-7-1
- **Status**: Already resolved. All 6 built-in SKILL.md files exist at:
  - `myagent/skills/builtin/brainstorming/SKILL.md`
  - `myagent/skills/builtin/code-review/SKILL.md`
  - `myagent/skills/builtin/systematic-debugging/SKILL.md`
  - `myagent/skills/builtin/tdd/SKILL.md`
  - `myagent/skills/builtin/writing-plans/SKILL.md`
  - `myagent/skills/builtin/executing-plans/SKILL.md`
  SkillRegistry scans and registers them on startup.
- **Verification**: `ls myagent/skills/builtin/*/SKILL.md` shows all 6 files.
