---
date: 2026-07-03
round: 2
gaps_to_fix: 16
source_report: docs/gap-reports/2026-07-03-gap-round-2.md
---

# Fix Plan — Round 2

## Summary
Fix 16 gaps across 12 files.

---

## Task 1: Fix /skill-name forced invocation (gap-2-01)
- **Gap IDs**: gap-2-01
- **Files**: `myagent/cli/commands.py`, `myagent/cli/repl.py`
- **Approach**:
  1. In `CommandDispatcher.dispatch()`, before returning "Unknown command", check if the command name matches a skill in `ctx.skill_registry`.
  2. If matched, call `ctx.skill_registry.get(cmd_name)` and return a `CommandResult` indicating the skill was invoked, setting a `skill_invoked` flag.
  3. In `REPLEngine.process_input()`, after dispatching, if result has `skill_invoked=True`, inject the skill into the agent engine for the next input (or immediately trigger the engine with a "run skill" instruction).
  4. Since the skill invocation mechanism in AgentEngine already uses `skill_invoke` as a virtual tool call, the REPL should signal the engine to load the skill for the next turn.
- **Verification**: Type `/code-review` at REPL and verify it loads the code-review skill instead of returning "Unknown command".

---

## Task 2: Dream engine memory creation/update from analysis (gap-2-02)
- **Gap IDs**: gap-2-02
- **Files**: `myagent/memory/dream.py`
- **Approach**:
  1. After scanning transcripts for patterns, when correction_count >= 2 is found, create a new memory file via `self.memory_store.write()` capturing the correction pattern as a convention.
  2. When contradictions are detected between memories, merge them by keeping the newer one and updating its content to note the resolution. Mark the older one for deletion.
  3. Add an LLM-powered consolidation step: spawn a background sub-agent (or use inline LLM) to analyze findings from transcript scans and contradictions, then generate new/updated memory files.
  4. Update `DreamResult.memories_created` and `memories_updated` counters accordingly.
- **Verification**: Run dream engine with synthetic session data containing corrections; verify new memory files are created.

---

## Task 3: Add MCP prompts/list call during startup (gap-2-03)
- **Gap IDs**: gap-2-03
- **Files**: `myagent/tools/mcp/client.py`, `myagent/cli/main.py`
- **Approach**:
  1. Add `list_prompts()` method to `MCPClient` that calls `prompts/list`.
  2. In `_start_single_mcp_server()`, call `client.list_prompts()` after `client.list_resources()`.
  3. Log the number of prompts discovered.
- **Verification**: Inspect startup logs to confirm `prompts/list` is called for each MCP server.

---

## Task 4: Implement permission config YAML persistence (gap-2-04)
- **Gap IDs**: gap-2-04
- **Files**: `myagent/agent/session.py`
- **Approach**:
  1. Replace the placeholder message with actual YAML file write logic.
  2. Determine target config file: user-level `~/.myagent/config.yaml` (create if not exists).
  3. Read existing config YAML, deep-merge permission changes (auto_allow.commands additions, auto_deny.commands additions, default_mode changes).
  4. Write the merged config back to disk.
  5. Handle the case where `project_dir/.myagent/config.yaml` exists (write to project-level instead).
- **Verification**: Run a session, add permission rules, end session, confirm YAML file is written.

---

## Task 5: Add auto-completion to REPL (gap-2-05)
- **Gap IDs**: gap-2-05
- **Files**: `myagent/cli/repl.py`
- **Approach**:
  1. Create a custom `Completer` class that provides completions for:
     - Slash commands (`/mode`, `/goal`, `/exit`, etc.) when input starts with `/`.
     - Skill names (from skill registry) after `/`.
     - Mode values after `/mode` (think-high, think-max, non-think).
     - File paths when typing arguments (optional, best-effort).
  2. Pass the completer to `PromptSession`.
- **Verification**: Type `/m<TAB>` and verify it completes to `/mode`, then `<TAB>` to cycle through mode values.

---

