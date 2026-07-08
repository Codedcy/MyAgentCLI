# Last Prompt Command Implementation Plan

## Goal

Implement `/prompt` so the user can inspect the most recent full prompt sent by
the main Agent to the LLM provider. `/prompt` renders a readable view and
`/prompt raw` renders deterministic JSON. The command must run immediately from
the chat window, without entering the pending submission queue.

## Design References

- `docs/superpowers/specs/2026-07-08-last-prompt-command-design.md`
- `myagent/agent/engine.py`
- `myagent/cli/commands.py`
- `myagent/cli/control_commands.py`
- `myagent/cli/repl.py`

## Task 1: Prompt Capture Model

Create a small prompt-capture helper under `myagent/agent/` with:

- `LastPromptCapture`
- `LastPromptCapture.capture(...)`
- `to_dict()`
- `to_json()`
- `to_text()`

The capture object must deep-copy messages and tools, record timestamp, model,
thinking mode, message count, tool count, and optional estimated token count.
Serialization must tolerate non-JSON-native values by falling back to string
conversion.

Tests:

- readable text includes metadata, messages, and tool schema details;
- raw JSON has stable top-level keys and preserves message/tool content.

## Task 2: Main Agent Capture Point

Update `AgentEngine` to store the latest main-Agent prompt immediately before
each `self.llm.complete(...)` call in `_react_loop`, after system prompt
insertion, context compression, and sub-agent completion message drainage.

Expose:

- `get_last_prompt_capture()`
- `last_prompt_text()`
- `last_prompt_json()`

Tests:

- a ReAct turn captures exactly the messages and tools passed to `llm.complete`;
- the captured prompt is not mutated if later message/tool lists change.

## Task 3: Slash Command Surface

Add `/prompt` to `CommandDispatcher`.

Behavior:

- `/prompt` returns readable text;
- `/prompt raw` returns JSON;
- no captured prompt returns `No LLM prompt captured yet.`;
- invalid arguments return usage with `success=False`.

Tests:

- command output for missing, readable, raw, and invalid argument cases.

## Task 4: Chat Integration

Make `/prompt` a control command that bypasses the pending chat queue and does
not append a user turn when submitted while the Agent is running. Add it to slash
completion and help text.

Tests:

- queued submission lock does not block `/prompt`;
- slash completion includes `prompt`;
- `/help` lists the command.

## Task 5: Documentation And Verification

Update `README.md` command documentation and test count after verification.

Verification commands:

- targeted pytest for new and touched tests;
- full `pytest tests/ -q`;
- targeted `ruff check`;
- `git diff --check`.

Commit the implementation after all verification passes.
