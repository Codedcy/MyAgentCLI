# Task 9: Tool System Fixes — ToolResult, Registry, File, Search, Agent Tools

**Files:**
- Modify: `myagent/tools/base.py` (ToolResult success/artifacts)
- Modify: `myagent/tools/registry.py` (source tracking, builtin priority)
- Modify: `myagent/tools/builtin/file_tools.py` (2000-line read cap)
- Modify: `myagent/tools/builtin/search_tools.py` (missing grep params)
- Modify: `myagent/tools/builtin/agent_tools.py` (model override param)

**Fixes audit issues:** #16, #17, #36, #37, #38

## Global Constraints
- All fixes must pass `pytest tests/ -v` before commit
- Backward compatible — existing tool callers must not break
- Python 3.12+

## Steps

### Step 1: ToolResult — add success, artifacts (audit #16)
- Add `success: bool = True` field
- Add `artifacts: list[str] = field(default_factory=list)` field
- Auto-set `success = False` when `error` is not None in `__post_init__`

### Step 2: ToolRegistry — source tracking (audit #17)
- Add `ToolEntry` dataclass with `tool` and `source` fields
- `register()` accepts `source: str = "builtin"` param
- Built-in tools take priority over MCP (don't overwrite with MCP)
- Add `get_source(name) -> str | None` method

### Step 3: ReadTool — 2000-line cap (audit #36)
- When no offset/limit and lines > 2000: truncate and add note

### Step 4: GrepTool — missing params (audit #37)
- Add `-n` (line numbers, default true), `-o` (only matching)
- Add `type` (file type filter), `offset`, `multiline`

### Step 5: SpawnSubagentTool — model param (audit #38)
- Add `model` param with enum: sonnet, opus, haiku, fable

### Step 6: Run tests and commit
Run: `pytest tests/tools/ -v`
Expected: PASS
```bash
git add myagent/tools/
git commit -m "fix(tools): ToolResult success/artifacts, registry tracking, param gaps"
```