## Task 6: Write LLM response JSON files to .prompts/ (gap-2-06)
- **Gap IDs**: gap-2-06
- **Files**: `myagent/llm/provider.py`
- **Approach**:
  1. Collect the full response data during streaming (text chunks, tool calls, usage).
  2. After the streaming loop completes successfully, write a response JSON file to `.prompts/` with the same timestamp-based naming as the request file.
  3. The response file should include the collected text content, tool calls, usage info, and latency.
- **Verification**: Set `llm_prompts=true` and DEBUG level, make a query, verify `.prompts/` has both request and response files.

---

## Task 7: Unify StatusBar and REPL into shared Rich layout (gap-2-07)
- **Gap IDs**: gap-2-07
- **Files**: `myagent/cli/status.py`, `myagent/cli/repl.py`
- **Approach**:
  1. Remove the standalone `Live` instance from `StatusBar`.
  2. In `REPLEngine`, create a shared `Live` instance with a `Layout` (top=status bar, main=output area).
  3. StatusBar updates go through the shared Live's `update()` method.
  4. REPL output (streaming text, results) is rendered into the "main" area of the shared layout.
  5. Provide a method for StatusBar to get its renderable, and REPL refreshes the Live display.
- **Verification**: Run the REPL and verify status bar appears at top while streaming output flows below without overlap.

---

## Task 8: Enhance sub-agent status bar details (gap-2-08)
- **Gap IDs**: gap-2-08
- **Files**: `myagent/cli/status.py`, `myagent/cli/main.py`
- **Approach**:
  1. Extend `SubAgentHandle` to track prompt summary, task name, progress percentage, and retry count.
  2. Update `StatusBar._render()` to format sub-agent details with emoji indicators matching the spec:
     - `⏳` for running with progress percentage
     - `✅` for completed with result count
     - `🔄` for retrying with attempt count
  3. In `main.py`, update the spawn wrapper to pass richer status info (task name extracted from prompt).
- **Verification**: Spawn sub-agents and verify status bar shows rich details per spec.

---

## Task 9: Enforce compression minimum_savings debounce (gap-2-09)
- **Gap IDs**: gap-2-09
- **Files**: `myagent/context/compression.py`
- **Approach**:
  1. In `compact()`, after Layer 2 summarization, calculate the actual token savings percentage.
  2. If savings are below `config.minimum_savings` (default 0.10), skip further compression layers for this cycle.
  3. Log a debug message when compression is skipped due to insufficient savings.
- **Verification**: Test with mock messages where Layer 2 produces minimal savings; verify Layers 3+ are skipped.

---

## Task 10: Add prompt_tokens/completion_tokens to LLM response log (gap-2-10)
- **Gap IDs**: gap-2-10
- **Files**: `myagent/llm/provider.py`
- **Approach**:
  1. In `_complete_with_model()`, track `prompt_tokens` and `completion_tokens` from the `Done` event's `Usage` object.
  2. Add `prompt_tokens` and `completion_tokens` fields to the response log's `extra` dict alongside the existing `token_consumption` (which maps to total_tokens).
- **Verification**: Check log output for LLM response entries; verify prompt_tokens and completion_tokens are present.

---

## Task 11: Add tokens_used_this_turn to Agent ReAct log (gap-2-11)
- **Gap IDs**: gap-2-11
- **Files**: `myagent/agent/engine.py`
- **Approach**:
  1. Track token usage per iteration by accumulating from the LLM `Done` event's usage info.
  2. At the end of each iteration, log `tokens_used_this_turn` in the extra dict.
  3. The log entry becomes: `logger.info("ReAct iteration %d", iteration, extra={"category": "agent", "tokens_used_this_turn": tokens})`.
- **Verification**: Run a ReAct loop and check logs for tokens_used_this_turn field.

---

## Task 12: Add system startup metadata to log (gap-2-12)
- **Gap IDs**: gap-2-12
- **Files**: `myagent/logging/logger.py`
- **Approach**:
  1. In `LogManager.setup()`, after initialization, compute `config_hash` (SHA256 of config dict), `python_version`, and `platform`.
  2. Add these fields to the startup log entry's `extra_fields`.
