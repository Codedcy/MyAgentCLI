# Task 11: MCP Fixes + Final Integration

**Files:**
- Modify: `myagent/tools/mcp/client.py` (stderr drain)
- Modify: `myagent/tools/mcp/adapter.py` ($ref/oneOf support, permission level)

**Fixes audit issues:** #39, #40

## Global Constraints
- All fixes must pass `pytest tests/ -v` before commit
- No new dependencies
- Python 3.12+

## Steps

### Step 1: MCP client stderr draining (audit #39)
- Launch background `asyncio.create_task` to read stderr line-by-line
- Log stderr output at DEBUG level
- Prevents pipe buffer deadlock from unfilled stderr pipe

### Step 2: MCP adapter schema conversion (audit #40)
- Add `_resolve_schema()` for `$ref` resolution
- Flatten `oneOf`/`anyOf` into enum descriptions
- Assign MCP tools default permission level 3 (network-write)

### Step 3: Run full integration test suite
Run: `pytest tests/integration/ -v`
Expected: PASS

### Step 4: Run full test suite
Run: `pytest tests/ -v`
Expected: All PASS

```bash
git add myagent/tools/mcp/
git commit -m "fix(mcp): stderr drain, schema ref/oneOf resolution, permission levels"
```
