# Task 9 Report: Tool System Fixes

**Status**: Complete

**Commit**: `710c2d2` — `fix(tools): ToolResult success/artifacts, registry tracking, param gaps`

**Branch**: main

**Date**: 2026-07-03

## Summary

Five tool-system fixes addressing audit issues #16, #17, #36, #37, #38. All changes are backward compatible — existing callers and tests work without modification.

## Changes

### 1. ToolResult — success + artifacts (audit #16)
**File**: `myagent/tools/base.py`

- Added `success: bool = True` field (auto-set to `False` in `__post_init__` when `error is not None`)
- Added `artifacts: list[str]` field (defaults to empty list)

### 2. ToolRegistry — source tracking (audit #17)
**File**: `myagent/tools/registry.py`

- Added `ToolEntry` dataclass with `tool` and `source` fields
- `register()` now accepts `source: str = "builtin"` parameter
- Built-in tools cannot be overwritten by MCP tools with the same name
- Added `get_source(name) -> str | None` method
- All existing methods (`get`, `list_all`, `get_schemas`, `get_schemas_for`, `__len__`, `__contains__`) maintain identical public interfaces

### 3. ReadTool — 2000-line cap (audit #36)
**File**: `myagent/tools/builtin/file_tools.py`

- When no `offset` or `limit` is provided and the file exceeds 2000 lines, output is truncated to the first 2000 lines with a note indicating the total line count and how to read more
- Files under 2000 lines: behavior unchanged

### 4. GrepTool — missing params (audit #37)
**File**: `myagent/tools/builtin/search_tools.py`

New parameters added:
- `-n` (bool, default true): show/hide line numbers in output
- `-o` (bool, default false): only matching — print matched portions only
- `type` (string): file type filter mapped to rg `--type`; Python fallback uses a `_TYPE_EXTENSIONS` mapping for common types
- `offset` (int, default 0): skip first N lines/entries (post-process for rg, native for Python fallback)
- `multiline` (bool, default false): enable `re.MULTILINE | re.DOTALL` for the Python fallback; maps to `--multiline --multiline-dotall` for rg

Both ripgrep and pure-Python paths handle all new parameters.

### 5. SpawnSubagentTool — model param (audit #38)
**File**: `myagent/tools/builtin/agent_tools.py`

- Added `model` parameter with enum: `sonnet`, `opus`, `haiku`, `fable`
- Passed through to `pool.spawn()` as `model=params.get("model")`

## Test Summary

```
213 passed, 0 failed, 3 warnings in 24.71s
```

All existing tests pass without modification. No regressions detected.
