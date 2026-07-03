---
date: 2026-07-03
round: 9
gaps_to_fix: 10
source_report: docs/gap-reports/2026-07-03-gap-round-9.md
---

# Fix Plan -- Round 9

## Summary
Fix 10 gaps across 12 files.

---

## Task 1: Honor session.save_transcripts config in persistence (G1)
- **Gap IDs**: R9-G1
- **Files**: `myagent/context/persistence.py`, `myagent/agent/engine.py`, `myagent/agent/session.py`
- **Approach**:
  1. Add optional `config` parameter to `SessionStore.__init__`.
  2. In `_write_transcripts()`, check `config.session.save_transcripts` before writing.
  3. In `create_session()`, `save_turn()`, `save_tool_call()`, and `engine._persist_turn()`, gate writes on the config flag.
  4. When `save_transcripts` is False, still maintain directories for subagents/tools/summaries but skip transcript files.
- **Verification**: Set `session.save_transcripts: false` in config, verify no transcript files created. Set `true`, verify they are created.

---

## Task 2: Honor session.transcript_format config (G2)
- **Gap IDs**: R9-G2
- **Files**: `myagent/context/persistence.py`
- **Approach**:
  1. In `_write_transcripts()`, check `config.session.transcript_format` before writing each format.
  2. Only write `transcript.json` if `"json" in transcript_format`.
  3. Only write `transcript.md` if `"markdown" in transcript_format`.
  4. Apply same gating to `_write_closed_session()`.
- **Verification**: Set `transcript_format: [json]`, verify only JSON written. Set `[markdown]`, verify only MD written. Set `[]`, verify neither written.

---

## Task 3: Refactor DreamEngine to spawn background sub-agent with LLM analysis (G3, G10)
- **Gap IDs**: R9-G3, R9-G10
- **Files**: `myagent/memory/dream.py`, `myagent/cli/main.py`, `myagent/cli/repl.py`
- **Approach**:
  1. Add optional `subagent_pool` parameter to `DreamEngine.__init__`.
  2. Add new method `DreamEngine.run_as_subagent()` that:
     a. Constructs a detailed prompt including all memory names/descriptions/content summaries.
     b. Scans recent session transcripts for patterns.
     c. Spawns a sub-agent via `subagent_pool.spawn()` with the analysis prompt.
     d. Waits for the sub-agent result (which includes proposed memory changes).
     e. Parses the result and applies memory changes.
  3. Modify `run()` to delegate to `run_as_subagent()` when `subagent_pool` is available.
  4. Fall back to inline rule-based analysis when `subagent_pool` is None (backward compat).
  5. The sub-agent prompt instructs it to detect: new conventions, repeated corrections, stale memories, semantic contradictions, and overlapping facts.
  6. Wire `subagent_pool` into `DreamEngine` in `main.py`.
  7. **G10 is addressed by this**: the sub-agent uses LLM reasoning for semantic contradiction detection instead of hardcoded word pairs.
- **Verification**: Manual trigger `/dream`, verify sub-agent is spawned (status bar shows it). Verify dream log contains LLM-driven analysis. Verify backward compat when no sub-agent pool.

---

## Task 4: Implement ui.syntax_highlight config (G4)
- **Gap IDs**: R9-G4
- **Files**: `myagent/cli/repl.py`, `myagent/cli/main.py`
- **Approach**:
  1. Pass `config` to `REPLEngine` (already done -- `self._config` is available).
  2. When `config.ui.syntax_highlight` is True:
     a. Configure `PromptSession` with a `PygmentsLexer` for Python (or detect language from project).
     b. In the renderer/output path, use Rich `Syntax` for code blocks (fenced code blocks in markdown output).
  3. When False, use plain text lexer (no highlighting).
  4. Apply a Python lexer by default since code input is often Python, with fallback to plain text.
- **Verification**: Toggle `ui.syntax_highlight` and verify input gets highlighted vs plain. Check Rich output for code blocks.

---

## Task 5: Honor ui.streaming config in LLM provider (G5)
- **Gap IDs**: R9-G5
- **Files**: `myagent/llm/provider.py`, `myagent/cli/main.py`
- **Approach**:
  1. Pass `streaming` flag from `config.ui.streaming` to `LLMProvider`.
  2. In `LLMProvider.complete()`, check the streaming flag.
  3. When `streaming=False`:
     a. Set `stream: False` in litellm kwargs.
     b. Collect the full response from litellm (non-streaming).
     c. Emit a single `TextDelta` with the full content.
     d. Still handle tool calls from the non-streaming response.
  4. When `streaming=True`: current behavior.
