---
date: 2026-07-03
round: 5
gaps_to_fix: 10
source_report: D:\code\myagentcli\docs\gap-reports\2026-07-03-gap-round-5.md
---

# Fix Plan — Round 5

## Summary
Fix 10 gaps across 9 files.

---

## Task 1: Fix spawn_subagent goal-mode detection (G1)
- **Gap IDs**: G1
- **Files**: `myagent/tools/base.py`, `myagent/tools/builtin/agent_tools.py`, `myagent/agent/engine.py`
- **Approach**:
  1. Add `goal_tracker` field (optional) to `ToolContext` dataclass.
  2. In `AgentEngine`, pass `self.goal_tracker` into the ToolContext when constructing it.
  3. In `SpawnSubagentTool.execute()`, replace the dead `getattr(context.config, '_goal', None)` checks with `context.goal_tracker.get_goal()`.
- **Verification**: In goal mode with no `speculative_exploration`, `background` should default to `True`. In non-goal mode without explicit `speculative_exploration` config, `background` should default to `False` when not explicitly set in params.

## Task 2: Wire --config CLI argument to ConfigLoader (G2)
- **Gap IDs**: G2
- **Files**: `myagent/config/loader.py`, `myagent/cli/main.py`
- **Approach**:
  1. Add `config_path: str | None = None` parameter to `ConfigLoader.__init__()`.
  2. In `load()`, if `config_path` is provided, load that YAML file and merge it as a high-priority layer (just above project config, below runtime overrides — since the design spec puts CLI-start-args at level 1, a custom config file from CLI is a start-arg but it's a file not an individual override; best placed between project config and runtime overrides).
  3. In `async_main()`, read `args.config` and pass it to `ConfigLoader(config_path=args.config)`.
- **Verification**: `myagent --config /tmp/custom.yaml` loads and applies the custom config values.

## Task 3: Populate SubAgentWorker transcript fields (G3)
- **Gap IDs**: G3
- **Files**: `myagent/subagent/worker.py`
- **Approach**:
  1. In `_run_impl()`, after building each assistant message (with tool calls), append it to `self._transcript_messages`.
  2. After each tool execution, append a record to `self._transcript_tool_calls` with tool name, params, result, and timing.
- **Verification**: After a sub-agent runs, `_transcript_messages` and `_transcript_tool_calls` are populated, and `_persist_subagent_transcript()` produces complete transcript files.

## Task 4: Wire SessionConfig.sessions_dir to SessionStore (G4)
- **Gap IDs**: G4
- **Files**: `myagent/context/persistence.py`, `myagent/cli/main.py`
- **Approach**:
  1. `SessionStore.__init__()` already accepts `base_dir` — no changes needed there.
  2. In `async_main()`, after loading config, resolve `config.session.sessions_dir` (expand `~` and env vars) and pass it to `SessionStore(base_dir=resolved_path)`.
- **Verification**: Changing `session.sessions_dir` in config.yaml changes where session files are stored.

## Task 5: Enforce read-before-edit in EditTool (G5)
- **Gap IDs**: G5
- **Files**: `myagent/tools/builtin/file_tools.py`
- **Approach**:
  1. Add `_read_files: set[str]` class-level tracking to `EditTool` (same pattern as `WriteTool`).
  2. Before editing, check if path is in `_read_files`. If not, return an error: "Must read this file before editing it."
  3. The Read tool should add to both `WriteTool._read_files` and `EditTool._read_files`, or better yet, use a shared module-level set.
- **Verification**: Attempting to edit a never-read file returns an error. Reading then editing succeeds.

## Task 6: Enforce read-before-write guard in WriteTool (G6)
- **Gap IDs**: G6
- **Files**: `myagent/tools/builtin/file_tools.py`
- **Approach**:
  1. In `WriteTool.execute()`, when `path.exists()` and `str(path) not in self._read_files`, return `ToolResult(error="Must read file before overwriting: ...")` instead of silently adding to the set and proceeding.
- **Verification**: Overwriting an existing unread file returns an error. After reading, overwrite succeeds.

## Task 7: Fix dream correction-to-memory detection (G7)
- **Gap IDs**: G7
- **Files**: `myagent/memory/dream.py`
- **Approach**:
  1. In `_scan_transcripts()`, return structured findings including `correction_count` as a numeric value (not just embedded in text).
  2. In `_create_memories_from_patterns()`, check `correction_count >= 2` directly instead of substring matching `"corrections" AND "detected"`.
  3. Create a memory that describes the specific correction patterns, not a generic placeholder. Include actual correction markers found.
- **Verification**: When `correction_count >= 2`, the memory is created regardless of the exact phrasing of the finding text.

## Task 8: Remove config_set from built-in tools (G8)
- **Gap IDs**: G8
- **Files**: `myagent/cli/main.py`
- **Approach**:
  1. Remove `ConfigSetTool` from the import and registration in `_register_builtin_tools()`.
  2. Keep the `config_tools.py` file as-is (it's a working implementation) but do not register it.
- **Verification**: `len(registry.tools) == 13` after startup. The `config_set` tool is no longer available to the LLM.

## Task 9: Remove send_message from TOOL_LEVEL_MAP (G9)
- **Gap IDs**: G9
- **Files**: `myagent/permissions/controller.py`
- **Approach**:
  1. Remove `"send_message": 2` from `TOOL_LEVEL_MAP`. Unknown tools default to level 3 (most restrictive per `_get_level()` fallback).
  2. Add a comment noting the deviation from the design spec's level 2 tool list.
- **Verification**: `TOOL_LEVEL_MAP` no longer contains `send_message`. `_get_level("send_message")` returns 3.

## Task 10: Integrate MCP resources and prompts into context (G10)
- **Gap IDs**: G10
- **Files**: `myagent/cli/main.py`, `myagent/context/builder.py`
- **Approach**:
  1. In `_startup_mcp_servers()`, collect MCP resources and prompts into lists on the `ToolRegistry` (add `mcp_resources` and `mcp_prompts` attributes).
  2. In `ContextBuilder`, when building the system prompt, include MCP resources and prompts as reference sections.
  3. For prompts: register them as reference info so the LLM knows they exist and can request them.
- **Verification**: After MCP startup, `tool_registry.mcp_resources` is populated. The system prompt includes MCP resource/prompt information.

