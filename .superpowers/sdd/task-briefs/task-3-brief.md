# Task 3: Wire CLI — Renderer, StatusBar, CommandDispatcher, --resume, multi-line REPL

**Files:**
- Modify: `myagent/cli/main.py`
- Modify: `myagent/cli/repl.py`
- Create/modify: `myagent/cli/renderer.py` (if needed)

**Interfaces:**
- Consumes: `CommandDispatcher`, `Renderer`, `StatusBar`, `SessionManager.resume()`
- Produces: Fully wired CLI with working slash commands, formatted output, status bar, and --resume
- Fixes audit issues: #5, #6, #7, #43

## Global Constraints

- All fixes must pass `pytest tests/ -v` before commit
- No new dependencies
- Follow existing patterns: async throughout, Rich for rendering
- Each task ends with `git commit` using conventional commit format
- DRY, YAGNI, TDD

## Steps

### Step 1: Fix main.py wiring

In `async_main()`, after the AgentEngine creation (around line 125):

1. **Create and wire CommandDispatcher:**
```python
from myagent.cli.commands import CommandDispatcher
commands = CommandDispatcher()
```

2. **Create and wire Renderer + StatusBar:**
```python
from myagent.cli.renderer import Renderer
from myagent.cli.status import StatusBar
renderer = Renderer()
status_bar = StatusBar(config.ui) if config.ui.show_status_bar else None
```

3. **Fix --resume to actually call SessionManager.resume():**
```python
if args.resume:
    session_id = None if args.resume == "__latest__" else args.resume
    session = await session_mgr.resume(session_id, project_dir)
    if session:
        repl = REPLEngine(
            engine=engine, commands=commands, session_mgr=session_mgr,
            config=config, project_dir=project_dir,
            renderer=renderer, status_bar=status_bar,
        )
        repl._current_session = session
        await repl.run()
        return 0
    else:
        print(f"No session found to resume.")
        return 1
```

4. **Pass commands, renderer, status_bar to REPLEngine for new sessions:**
```python
repl = REPLEngine(
    engine=engine, commands=commands, session_mgr=session_mgr,
    config=config, project_dir=project_dir,
    renderer=renderer, status_bar=status_bar,
)
```

### Step 2: Fix repl.py

1. **Accept new constructor params:** `renderer`, `status_bar`
2. **Enable multi-line input:** `PromptSession(..., multiline=True)`
3. **Ctrl+C interrupts, doesn't exit:** Add KeyBindings for c-c to clear buffer
4. **Use Renderer instead of print():** Pass events to `self._renderer.render_event(event)` instead of `print()`
5. **Wire CommandContext with all fields:** Set `session_manager`, `goal_tracker`, `skill_registry` when dispatching commands
6. **Graceful shutdown:** Ctrl+D/exit calls `_shutdown()` which stops status bar and calls `end_session`

### Step 3: Ensure Renderer exists

If `myagent/cli/renderer.py` is minimal, add `render_event()` method that dispatches by event type:
- TextChunk → print content
- ThinkingChunk → hidden
- ToolCallStart → print tool name
- ToolCallEnd → print result/error
- Done → print usage if available
- Error → print error
- AskUserQuestion → print question

### Step 4: Run tests and commit

Run: `pytest tests/cli/ -v`
Expected: PASS

```bash
git add myagent/cli/main.py myagent/cli/repl.py myagent/cli/renderer.py tests/cli/
git commit -m "fix(cli): wire CommandDispatcher, Renderer, StatusBar; fix --resume; enable multi-line REPL"
```
