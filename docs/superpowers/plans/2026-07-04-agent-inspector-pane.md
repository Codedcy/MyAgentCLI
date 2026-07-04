# Fixed Agent Inspector Pane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the temporary/current-page status display with a fixed right-side Agent Inspector Pane that shows token, context, goal, sub-agent, tool, retry, and health state, with a narrow rail fallback on small terminals.

**Architecture:** Runtime status is separated from rendering. A Rich-based Agent Inspector Pane reads immutable snapshots from `RuntimeStatusModel`; `REPLEngine` owns the single layout controller so output streaming, prompt handling, and status refresh do not compete for terminal control.

**Tech Stack:** Python 3.12+, dataclasses, Rich Layout/Live, prompt_toolkit key bindings, pytest, ruff.

---

## Scope Check

This plan covers one subsystem: the CLI runtime status pane. It touches configuration, runtime status aggregation, Rich rendering, REPL layout integration, event wiring, tests, and user-facing docs. It does not change sub-agent scheduling rules, ReAct decision logic, model provider selection, MCP protocol behavior, or session persistence formats except for optional display fields read from existing objects.

Repository convention from `AGENTS.md`: implementation plans must not include code blocks. This plan therefore lists exact files, interface signatures, test cases, commands, dependencies, and commit points; implementation code is written only during execution.

## File Structure

- Modify `myagent/config/schema.py`: add `StatusPaneConfig` and expose it through `UIConfig` while keeping legacy `show_status_bar` and `status_bar_items`.
- Modify `myagent/config/loader.py`: normalize legacy UI config into `ui.status_pane` after merge and validate pane dimensions.
- Create `myagent/agent/runtime_status.py`: framework-neutral runtime status dataclasses and `RuntimeStatusModel`.
- Modify `myagent/agent/__init__.py`: export runtime status types used by CLI wiring and tests.
- Modify `myagent/agent/engine.py`: emit or expose context, goal, tool, LLM usage, and health status changes needed by the status model.
- Modify `myagent/cli/status.py`: replace the old status bar renderer with `AgentInspectorPane`, preserving a `StatusBar` compatibility alias during migration.
- Create `myagent/cli/layout.py`: own the Rich `Layout` and `Live` lifecycle, choose full pane versus rail, buffer output, and expose a toggle hook.
- Modify `myagent/cli/main.py`: build status components, wire LLM retry callbacks and sub-agent lifecycle callbacks to `RuntimeStatusModel`, and pass the pane into the REPL.
- Modify `myagent/cli/repl.py`: use `AgentLayoutController`, route streamed output and status events through it, bind `Ctrl+I`, and stop the layout on shutdown.
- Modify `myagent/cli/__init__.py`: export `AgentInspectorPane`, `RuntimeStatusModel` integration surface, and keep `StatusBar` compatibility.
- Modify `myagent/cli/renderer.py`: ignore status-only events so they do not print as conversation output.
- Create `tests/agent/test_runtime_status.py`: status model tests.
- Create `tests/cli/test_status.py`: inspector pane render and rail tests.
- Create `tests/cli/test_layout.py`: layout controller tests.
- Create `tests/cli/test_repl_layout.py`: REPL/layout integration tests.
- Modify `tests/config/test_schema.py`: status pane config defaults and custom values.
- Modify `tests/config/test_loader.py`: legacy config migration and new config precedence.
- Create `tests/cli/test_main_status.py`: main wiring helpers and sub-agent status callback tests.
- Modify `tests/agent/test_engine.py`: status event or status callback coverage.
- Modify `README.md`: update CLI file tree, UI config example, and status pane behavior.
- Modify `docs/superpowers/specs/2026-07-02-myagentcli-design.md` only if the implemented interface differs from the approved spec; if needed, update the sections `CLI 布局与运行状态`, `Agent Inspector Pane 展示`, and the UI config sample.

## Dependencies And Order

