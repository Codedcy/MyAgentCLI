# Task 2 Report: Fix Goal Tracker — LLM-driven evaluation

**Status:** Complete
**Commit:** `e80bb0e` — `fix(goal): LLM-driven goal evaluation instead of always-True stub`

## Summary

Replaced the always-True stub in `GoalTracker.check_goal()` with real LLM-driven evaluation. The tracker now makes an actual LLM call (in Non-think mode) to assess whether the session goal has been achieved based on conversation history.

## Changes Made

### `myagent/agent/goal.py` (modified)

1. **Constructor change**: `__init__` now accepts optional `llm: LLMProvider | None = None` parameter.
2. **Conservative fallback**: Without an LLM provider, `check_goal()` returns `achieved=False` with reasoning `"No LLM available for goal evaluation — conservative fallback."` — preventing false completion claims.
3. **LLM-driven evaluation**: `_llm_check()` method:
   - Builds a history summary from the last 20 messages (role + content preview, truncated at 300 chars)
   - Constructs an evaluation prompt instructing the LLM to return JSON only (`achieved`, `reasoning`, `remaining_work`)
   - Streams the LLM response in Non-think mode, collecting text via duck-typing (`hasattr(event, "content")`)
   - Parses the collected JSON with markdown-fence tolerance
4. **Error resilience**: All exceptions in `check_goal()` are caught; returns conservative `achieved=False` with error reasoning. Logged via `logging.getLogger("myagent.agent.goal")`.
5. **No fields changed** on `GoalCheckResult` dataclass.

### `tests/agent/test_goal.py` (replaced)

Replaced the old test class with four async tests:

| Test | What it covers |
|------|---------------|
| `test_check_goal_no_llm_conservative_fallback` | Without LLM, returns `achieved=False` |
| `test_check_goal_no_goal_returns_false` | No goal set returns false with "No goal set" |
| `test_set_clear_goal` | Basic set/get/clear lifecycle |
| `test_check_goal_with_llm_parses_json_response` | With mock LLM returning JSON, parses correctly |

## Test Results

- `tests/agent/test_goal.py`: 4/4 passed
- `tests/` (full suite): 188/188 passed, 0 failed
