# MyAgentCLI Chat Window UI Design

> Date: 2026-07-05 | Status: Implemented

## Goal

MyAgentCLI should start into a fixed, chat-like terminal UI similar to opencode:

- conversation history stays in a fixed scrollable pane;
- the input box is always visible at the bottom;
- the existing Agent Inspector Pane remains visible on the right on wide terminals;
- narrow terminals collapse the Inspector into the existing rail;
- streaming output updates inside the conversation area instead of pushing the input prompt away.

This is a CLI/TUI interaction change only. It does not change the ReAct loop, model provider, tool execution, sub-agent scheduling, permissions, session persistence, or memory semantics.

## Current State

The current REPL uses `prompt_toolkit.PromptSession.prompt_async("myagent> ")` for each input turn. `AgentLayoutController` owns Rich `Layout` and `Live` only while engine output is streaming, then stops before returning to prompt_toolkit. This is stable for the fixed Inspector Pane, but it still behaves like a traditional terminal prompt: the input prompt moves down the terminal scrollback as output is printed.

The requested behavior needs one persistent UI owner for the whole interactive session, not a transient Live display per engine turn.

## Recommended Layout

Wide terminal layout:

```text
┌─ MyAgentCLI · project · model/mode ───────────────────┬─ Agent Inspector ─┐
│ Conversation transcript                                │ Session          │
│                                                        │ Tokens / Context │
│ User/assistant/tool messages                           │ Goal             │
│ Mouse wheel and keybindings scroll this pane           │ Subagents        │
│                                                        │ Tools / Health   │
├────────────────────────────────────────────────────────┴──────────────────┤
│ > Persistent multiline input box                                          │
└───────────────────────────────────────────────────────────────────────────┘
```

Narrow terminal layout:

```text
┌─ MyAgentCLI ───────────────────────────────────────────┬─ AI ─┐
│ Conversation transcript                                │ 42k  │
│                                                        │ 31%  │
│                                                        │ 2    │
├────────────────────────────────────────────────────────┴──────┤
│ > Persistent input box                                         │
└────────────────────────────────────────────────────────────────┘
```

The wide layout is the default target. The rail layout is selected below `ui.status_pane.collapse_below_columns`. `F2` keeps its current meaning: toggle between expanded Inspector and rail when there is enough room; on narrow terminals it stays rail-first and never covers the input.

## Architecture

Add a new chat-window UI layer that owns the full-screen terminal session:

- `ChatWindowController`: owns the full-screen application layout, transcript viewport, bottom input container, and status pane region.
- `TranscriptBuffer`: stores renderable conversation entries separately from raw transcript persistence. It keeps enough in-memory renderables for local scrollback and can reconstruct from session history when resuming.
- `InputController`: owns prompt_toolkit input editing, completion, history, multiline behavior, submit/cancel bindings, and focus state.
- `AgentLayoutController`: remains responsible for rendering the Agent Inspector Pane and output renderables during compatibility mode, but chat-window mode uses shared components rather than starting a second Live.

The preferred implementation is a prompt_toolkit full-screen `Application` with Rich renderables embedded through the existing terminal rendering helpers where practical. This avoids fighting prompt_toolkit for input focus and avoids running Rich `Live` while prompt_toolkit owns the screen. Rich remains the renderer for message bodies and Inspector content; prompt_toolkit owns the fixed input and scrollable viewport.

## Data Flow

1. Startup builds `RuntimeStatusModel`, `AgentInspectorPane`, `TranscriptBuffer`, and `ChatWindowController`.
2. Existing session resume loads recent transcript messages into `TranscriptBuffer`.
3. User edits in the bottom input box.
4. On submit, the message is passed into the chat submission pipeline. If another agent turn is already running, the message appears in a visible `Queue |` block instead of being appended to the transcript immediately.
5. When the queued submission acquires the REPL chat-submission lock, it is moved from the visible queue into the transcript as the next `You |` turn and then passed to `REPLEngine.process_input()`.
6. Engine events update two sinks:
   - visible conversation renderables go to `TranscriptBuffer`;
   - `StatusUpdate` and lifecycle events go to `RuntimeStatusModel`.
7. The chat window invalidates only the affected regions: transcript viewport, input box, queue, and Inspector.
8. On shutdown, session persistence remains the existing `SessionManager` path.

Status-only events must never appear as conversation messages and must not be persisted as transcript messages.

## Input Behavior

Default bindings:

- `Enter`: submit when the input is a single complete command/message.
- `Esc+Enter` or `Alt+Enter`: insert newline in the input box.
- `Tab`: keep existing completions for slash commands, skills, and paths.
- `F2`: toggle Inspector full/rail state.
- `Ctrl+C`: interrupt the running agent when a run is active; clear current input or ask exit confirmation when idle, preserving existing semantics.
- `Ctrl+D`: exit when input is empty.
- `PageUp/PageDown`, mouse wheel over the conversation body: scroll conversation history.
- `Home/End` inside input keeps normal input editing semantics; scroll-specific bindings should not steal ordinary text editing keys.