Tasks must be executed in order. The config task comes first because all later constructors read `config.ui.status_pane`. The runtime model comes before rendering. Rendering comes before layout. Layout comes before REPL integration. Event/status enrichment comes before final smoke testing.

Commit after each task. Do not push.

---

### Task 1: Add Status Pane Configuration

**Files:**
- Modify: `myagent/config/schema.py`
- Modify: `myagent/config/loader.py`
- Modify: `tests/config/test_schema.py`
- Modify: `tests/config/test_loader.py`

**Interfaces:**
- `StatusPaneConfig`
- `UIConfig.status_pane: StatusPaneConfig`
- `ConfigLoader._normalize_ui_config(merged: dict) -> dict`
- `ConfigLoader._validate(config: AppConfig) -> None` gains status pane width validation

- [ ] **Step 1: Write failing schema tests**

Add tests asserting default `UIConfig().status_pane.enabled is True`, `placement == "right"`, `width == 34`, `min_width == 28`, `max_width == 48`, `collapse_below_columns == 120`, `rail_width == 5`, `toggle_key == "ctrl+i"`, and sections equal `["session", "tokens", "goal", "subagents", "tools", "health"]`. Keep assertions that `show_status_bar` and `status_bar_items` still exist for backward compatibility.

- [ ] **Step 2: Write failing loader compatibility tests**

Add tests for these YAML inputs: `ui.status_pane.enabled: false` is loaded directly; legacy `ui.show_status_bar: false` maps to `config.ui.status_pane.enabled is False` when no explicit `status_pane.enabled` exists; legacy `ui.status_bar_items: [tokens]` maps to `config.ui.status_pane.sections == ["tokens"]` when no explicit `status_pane.sections` exists; explicit `ui.status_pane.sections` wins over legacy `status_bar_items` when both are present.

- [ ] **Step 3: Run focused failing tests**

Run: `pytest tests/config/test_schema.py::TestUIConfig tests/config/test_loader.py -v`

Expected: FAIL because `StatusPaneConfig` and compatibility normalization do not exist yet.

- [ ] **Step 4: Implement config schema and normalization**

Add `StatusPaneConfig` to `myagent/config/schema.py`. Add `status_pane` to `UIConfig`. Keep legacy fields in `UIConfig`. In `ConfigLoader.load()`, normalize the merged dict before converting to dataclasses. Validation should warn when `width < min_width`, `width > max_width`, `rail_width < 1`, or `collapse_below_columns < 40`.

- [ ] **Step 5: Run focused passing tests**

