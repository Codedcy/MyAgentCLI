# Task 6: Implement Dream Engine Memory Consolidation

**Files:**
- Modify: `myagent/memory/dream.py`

**Fixes audit issue:** #4 (dream engine complete stub — returns DreamResult with all zeros)

## Global Constraints
- All fixes must pass `pytest tests/ -v` before commit
- No new dependencies
- Follow existing patterns
- Python 3.12+

## Steps

### Step 1: Implement real dream cycle

Replace `DreamEngine.run()` to actually consolidate memories:

1. Scan existing memories from `self.memory_store.list_all("project")` and `list_all("user")`
2. Find duplicate descriptions → keep newer, delete older
3. Find empty/placeholder memories (< 20 chars content) → remove
4. Write dream log with actions taken to `~/.myagent/dreams/YYYY-MM-DD.md`
5. Update `last_dream.json` state file (last_run timestamp, reset round_count)
6. Return `DreamResult` with actual counts of created/updated/deleted

The dream principles: never modify project code, never ask user, always background.

### Step 2: Run tests and commit

Run: `pytest tests/memory/test_dream.py -v`
Expected: PASS

```bash
git add myagent/memory/dream.py tests/memory/test_dream.py
git commit -m "fix(dream): implement real memory consolidation — dedup, clean empty, write log"
```
