# Gap Round 21 Fix Plan

## Goal

Close all Blocking and Important gaps from round 21: G21-01 through G21-06.

## Scope

### G21-01 Permission Safety

Files:
- Modify `myagent/tools/builtin/exec_tools.py`
- Modify `myagent/agent/engine.py`
- Test `tests/tools/builtin/test_exec_tools.py`
- Test `tests/agent/test_engine.py`

Plan:
- Remove model-visible `dangerouslyDisableSandbox` from the bash tool schema.
- Remove tool-parameter-driven permission bypass from `AgentEngine._execute_tool()`.
- Keep permission bypass only through the centralized `PermissionController.skip_all()` path controlled by CLI/runtime state.
- Add regression coverage proving a tool call parameter named `dangerouslyDisableSandbox` does not skip permission checks.

### G21-02 MCP Project Config

Files:
- Modify `myagent/cli/main.py`
- Test `tests/cli/test_main.py`

Plan:
- Change `_startup_mcp_servers` to accept an explicit `project_dir`.
- Pass the parsed `--project-dir` value from `async_main`.
- Load project MCP config from `project_dir / ".myagent" / "mcp.json"`.
- Merge MCP server definitions so project-level entries override user-level entries with the same server name.
- Add CLI tests for `--project-dir` discovery and project-over-user override.

### G21-03 Dream Transcript Scanning

Files:
- Modify `myagent/memory/dream.py`
- Test `tests/memory/test_dream.py`

Plan:
- Add persistent processed-transcript tracking to `last_dream.json`.
- Discover unprocessed transcript files without a fixed seven-day or top-N boundary.
- Keep any top-N behavior only for prompt or excerpt truncation.
- Update state after a dream run with the processed transcript identifiers and timestamp.
- Add tests proving old unprocessed transcripts are scanned and already processed transcripts are skipped.

### G21-04 Sub-Agent Message Attribution

Files:
- Modify `myagent/tools/base.py`
- Modify `myagent/subagent/pool.py`
- Modify `myagent/subagent/worker.py`
- Modify `myagent/tools/builtin/agent_tools.py`
- Test `tests/tools/builtin/test_agent_tools.py`

Plan:
- Add current sub-agent identity to `ToolContext`.
- Ensure the sub-agent pool injects the actual handle ID into worker tool contexts.
- Use the context identity when `send_message(to="main")` omits `from`.
- Add coverage showing the main outbound queue receives the real sub-agent ID.

### G21-05 Sub-Agent Concurrency Default

Files:
- Modify `myagent/config/schema.py`
- Modify `myagent/cli/main.py`
- Test `tests/config/test_schema.py`
- Test `tests/subagent/test_pool.py`

Plan:
- Change `SubagentsConfig.max_concurrent` default to `None` to mean automatic sizing.
- Preserve explicit integer values as user-chosen limits.
- Let `SubAgentPool` compute automatic concurrency as `min(16, max(1, os.cpu_count() - 2))`.
- Add tests for the schema default, explicit integer behavior, and automatic pool sizing.

### G21-06 Exception Logging Compliance

Files:
- Modify `myagent/tools/builtin/exec_tools.py`
- Modify `myagent/tools/builtin/file_tools.py`
- Modify `myagent/tools/builtin/agent_tools.py`
- Modify `myagent/tools/builtin/mcp_tools.py`
- Modify `myagent/memory/dream.py`
- Test `tests/tools/builtin/test_exec_tools.py`

Plan:
- Add traceback logging with `category="error"`, `component`, and `context` for unexpected generic Exception fallback paths in the named built-in tools.
- Keep expected user-facing errors as `ToolResult(error=...)` without treating them as unexpected exceptions.
- Replace dream silent `continue` or system-category fallback exception logs in the named paths with error-category traceback logs where they catch unexpected exceptions.
- Add focused logging coverage proving a tool unexpected exception emits `category="error"` with component and context metadata.

## Verification Plan

- Run focused tests for the changed areas:
  - `pytest tests/agent/test_engine.py tests/tools/builtin/test_exec_tools.py tests/cli/test_main.py tests/memory/test_dream.py tests/tools/builtin/test_agent_tools.py tests/config/test_schema.py tests/subagent/test_pool.py -v`
- Run lint:
  - `ruff check myagent/`
- Run the full suite if a suitable Python and pytest environment is available:
  - `pytest tests/ -v`

## Commit Plan

- Review the diff and ensure `.claude/` is not staged.
- Commit locally with message `fix: close round 21 design gaps`.
- Do not push.