Run: `pytest tests/config/test_schema.py::TestUIConfig tests/config/test_loader.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

Run: `git add myagent/config/schema.py myagent/config/loader.py tests/config/test_schema.py tests/config/test_loader.py`

Run: `git commit -m "feat: add status pane config"`

---

### Task 2: Create Runtime Status Model

**Files:**
- Create: `myagent/agent/runtime_status.py`
- Modify: `myagent/agent/__init__.py`
- Create: `tests/agent/test_runtime_status.py`

**Interfaces:**
- `SessionRuntimeStatus(session_id: str, project_name: str, model: str, thinking: str)`
- `TokenRuntimeStatus(prompt_tokens: int, completion_tokens: int, turn_total: int, session_total: int, context_usage: float, context_window: int)`
- `GoalRuntimeStatus(name: str, active: bool, achieved: bool, waiting_for_user: bool, budget_used: int | None, budget_limit: int | None)`
- `SubAgentRuntimeInfo(agent_id: str, task_name: str, status: str, progress_pct: float, result_summary: str, retry_count: int, max_retries: int, duration_ms: float | None)`
- `ToolRuntimeStatus(name: str, status: str, permission_waiting: bool, last_result_summary: str, duration_ms: float | None)`
- `HealthRuntimeStatus(retry_info: str, mcp_connected: bool | None, last_error: str)`
- `RuntimeStatusSnapshot`
- `RuntimeStatusModel.snapshot() -> RuntimeStatusSnapshot`
- `RuntimeStatusModel.update_session(...) -> None`
- `RuntimeStatusModel.update_tokens(...) -> None`
- `RuntimeStatusModel.update_goal(...) -> None`
- `RuntimeStatusModel.upsert_subagent(...) -> None`
- `RuntimeStatusModel.remove_subagent(agent_id: str) -> None`
- `RuntimeStatusModel.update_tool(...) -> None`
- `RuntimeStatusModel.update_health(...) -> None`
- `RuntimeStatusModel.clear_transient() -> None`

- [ ] **Step 1: Write failing model tests**

Add tests for default snapshot values, token updates with clamped context usage, sub-agent upsert/remove, goal update, tool update, health retry update, and snapshot isolation. The snapshot isolation test mutates the returned sub-agent list and then asserts a second snapshot is unchanged.

- [ ] **Step 2: Run focused failing tests**

Run: `pytest tests/agent/test_runtime_status.py -v`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement the model**

Create framework-neutral dataclasses with no Rich or prompt_toolkit imports. Store mutable state only inside `RuntimeStatusModel`. Return copies from `snapshot()`. Clamp percentages into `0.0 <= value <= 1.0`. Keep empty-string defaults instead of `None` for values rendered directly in CLI text.

- [ ] **Step 4: Export the model**

Update `myagent/agent/__init__.py` so tests and CLI code can import `RuntimeStatusModel` and `RuntimeStatusSnapshot`.

- [ ] **Step 5: Run focused passing tests**

Run: `pytest tests/agent/test_runtime_status.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

Run: `git add myagent/agent/runtime_status.py myagent/agent/__init__.py tests/agent/test_runtime_status.py`

Run: `git commit -m "feat: add runtime status model"`

---

### Task 3: Replace Status Bar Renderer With Agent Inspector Pane

**Files:**
- Modify: `myagent/cli/status.py`
- Modify: `myagent/cli/__init__.py`
- Create: `tests/cli/test_status.py`

**Interfaces:**
- `AgentInspectorPane(config=None, status_model: RuntimeStatusModel | None = None)`
- `AgentInspectorPane.update(**kwargs) -> None` for legacy call sites
- `AgentInspectorPane.get_renderable(terminal_columns: int | None = None) -> RenderableType | None`
- `AgentInspectorPane.toggle() -> bool`
- `AgentInspectorPane.set_expanded(expanded: bool) -> None`
- `StatusBar = AgentInspectorPane` compatibility alias
- Existing `SubAgentInfo` remains accepted by `update(subagents_details=...)` until `main.py` is migrated

- [ ] **Step 1: Write failing renderer tests**

Add tests asserting full mode renders title `Agent Inspector`, session/model/thinking, token totals, context percent, goal, sub-agent rows, current tool, retry info, and health error. Add a narrow-terminal test asserting rail mode is selected below `collapse_below_columns` and does not include long task names. Add disabled-config test asserting `get_renderable()` returns `None`. Add long text test asserting render output omits overflow-prone long task summaries.

- [ ] **Step 2: Run focused failing tests**

Run: `pytest tests/cli/test_status.py -v`

Expected: FAIL because `AgentInspectorPane` does not exist.

- [ ] **Step 3: Implement inspector rendering**

Refactor `myagent/cli/status.py` so the primary class is `AgentInspectorPane`. It reads `RuntimeStatusModel.snapshot()` when a model is provided and falls back to legacy `_data` when old `update()` calls are still in use. Render full mode as a Rich `Panel` or `Group` sized for the configured width. Render rail mode as a compact Rich renderable with token/context indicator, active sub-agent count, and error/retry marker.

- [ ] **Step 4: Preserve imports**

Update `myagent/cli/__init__.py` to export `AgentInspectorPane` and keep `StatusBar` so existing imports keep working during later tasks.

- [ ] **Step 5: Run focused passing tests**

