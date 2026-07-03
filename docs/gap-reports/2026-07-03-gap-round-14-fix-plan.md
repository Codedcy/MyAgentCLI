---
date: 2026-07-03
round: 14
gaps_to_fix: 5
source_report: D:\code\myagentcli\docs\gap-reports\2026-07-03-gap-round-14.md
---

# Fix Plan — Round 14

## Summary
Fix 5 gaps across 5 files. All fixes are complete, working implementations.

## Task 1: Fix skill resource paths in _format_skill_content
- **Gap IDs**: gap-r14-01, gap-r14-02
- **Files**: `myagent/skills/loader.py` (Skill dataclass), `myagent/context/builder.py` (_format_skill_content)
- **Approach**:
  1. Add `base_dir: Path | None` field to the `Skill` dataclass in `loader.py`.
  2. Set `base_dir=path.parent` in `SkillLoader.parse_skill_md`.
  3. In `_format_skill_content`, use `str(r)` (absolute path) instead of `str(r.name)` for references and scripts, and add listing for `templates/` and `assets/`.
  4. Also include the skill's `base_dir` in the formatted output so the agent knows where to find resources.
- **Verification**: `_format_skill_content` output now includes full paths and all four resource types.

## Task 2: Remove redundant permission check from BashTool.execute
- **Gap IDs**: gap-r14-03
- **Files**: `myagent/tools/builtin/exec_tools.py` (BashTool.execute), `myagent/agent/engine.py` (_execute_tool)
- **Approach**:
  1. Remove the entire permission check + confirm block from `BashTool.execute` (lines 54-84). The engine's `_execute_tool` already performs centralized permission checks.
  2. Add a `dangerouslyDisableSandbox` check in the engine: if the tool's params contain `dangerouslyDisableSandbox=True`, skip the permission check for that tool call.
  3. Remove the `dangerouslyDisableSandbox` parameter from BashTool's parameter schema since it's now handled at the engine level.
- **Verification**: Permission prompts appear exactly once for bash commands.

## Task 3: Fix SubAgentPool counter reset on session resume
- **Gap IDs**: gap-r14-04
- **Files**: `myagent/subagent/pool.py` (SubAgentPool, spawn), `myagent/cli/main.py` (async_main resume path)
- **Approach**:
  1. Add a `set_session(session, session_store)` method to SubAgentPool that updates the pool's session reference and scans existing `subagents/` directories to find the maximum existing sub-agent ID, then sets `_counter` above it.
  2. Call `subagent_pool.set_session(session, session_store)` in the resume path of `async_main` after successful session load.
  3. This also fixes the secondary bug where `_persist_subagent_transcript` uses `self._session` (always None on resume because it was never set).
- **Verification**: On session resume, new sub-agents get unique IDs that don't collide with previous session's sub-agents.

## Task 4: Fix compression minimum_messages guard to exclude system messages
- **Gap IDs**: gap-r14-05
- **Files**: `myagent/context/compression.py` (compact method)
- **Approach**:
  1. In `compression.compact()`, when checking `len(messages) < self.config.minimum_messages`, exclude system-role messages from the count. System messages (the system prompt) are metadata, not conversation rounds.
  2. Also apply the same logic in `_layer3_summarize` where it checks `len(messages) < 10`.
- **Verification**: Compression triggers only when there are at least `minimum_messages` actual conversation messages.

## Task 5: Update Skill.__init__ exports
- **Gap IDs**: gap-r14-02 (follow-up)
- **Files**: `myagent/skills/__init__.py`
- **Approach**: No change needed — `Skill` is already exported. The base_dir field is a new kwarg with default None, so existing callers are unaffected.
- **Verification**: Import still works without errors.
