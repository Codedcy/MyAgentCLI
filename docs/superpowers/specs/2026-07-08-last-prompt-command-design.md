# Last Prompt Command Design

## Purpose

Add an interactive slash command that lets the user inspect the most recent full
LLM prompt sent by the main Agent. This is a debugging and transparency feature:
when the model behaves unexpectedly, the user can see exactly what context,
system prompt, messages, and tool definitions were available to that turn.

## Command Surface

- `/prompt` shows the last captured prompt in a readable text form.
- `/prompt raw` shows the same data as JSON for copying and external inspection.
- If no LLM request has been captured in the current process, the command returns
  `No LLM prompt captured yet.`

The command is an immediate control command. If entered while an Agent turn is
running, it should not enter the visible chat queue and should not create a new
`You |` transcript turn.

## Captured Data

The feature stores only the latest main-Agent LLM request in memory:

- model name actually used for the request;
- thinking mode;
- full `messages` list after the system prompt has been inserted and after any
  context compression for that iteration;
- tool definitions passed to the model;
- capture timestamp;
- estimated prompt token count if already available.

This is intentionally process-local and last-request-only. It does not persist
prompt content to session transcripts or prompt log files by default. Existing
`logging.llm_prompts` remains the opt-in disk logging path for full prompt
archives.

## Data Flow

The capture point should be immediately before the LLM provider sends a request
to LiteLLM, after the Agent has prepared the exact `messages` and tools for the
iteration. Capturing at this boundary avoids showing a partially built context
or a pre-compression approximation.

The command dispatcher reads the captured prompt through the active engine or
LLM provider. The CLI renderer then displays the result as a normal system
message in the chat window or console.

## Formatting

Readable `/prompt` output should be structured but compact:

- header with model, thinking mode, timestamp, message count, tool count, and
  estimated tokens;
- each message rendered as `[#] role` followed by content;
- tools rendered as names with their full JSON schema; large output is handled by
  the existing chat viewport wrapping and scrolling.

`/prompt raw` should return deterministic JSON with stable keys so it is easy to
diff during debugging.

## Error Handling

- Missing captured prompt: return a successful command result with a clear
  message, not an exception.
- Malformed or unserializable tool definitions: fall back to `str(value)` for
  the affected field while logging a structured error.
- Very large prompts should still be available; the existing chat viewport and
  transcript wrapping are responsible for display and scrolling.

## Tests

Add tests for:

- the LLM capture object records the exact messages and tools passed to the
  provider;
- `/prompt` reports no captured prompt before the first LLM call;
- `/prompt` renders the captured prompt in readable form;
- `/prompt raw` returns valid JSON;
- `/prompt` bypasses the pending chat queue like `/subagents` and `/goal`;
- help and README list the new command.

## Out Of Scope

- Prompt history beyond the most recent request.
- Persisting prompt snapshots to disk from the command itself.
- Redacting secrets in prompt content. This command is explicitly a debugging
  view of the actual prompt available to the model.