Run: `pytest tests/cli/test_status.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

Run: `git add myagent/cli/status.py myagent/cli/__init__.py tests/cli/test_status.py`

Run: `git commit -m "feat: render agent inspector pane"`

---

### Task 4: Add Rich Layout Controller

**Files:**
- Create: `myagent/cli/layout.py`
- Create: `tests/cli/test_layout.py`

**Interfaces:**
- `AgentLayoutController(console, status_pane, status_config)`
- `AgentLayoutController.start() -> None`
- `AgentLayoutController.stop() -> None`
- `AgentLayoutController.append_output(text: str, end: str = "\n") -> None`
- `AgentLayoutController.set_output_lines(lines: list[str]) -> None`
- `AgentLayoutController.refresh() -> None`
- `AgentLayoutController.toggle_inspector() -> bool`
- `AgentLayoutController.render_once() -> None`
- `AgentLayoutController.is_live: bool`

- [ ] **Step 1: Write failing layout tests**

Add tests asserting a 160-column console builds a layout with output plus full right inspector, an 80-column console uses rail mode, output buffering trims to the existing 500-line behavior, `toggle_inspector()` flips expanded state, `stop()` is idempotent, and render failures are logged with `category="error"` and `context="cli_layout_refresh"`.

- [ ] **Step 2: Run focused failing tests**

Run: `pytest tests/cli/test_layout.py -v`

Expected: FAIL because `myagent.cli.layout` does not exist.

- [ ] **Step 3: Implement the controller**

Create a controller that owns one Rich `Layout` and one optional `Live` instance. It chooses full pane or rail from console width and `status_pane.collapse_below_columns`. It buffers streamed output separately from status rendering. It must never create a second Live display if one is already active.

- [ ] **Step 4: Implement graceful degradation**

If Rich Live update fails, log through `logging.getLogger("myagent.cli.layout")` and fall back to direct console output for that update while keeping the REPL running.

- [ ] **Step 5: Run focused passing tests**

Run: `pytest tests/cli/test_layout.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

Run: `git add myagent/cli/layout.py tests/cli/test_layout.py`

Run: `git commit -m "feat: add agent layout controller"`

---

### Task 5: Wire Status Model In CLI Startup

**Files:**
- Modify: `myagent/cli/main.py`
- Create: `tests/cli/test_main_status.py`

**Interfaces:**
- `_build_status_components(config, project_dir) -> tuple[RuntimeStatusModel, AgentInspectorPane | None]`
- `_wire_subagent_status(subagent_pool, status_model: RuntimeStatusModel) -> None`
- `_extract_task_name(prompt: str, max_len: int = 20) -> str`

- [ ] **Step 1: Write failing startup wiring tests**

Add tests using fake config and fake project dir. Assert `_build_status_components()` creates a model with project name, model name, and thinking mode. Assert it returns `None` for pane when `config.ui.status_pane.enabled is False`.

- [ ] **Step 2: Write failing sub-agent callback tests**

Add tests with fake pool handles for running, retrying, completed, failed, interrupted, and result-consumed states. Assert callbacks update `RuntimeStatusModel` with active counts, task names, progress percent, retry counts, result summaries, and removal after `RESULT_CONSUMED`.

- [ ] **Step 3: Run focused failing tests**

Run: `pytest tests/cli/test_main_status.py -v`

Expected: FAIL because helper functions do not exist.

- [ ] **Step 4: Implement startup helpers**

Move the current inline status-bar construction and sub-agent callback logic out of `async_main()` into helper functions. Use `RuntimeStatusModel.upsert_subagent()` instead of building `SubAgentInfo` lists. Keep task-name extraction behavior compatible with the current 20-character truncation.

- [ ] **Step 5: Wire LLM retry callback**

Update the `LLMProvider` retry callback to call `status_model.update_health(retry_info=...)`. Keep retry behavior unchanged.

- [ ] **Step 6: Pass the pane into REPL**