- **Verification**: Check the first log entry after startup for config_hash, python_version, platform fields.

---

## Task 13: Delete stale memories in dream engine (gap-2-13)
- **Gap IDs**: gap-2-13
- **Files**: `myagent/memory/dream.py`
- **Approach**:
  1. In the stale memory detection loop (step 6), actually delete files that are > 30 days unmodified (not just report them).
  2. Call `self.memory_store.delete(mf.name)` for each stale memory.
  3. Update the delete counter and add an action entry.
- **Verification**: Create a memory with old mtime, run dream, verify file is deleted.

---

## Task 14: Fix ContextBuilder variable naming to match 6-layer model (gap-2-14)
- **Gap IDs**: gap-2-14
- **Files**: `myagent/context/builder.py`
- **Approach**:
  1. Rename `l5` (active skill content) to `skill_content` to avoid confusion with context layers.
  2. Rename `l6` (goal context) to `goal_context`.
  3. Keep `l2`, `l3`, `l4` as they correctly correspond to L2 (skills index), L3 (project), L4 (memory).
  4. Add clear comments mapping each variable to the spec's layer model.
  5. In the assembly section, use descriptive names in comments.
- **Verification**: Read the code; verify variable names are descriptive and comments reference the correct layer numbers.

---

## Task 15: Create missing resource directories for built-in skills (gap-2-15)
- **Gap IDs**: gap-2-15
- **Files**: `myagent/skills/builtin/code-review/`, `myagent/skills/builtin/brainstorming/`, `myagent/skills/builtin/tdd/`
- **Approach**:
  1. Create `myagent/skills/builtin/code-review/references/` with `security-checklist.md`.
  2. Create `myagent/skills/builtin/code-review/scripts/` with a `.gitkeep`.
  3. Create `myagent/skills/builtin/brainstorming/references/` with a `.gitkeep`.
  4. Create `myagent/skills/builtin/tdd/templates/` with a `.gitkeep`.
- **Verification**: Verify directories exist with `ls` commands.

---

## Task 16: Wire schema parameter through SubAgentWorker for structured output (gap-2-16)
- **Gap IDs**: gap-2-16
- **Files**: `myagent/tools/builtin/agent_tools.py`, `myagent/subagent/pool.py`, `myagent/subagent/worker.py`
- **Approach**:
  1. Add `schema` parameter to `SubAgentWorker.__init__()`.
  2. Store it as `self.schema`.
  3. When `schema` is provided, append an instruction to the sub-agent's system prompt requiring the final response to be valid JSON matching the schema.
  4. After the worker's ReAct loop returns, validate the output against the schema (if output looks like JSON). If valid, wrap it; if not, include a note that schema validation failed.
  5. Pass `schema` through `pool.spawn()` → `_run_background()` → `SubAgentWorker()`.
- **Verification**: Spawn a sub-agent with a schema parameter and verify the schema instruction appears in the worker's system prompt.

---

## Implementation Order
Tasks are organized by file to minimize conflicts:
1. gap-2-14 → `myagent/context/builder.py`
2. gap-2-09 → `myagent/context/compression.py`
3. gap-2-10, gap-2-06 → `myagent/llm/provider.py`
4. gap-2-11 → `myagent/agent/engine.py`
5. gap-2-16 → `myagent/subagent/worker.py`, `myagent/subagent/pool.py`, `myagent/tools/builtin/agent_tools.py`
6. gap-2-01 → `myagent/cli/commands.py`, `myagent/cli/repl.py`
7. gap-2-05 → `myagent/cli/repl.py`
8. gap-2-07, gap-2-08 → `myagent/cli/status.py`, `myagent/cli/repl.py`, `myagent/cli/main.py`
9. gap-2-04 → `myagent/agent/session.py`
10. gap-2-02, gap-2-13 → `myagent/memory/dream.py`
11. gap-2-03 → `myagent/tools/mcp/client.py`, `myagent/cli/main.py`
12. gap-2-12 → `myagent/logging/logger.py`
13. gap-2-15 → `myagent/skills/builtin/*/`
