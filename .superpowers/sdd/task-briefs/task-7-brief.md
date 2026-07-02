# Task 7: PermissionController confirm() — Rich interactive dialog

**Files:**
- Modify: `myagent/permissions/controller.py`

**Fixes audit issue:** #18 (confirm() is a no-op stub, always returns True)

## Global Constraints
- All fixes must pass `pytest tests/ -v` before commit
- No new dependencies (Rich already listed)
- Permission confirmations have NO timeout — wait forever per design doc §五
- Fall back to allow=True in non-interactive environments (tests, CI, piped stdin)

## Steps

### Step 1: Implement interactive confirm()

Replace `confirm()` method:
1. Check `sys.stdin.isatty()` — if not interactive, log warning and return True
2. Display Rich Panel with: tool name, permission level, params summary (truncate values to 80 chars)
3. Use `rich.prompt.Prompt.ask()` with choices [A]llow / [D]eny / [Y]es to all
4. 'A' → return True, 'D' → return False, 'Y' → set mode to allow_all and return True
5. Handle `ImportError` for missing Rich — fall back to True
6. No timeout — wait forever

### Step 2: Run tests and commit

Run: `pytest tests/permissions/test_controller.py -v`
Expected: PASS (non-TTY fallback tested)

```bash
git add myagent/permissions/controller.py tests/permissions/test_controller.py
git commit -m "fix(permissions): interactive Rich confirm dialog for permission checks"
```