The input box defaults to one visual line and can grow up to six visual lines before scrolling internally. Very long input should scroll inside the input box, not resize the bottom bar enough to hide the transcript.

## Scrolling Behavior

The conversation pane is an independent viewport:

- mouse wheel scrolls transcript history, not the whole terminal;
- new output auto-follows the bottom only when the user is already near the bottom;
- if the user has scrolled up, new output should not yank the viewport down;
- a small unread/new-output marker can appear when output arrives while scrolled away from bottom;
- all transcript text is wrapped to the available terminal cell width before clipping, so long CJK/emoji/ASCII lines stay inside pane boundaries;
- role labels are aligned (`You |`, `Agent |`, `System |`, `Tool |`) and role changes may include a blank visual separator for readability;
- assistant replies may receive display-only Markdown-ish cleanup for common compact headings, bold markers, horizontal separators, ordered and unordered lists, compact term lists, and simple tables; this improves readability without changing persisted transcript text;
- submissions made while an agent turn is active remain in a visible queue until their own turn starts, preventing queued questions from visually pairing with the wrong answer; immediate control commands such as `/goal <text>` do not queue and do not append a user turn;
- clearing the screen clears the transcript viewport for the current UI session while preserving session persistence behavior already defined by commands.

## Configuration

Add `UIConfig.chat_window`:

```yaml
ui:
  chat_window:
    enabled: true
    input_position: bottom
    scrollback_lines: 2000
    input_min_lines: 1
    input_max_lines: 6
    follow_output: auto
    mouse_support: true
```

Rules:

- `ui.chat_window.enabled: true` is the default for interactive sessions.
- `ui.chat_window.mouse_support: true` is the default so mouse-wheel scrolling works inside the conversation pane. Users can set it to `false` when native terminal selection/copy behavior is more important. On Windows, the TUI uses native Win32 mouse input and resets VT/SGR mouse reporting so mouse reports do not leak into the input buffer; on non-Windows VT terminals it enables only the terminal mouse modes needed for wheel reporting and avoids drag/any-motion tracking.
- Non-interactive one-shot commands such as `--help`, `--list-sessions`, and export commands never start the chat window.
- If full-screen UI startup fails, MyAgentCLI falls back to the current REPL + Agent Inspector behavior and logs a structured error.
- `ui.status_pane` continues to configure the right-side Inspector and rail behavior.

## Compatibility

The current REPL remains available as fallback and for unsupported terminals. Existing public behavior remains:

- slash commands keep their names and output;
- autocomplete remains available;
- `StatusBar` compatibility alias remains;
- legacy config keys `ui.show_status_bar` and `ui.status_bar_items` continue to map to `ui.status_pane`;
- transcript JSON and Markdown formats do not change.

The chat window should be feature-compatible before it becomes the only path. If needed during rollout, `ui.chat_window.enabled: false` can force the current prompt-style REPL.

## Error Handling

The full-screen chat UI must fail soft:

- startup failure logs `category="error"`, `component="agent"`, and a context such as `cli_chat_window_start`;
- render failure logs structured error metadata and falls back to plain transcript output if possible;
- prompt_toolkit input failure falls back to existing simple input behavior;
- a rendering failure must not cancel an active ReAct loop or corrupt session persistence.

## Testing

Required tests:

- config schema and loader tests for `ui.chat_window` defaults and overrides;
- startup wiring tests proving one-shot commands skip chat window and interactive REPL uses it by default;
- layout tests for wide, narrow, and minimum terminal sizes;
- layout tests proving pane boundaries are drawn and long transcript lines wrap within terminal cell width;
- formatting tests proving compact assistant Markdown-ish text is split into readable headings, list lines, term lists, and table rows while existing code fences remain intact;
- input tests for Enter submit, multiline insert, Tab completion, `F2`, `Ctrl+C`, and `Ctrl+D`;
- scroll tests for mouse wheel over the conversation body, PageUp/PageDown, auto-follow at bottom, and no auto-yank when scrolled up;
- queue tests proving submissions made during an active agent turn are visible as pending and move into the transcript only when processing starts, while `/goal <text>` applies immediately as a system/status update;
- engine integration tests proving streamed text, Rich panels, tool events, errors, and `StatusUpdate` route to the correct sinks;
- fallback tests for full-screen startup/render failure;
- regression tests proving no second Rich `Live` owner appears in chat-window mode.

Manual smoke:

- run `myagent` in a normal terminal and verify the input stays fixed while long output streams;
- run at a narrow terminal width and verify rail mode;
- run `myagent --list-sessions` and `myagent --help` and verify they stay non-full-screen.

## Open Decisions

The approved direction is:

- A/B hybrid from the mockup: wide full chat + right Inspector; narrow chat + rail.
- The feature should be the default interactive startup UI.
- Existing REPL behavior remains as fallback through `ui.chat_window.enabled: false`.

No further product decision is needed before writing the implementation plan.