- **Verification**: Set `ui.streaming: false`, verify text appears all at once. Set `true`, verify streaming chunks.

---

## Task 6: Create mcp_read_resource and mcp_get_prompt tools (G6)
- **Gap IDs**: R9-G6
- **Files**: `myagent/tools/mcp/client.py`, `myagent/tools/builtin/mcp_tools.py` (new), `myagent/tools/registry.py`, `myagent/cli/main.py`
- **Approach**:
  1. Add `read_resource(uri: str)` method to `MCPClient` -- sends `resources/read` request.
  2. Add `get_prompt(name: str, arguments: dict)` method to `MCPClient` -- sends `prompts/get` request.
  3. Create `myagent/tools/builtin/mcp_tools.py` with two tool classes:
     a. `MCPReadResourceTool`: takes `uri` parameter, finds the right MCP client, calls `read_resource()`.
     b. `MCPGetPromptTool`: takes `name` and `arguments` parameters, finds the right MCP client, calls `get_prompt()`.
  4. Both tools need access to the MCP clients list. Pass them via `ToolContext` or store on `ToolRegistry`.
  5. Register both tools in `_register_builtin_tools()`.
- **Verification**: Set up an MCP server with resources/prompts, verify the tools appear in tool list and can be called.

---

## Task 7: Add /compact slash command (G7)
- **Gap IDs**: R9-G7
- **Files**: `myagent/cli/commands.py`
- **Approach**:
  1. Register `/compact` command in `CommandDispatcher._register_defaults()`.
  2. Implement `_cmd_compact()` that:
     a. Accesses the compression engine via `ctx.engine.compression`.
     b. Gets current conversation messages from the session.
     c. Calls `compression.compact(messages, estimated_usage)`.
     d. Reports the result (layers applied, messages before/after).
  3. The `/clear` message at 50% context usage should also mention `/compact` as an alternative.
- **Verification**: Type `/compact` in REPL, verify compression runs and reports results. Verify `/help` shows the command.

---

## Task 8: Fix config_set valid_keys -- derive from AppConfig schema (G8)
- **Gap IDs**: R9-G8
- **Files**: `myagent/tools/builtin/config_tools.py`
- **Approach**:
  1. Add a classmethod `_derive_valid_keys()` that introspects the `AppConfig` dataclass hierarchy to produce the full set of dot-separated config paths.
  2. Replace the hardcoded `valid_keys` set with the derived one.
  3. Also update `_validate_value()` to derive type coercion rules from field annotations.
  4. This ensures adding a new config field automatically makes it settable via `config_set`.
  5. The missing keys (`session.sessions_dir`, `model.provider`, `model.model`, `ui.status_bar_items`) will automatically be included.
- **Verification**: Call `config_set` with the previously-missing keys and verify they work. Call with a truly invalid key and verify it's rejected.

---

## Task 9: Add semantic dedup to memory_write (G9)
- **Gap IDs**: R9-G9
- **Files**: `myagent/memory/store.py`
- **Approach**:
  1. In `MemoryStore.write()`, after name-based dedup, perform content similarity check.
  2. Use the existing embedding-based recall from `myagent/memory/recall.py` to find similar memories.
  3. Compute cosine similarity between the new content and existing memory descriptions/contents.
  4. If similarity exceeds a threshold (e.g., 0.80), treat as update rather than create.
  5. Fall back to name-only dedup if embedding model is unavailable.
  6. Add a `_find_by_similarity(content: str)` method that uses the recall module.
- **Verification**: Create a memory, then write a semantically similar memory with a different name. Verify the second write updates rather than creates a duplicate.

---

## Task 10: Add LLM-based contradiction detection to dream engine (G10)
- **Gap IDs**: R9-G10
- **Files**: `myagent/memory/dream.py`
- **Approach**:
  This is addressed by Task 3 (refactoring dream to spawn sub-agent). The sub-agent uses LLM reasoning for semantic contradiction detection instead of hardcoded word pairs. The keyword-based `_detect_contradictions()` is replaced by the sub-agent's LLM analysis.
- **Verification**: Covered by Task 3 verification.