Construct `AgentInspectorPane(config.ui.status_pane, status_model)` when enabled and pass it to `REPLEngine`. Use local variable names `status_model` and `status_pane`; keep compatibility for code paths that still refer to `status_bar` until Task 6 removes them.

- [ ] **Step 7: Run focused passing tests**

Run: `pytest tests/cli/test_main_status.py tests/cli/test_main.py -v`

Expected: PASS.

- [ ] **Step 8: Commit**

Run: `git add myagent/cli/main.py tests/cli/test_main_status.py`

Run: `git commit -m "feat: wire inspector status model"`

---

### Task 6: Integrate Fixed Pane Into REPL

**Files:**
- Modify: `myagent/cli/repl.py`
- Create: `tests/cli/test_repl_layout.py`

**Interfaces:**
- `REPLEngine(..., status_pane=None, status_model=None, status_bar=None, ...)`
- `REPLEngine._layout_controller`
- `REPLEngine._update_status_from_event(event) -> None`
- `REPLEngine._toggle_inspector() -> None`

- [ ] **Step 1: Write failing REPL layout tests**

Add tests asserting a REPL constructed with `status_pane` creates `AgentLayoutController`, `_output_to_console()` appends to the controller instead of printing directly, `_shutdown()` stops the controller, and a `Done` event with usage updates `RuntimeStatusModel` token totals.

- [ ] **Step 2: Write failing tool event tests**

Add tests asserting `ToolCallStart` sets current tool status to running, `ToolCallEnd` sets current tool status to completed or failed based on `ToolResult.error`, and `AskUserQuestion` marks the goal/status model as waiting for user.

- [ ] **Step 3: Run focused failing tests**

Run: `pytest tests/cli/test_repl_layout.py -v`

Expected: FAIL because REPL has no layout controller integration.

- [ ] **Step 4: Update constructor and output path**

Accept `status_pane` and `status_model`. Preserve `status_bar` as an alias for compatibility. Replace direct status-bar one-time printing with `AgentLayoutController.render_once()` when the pane exists. Route `_output_to_console()` through the controller.

- [ ] **Step 5: Add `Ctrl+I` binding**

Extend the existing prompt_toolkit `KeyBindings` setup so `Ctrl+I` calls `_toggle_inspector()` and refreshes the layout. The binding must not submit or mutate the current input buffer.

- [ ] **Step 6: Update status from engine events**

Call `_update_status_from_event(event)` inside the engine event loop before rendering output. Cover `Done`, `ToolCallStart`, `ToolCallEnd`, `AskUserQuestion`, `Error`, and `Interrupted`. Keep existing renderer behavior for visible conversation output.

- [ ] **Step 7: Stop layout on shutdown**

Replace direct `_live.stop()` shutdown logic with `AgentLayoutController.stop()` while preserving the existing session end flow and goodbye message.

- [ ] **Step 8: Run focused passing tests**

Run: `pytest tests/cli/test_repl_layout.py tests/cli/test_status.py tests/cli/test_layout.py -v`

Expected: PASS.

- [ ] **Step 9: Commit**

Run: `git add myagent/cli/repl.py tests/cli/test_repl_layout.py`

Run: `git commit -m "feat: integrate inspector pane in repl"`

---

### Task 7: Emit Runtime Status From Agent Engine

**Files:**
- Modify: `myagent/agent/engine.py`
- Modify: `myagent/cli/renderer.py`
- Modify: `tests/agent/test_engine.py`
- Modify: `tests/cli/test_renderer.py`

**Interfaces:**
- `StatusUpdate(scope: str, data: dict[str, object])`
- `AgentEvent` union includes `StatusUpdate`
- Renderer returns `None` for `StatusUpdate`

- [ ] **Step 1: Write failing engine status tests**

Add tests asserting the engine yields a context status update after context usage is estimated, yields a goal status update when a goal check starts and when the goal is achieved or remains open, and yields a health status update when an LLM stream error is caught.

- [ ] **Step 2: Write failing renderer test**

