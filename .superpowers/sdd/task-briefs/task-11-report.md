# Task 11 Report: MCP Fixes + Final Integration

**Status:** COMPLETE  
**Date:** 2026-07-03  
**Commit:** `ce003e0`

## Summary

Two audit fixes applied to the MCP module:

### Fix 1: Stderr Draining (audit #39)

**File:** `myagent/tools/mcp/client.py`

Added a background `asyncio.create_task` (`_drain_stderr`) that reads the MCP subprocess stderr line-by-line and logs each line at DEBUG level. This prevents the OS pipe buffer from filling up and deadlocking the subprocess. The task is started in `start()` alongside the reader loop and properly cancelled in `shutdown()`.

### Fix 2: Schema $ref/oneOf Resolution (audit #40)

**File:** `myagent/tools/mcp/adapter.py`

Added three new methods:
- `_resolve_schema()` -- recursively traverses a JSON schema dict, resolving `$ref` pointers and `oneOf`/`anyOf` alternatives
- `_resolve_ref()` -- resolves local `$ref` pointers (e.g. `#/$defs/Foo`, `#/definitions/Bar`) against the root schema
- `_flatten_oneof()` -- resolves each `oneOf`/`anyOf` alternative into a self-contained concrete schema

The `_translate_schema()` method now calls `_resolve_schema()` first, so that OpenAI function-calling schemas see resolved types instead of JSON Schema indirections.

Also added `permission_level: int = 3` as a class attribute on `MCPToolAdapter`, explicitly marking MCP tools at level 3 (network-write).

## Test Results

- **Unit tests:** 213 passed, 0 failed
- **Integration tests:** 4 passed, 0 failed
- **MCP-specific tests:** 9 passed, 0 failed

## Files Changed

| File | Change |
|------|--------|
| `myagent/tools/mcp/client.py` | +50 lines: `_stderr_task` field, `_drain_stderr()` method, cancellation in `shutdown()` |
| `myagent/tools/mcp/adapter.py` | +97/-13 lines: `_resolve_schema()`, `_resolve_ref()`, `_flatten_oneof()`, `permission_level`, updated `_translate_schema()` |