Add a renderer test asserting `Renderer.render_event(StatusUpdate(...)) is None`.

- [ ] **Step 3: Run focused failing tests**

Run: `pytest tests/agent/test_engine.py tests/cli/test_renderer.py -v`

Expected: FAIL because `StatusUpdate` is not defined.

- [ ] **Step 4: Implement status events**

Add a small dataclass event in `myagent/agent/engine.py`. Yield status events without changing the existing visible event order for text, tool calls, questions, errors, and done. Do not persist `StatusUpdate` into transcripts.

- [ ] **Step 5: Connect status events in REPL**

If Task 6 did not already handle `StatusUpdate`, update `REPLEngine._update_status_from_event()` to merge `context`, `goal`, and `health` scopes into `RuntimeStatusModel`.

- [ ] **Step 6: Run focused passing tests**

Run: `pytest tests/agent/test_engine.py tests/cli/test_renderer.py tests/cli/test_repl_layout.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

Run: `git add myagent/agent/engine.py myagent/cli/renderer.py myagent/cli/repl.py tests/agent/test_engine.py tests/cli/test_renderer.py tests/cli/test_repl_layout.py`

Run: `git commit -m "feat: emit runtime status events"`

---

### Task 8: Documentation, Compatibility Sweep, And Final Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-02-myagentcli-design.md` only if actual interfaces differ from the approved spec sections named in the file structure

- [ ] **Step 1: Update README**

Update the architecture file tree entry from `status.py — Live 状态栏` to the Agent Inspector Pane wording. Add or update the UI config example to use `ui.status_pane`. Mention that legacy `show_status_bar` and `status_bar_items` are still accepted.

- [ ] **Step 2: Search for stale old terms**

Run: `rg -n "StatusBar|Status Bar|Top Bar|status_bar|show_status_bar|status_bar_items|状态栏" myagent tests README.md docs/superpowers/specs/2026-07-02-myagentcli-design.md`

Expected: Only compatibility aliases, migration tests, and explicit legacy-config notes remain.

- [ ] **Step 3: Run full tests**

Run: `pytest tests/ -v`

Expected: PASS.

- [ ] **Step 4: Run lint**

Run: `ruff check myagent/`

Expected: PASS.

- [ ] **Step 5: Run whitespace check**

Run: `git diff --check`

Expected: PASS, allowing only Git CRLF warnings if the local checkout emits them.

- [ ] **Step 6: Run CLI smoke checks**

Run: `python -m myagent.cli.main --list-sessions`

Expected: exit code 0 and a session list or a no-sessions message.

Run: `python -m myagent.cli.main --help`

Expected: exit code 0 and argparse help text.

- [ ] **Step 7: Inspect final diff**

Run: `git diff --stat`

Expected: Changes are limited to status pane implementation, tests, and docs.

- [ ] **Step 8: Commit**

Run: `git add myagent tests README.md docs/superpowers/specs/2026-07-02-myagentcli-design.md`

Run: `git commit -m "docs: update inspector pane documentation"`

---

## Final Acceptance Criteria

- `ui.status_pane` is the primary configuration surface.
- Legacy `ui.show_status_bar` and `ui.status_bar_items` still load and map to pane settings.
- The CLI renders a fixed right-side Agent Inspector Pane when terminal width is at or above the configured threshold.
- Narrow terminals automatically show a compact rail.
- `Ctrl+I` toggles expanded/collapsed Inspector state without submitting the prompt.
- Token, context, goal, sub-agent, tool, retry, and health state update through `RuntimeStatusModel`.
- The pane renderer has no direct dependency on Agent Engine, Tool Registry, Sub-Agent Pool, or LLM Provider internals.
- `REPLEngine` owns a single layout controller; no component starts a competing Live display.
- The fallback path remains usable if Rich or prompt_toolkit integration fails.
- All focused tests, full pytest suite, ruff, diff check, and CLI smoke checks pass.
