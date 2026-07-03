# MyAgentCLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a personal AI Agent CLI assistant using DeepSeek V4 Pro, with ReAct loop, sub-agent orchestration, MCP tools, memory system, skills, and Rich+prompt_toolkit TUI.

**Architecture:** Four-layer asynchronous monolith — CLI (Rich + prompt_toolkit), Application (ReAct engine + goal tracker + session manager), Service (tool registry + sub-agent pool + memory store), Infrastructure (LiteLLM + MCP protocol). Single ReAct loop for all interactions; sub-agents via spawn_subagent tool; model autonomously judges complexity and orchestration strategy.

**Tech Stack:** Python 3.12+, LiteLLM, Rich, prompt_toolkit, YAML config, JSON+Markdown persistence, pipx/pip distribution, mcp Python SDK.

## Global Constraints

- Python 3.12+ (match pyproject.toml `requires-python`)
- DeepSeek V4 Pro as primary model via LiteLLM (1M context window, Think High/Think Max/Non-think modes)
- All tools implement unified `Tool` protocol (name, description, parameters as JSON Schema, async execute)
- Context compression: auto-trigger at 75%, guide toward 30% (**non-binding target** — accept actual result if unreachable), hard truncation at 90%
- Permission system: 4 levels (0=read, 1=write, 2=exec, 3=network-write); default level 0 auto-allow, rest ask
- Sub-agents: max concurrent = min(16, CPU cores - 2), global cap 1000 per session
- Skills: natural-language instruction files (SKILL.md), not code plugins; three-tier priority (built-in < user < project)
- Memory: file-level frontmatter Markdown, one fact per file, MEMORY.md index
- Config: 7-level priority merge (CLI args → runtime → project config → project AGENT.md → user config → user AGENT.md → defaults)
- Sessions: organized as `<project-name>/<project-hash>/<session-id>/` with transcript.json + transcript.md
- No `/stop`, `/insert` commands — all interaction via natural language intent interpretation. Model outputs structured intent signals: `STOP`, `CORRECT`, `INSERT`, `CONTINUE`. Engine dispatches accordingly.
- No complexity router — model autonomously decides orchestration strategy
- Permission timeout: no timeout (wait forever). Decision timeout (agent asks user a question): 120s then auto-decide.
- Agent can ask user clarifying questions mid-execution → REPL displays question with 120s countdown → user answers or timeout auto-decides
- Session end: prompt to persist runtime permission changes to config; summarize memories written/updated during session
- Distribution: pipx / pip (PyPI)

---

## File Structure

```
myagent/
├── __init__.py
├── cli/                        # CLI layer
│   ├── __init__.py
│   ├── main.py                 # Entry point, arg parsing
│   ├── repl.py                 # prompt_toolkit REPL engine
│   ├── status.py               # Rich Live status bar
│   ├── commands.py             # Slash command dispatch (/mode, /goal, /skills, /dream, /clear, /history, /exit)
│   └── renderer.py             # Stream event → Rich renderable
├── agent/                      # Application layer
│   ├── __init__.py
│   ├── engine.py               # ReAct loop core
│   ├── goal.py                 # Goal tracker overlay
│   ├── session.py              # Session persistence, resume, listing
│   └── project.py              # Project environment detection (git, type, structure)
├── tools/                      # Service layer — tools
│   ├── __init__.py
│   ├── base.py                 # Tool Protocol, ToolResult, ToolContext
│   ├── registry.py             # ToolRegistry (unified built-in + MCP)
│   ├── builtin/
│   │   ├── __init__.py
│   │   ├── file_tools.py       # read, write, edit, glob
│   │   ├── search_tools.py     # grep (ripgrep)
│   │   ├── exec_tools.py       # bash
│   │   ├── agent_tools.py      # spawn_subagent, send_message
│   │   ├── session_tools.py    # task_create, task_update
│   │   ├── memory_tools.py     # memory_write
│   │   └── web_tools.py        # web_fetch, web_search
│   └── mcp/
│       ├── __init__.py
│       ├── client.py           # MCP subprocess lifecycle (stdio)
│       └── adapter.py          # MCP tool → Tool protocol adapter
├── subagent/                   # Service layer — sub-agents
│   ├── __init__.py
│   ├── pool.py                 # SubAgentPool (concurrency, lifecycle)
│   └── worker.py               # SubAgent worker (runs ReAct loop in isolation)
├── context/                    # Service layer — context
│   ├── __init__.py
│   ├── builder.py              # Six-layer context assembler
│   ├── compression.py          # Four-layer progressive compression
│   └── persistence.py          # Transcript/tool-call file I/O
├── memory/                     # Service layer — memory
│   ├── __init__.py
│   ├── store.py                # File CRUD + MEMORY.md index
│   ├── recall.py               # Semantic matching for L4 loading
│   └── dream.py                # Dream background loop
├── skills/                     # Service layer — skills
│   ├── __init__.py
│   ├── registry.py             # Skill discovery, multi-level scan, priority merge
│   └── loader.py               # SKILL.md parser, resource enumeration
├── config/                     # Infrastructure — config
│   ├── __init__.py
│   ├── loader.py               # 7-level YAML loader + deep merge
│   └── schema.py               # TypedDict/dataclass config model
├── permissions/                # Infrastructure — permissions
│   ├── __init__.py
│   └── controller.py           # Level check, allow/deny matching, confirmation UI
└── llm/                        # Infrastructure — LLM
    ├── __init__.py
    └── provider.py             # LiteLLM async wrapper, streaming, thinking mode
├── logging/                    # Infrastructure — logging
│   ├── __init__.py
│   ├── logger.py               # LogManager: setup, rotation, cleanup
│   ├── formatter.py            # JsonLineFormatter
│   └── context.py              # LogContext: session/project binding via contextvars
```

---

## Phase 1: Project Skeleton & Configuration

### Task 1: Project scaffold and dependency setup

**Files:**
- Create: `pyproject.toml`
- Create: `myagent/__init__.py`
- Create: `myagent/cli/__init__.py`
- Create: `myagent/agent/__init__.py`
- Create: `myagent/tools/__init__.py`
- Create: `myagent/tools/builtin/__init__.py`
- Create: `myagent/tools/mcp/__init__.py`
- Create: `myagent/subagent/__init__.py`
- Create: `myagent/context/__init__.py`
- Create: `myagent/memory/__init__.py`
- Create: `myagent/skills/__init__.py`
- Create: `myagent/config/__init__.py`
- Create: `myagent/permissions/__init__.py`
- Create: `myagent/llm/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Produces: Empty package structure, pyproject.toml with all dependencies

- [ ] **Step 1: Write pyproject.toml**

Key dependencies: `litellm>=1.50.0`, `rich>=13.0.0`, `prompt-toolkit>=3.0.0`, `pyyaml>=6.0`, `mcp>=1.0.0`, `pydantic>=2.0.0`. Dev: `pytest>=8.0.0`, `pytest-asyncio>=0.24.0`, `ruff>=0.4.0`. Entry point: `myagent = "myagent.cli.main:main"`. Python >= 3.12.

- [ ] **Step 2: Create all `__init__.py` files**

All empty. Package namespace established.

- [ ] **Step 3: Create tests/conftest.py**

Shared fixtures: `tmp_project_dir` (temp dir with .myagent/ skeleton), `tmp_home_dir` (temp ~/.myagent/ with default config).

- [ ] **Step 4: Install dev dependencies and verify**

Run: `pip install -e ".[dev]"`
Expected: All packages install without error.

- [ ] **Step 5: Verify package is importable**

Run: `python -c "import myagent; print(myagent.__name__)"`
Expected: `myagent`

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: project scaffold with pyproject.toml and package structure"
```

---

### Task 2: Config schema and loader

**Files:**
- Create: `myagent/config/schema.py`
- Create: `myagent/config/loader.py`
- Create: `myagent/config/__init__.py`
- Test: `tests/config/test_schema.py`
- Test: `tests/config/test_loader.py`

**Interfaces:**
- Produces:
  - `AppConfig` dataclass — all config fields with defaults
  - `ModelConfig`, `ContextConfig`, `PermissionsConfig`, `SubagentsConfig`, `DreamConfig`, `ToolsConfig`, `UIConfig`, `SessionConfig` nested dataclasses
  - `ConfigLoader.load()` → `AppConfig` — 7-level merge
  - `ConfigLoader.from_cli_args(args: dict) -> AppConfig` — apply CLI overrides

**Key data structures:**

```python
# schema.py — all dataclasses with defaults matching design doc §九

@dataclass
class ModelConfig:
    provider: str = "deepseek"
    model: str = "deepseek-v4-pro"
    thinking: Literal["Think High", "Think Max", "Non-think"] = "Think High"
    fallback_models: list[str] = field(default_factory=list)

@dataclass
class CompressionConfig:
    primary_threshold: float = 0.75
    target_after: float = 0.30
    hard_limit: float = 0.90
    minimum_messages: int = 10
    minimum_savings: float = 0.10

@dataclass
class PermissionsConfig:
    default_mode: Literal["ask", "allow_all"] = "ask"
    auto_allow: AutoAllowConfig  # levels: list[int], paths: list[str], commands: list[str]
    auto_deny: AutoDenyConfig    # paths: list[str], commands: list[str]

@dataclass
class AppConfig:
    model: ModelConfig
    context: ContextConfig
    permissions: PermissionsConfig
    subagents: SubagentsConfig
    dream: DreamConfig
    tools: ToolsConfig
    ui: UIConfig
    session: SessionConfig
    logging: LoggingConfig
```

**Additional config types:**

```python
@dataclass
class LoggingConfig:
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    dir: str = "~/.myagent/logs/"
    format: Literal["jsonl", "text", "both"] = "jsonl"
    max_size_mb: int = 100
    retention_days: int = 30
    llm_prompts: bool = False
```

**Loader merge order (low→high):**
1. Hardcoded defaults → 2. User AGENT.md (~/.myagent/AGENT.md) → 3. User config (~/.myagent/config.yaml) → 4. Project AGENT.md (.myagent/AGENT.md) → 5. Project config (.myagent/config.yaml) → 6. Runtime overrides (in-memory dict) → 7. CLI args

Merge is deep-merge for dicts, replace for scalars and lists. **List replacement (not append):** high-priority lists completely replace low-priority ones. Users who want to extend a default list must copy the full default list and add to it. This design avoids ambiguity — if lists were merged, there's no consistent rule for dedup or ordering.

- [ ] **Step 1: Write failing tests for schema defaults**

Test that `AppConfig()` with no args produces all documented defaults (thinking="Think High", primary_threshold=0.75, etc.)

- [ ] **Step 2: Implement schema dataclasses**

All fields, defaults, and nested structures per design doc §九. Use `@dataclass` with `field(default_factory=...)` for mutable defaults.

- [ ] **Step 3: Run schema tests to verify defaults**

Run: `pytest tests/config/test_schema.py -v`
Expected: PASS

- [ ] **Step 4: Write failing tests for 7-level merge**

Test: user config overrides defaults, project config overrides user, CLI args override all. Test that AGENT.md values land in correct priority position.

- [ ] **Step 5: Implement ConfigLoader**

`ConfigLoader` class with:
- `__init__(project_dir: Path, user_home: Path = DEFAULT_USER_HOME)`
- `async load(cli_args: dict | None = None) -> AppConfig` — walk 7 layers, deep-merge
- `apply_runtime_override(key: str, value: Any)` — for mid-conversation adjustments
- `_load_yaml(path: Path) -> dict` — parse YAML file, return {} if missing
- `_load_agent_md(path: Path) -> dict` — extract config-relevant directives from AGENT.md
- `_deep_merge(base: dict, override: dict) -> dict` — recursive dict merge

- [ ] **Step 6: Run loader tests**

Run: `pytest tests/config/test_loader.py -v`
Expected: PASS

- [ ] **Step 7: Update config/__init__.py exports**

Export `AppConfig`, `ConfigLoader`, and all nested config types.

- [ ] **Step 8: Commit**

```bash
git add tests/config/ myagent/config/
git commit -m "feat: config schema and 7-level loader"
```

---

### Task 2b: Project environment detection

**Files:**
- Create: `myagent/agent/project.py`
- Test: `tests/agent/test_project.py`

**Interfaces:**
- Produces:
  - `ProjectContext` dataclass — detected project metadata
  - `ProjectDetector` class
  - `async detect(project_dir: Path) -> ProjectContext`

**Key data structure:**

```python
@dataclass
class ProjectContext:
    # Git
    is_git_repo: bool
    git_branch: str | None
    git_status: str | None          # e.g. "2 files modified"

    # Project type
    project_type: str                # "python" | "node" | "go" | "rust" | "unknown"
    package_manager: str | None      # "uv" | "pip" | "poetry" | "npm" | "pnpm" | "yarn"
    python_version: str | None       # "3.12"
    build_system: str | None         # "make" | "pyproject" | ...
    test_framework: str | None       # "pytest" | "unittest" | ...
    linter: str | None               # "ruff" | "flake8" | ...

    # Directory structure
    structure_summary: str           # e.g. "src/ tests/ docs/"

    # AGENT.md / CLAUDE.md content (for L3 context injection)
    agent_md_content: str | None
```

**Detection logic:**
- Git: run `git status --porcelain`, `git branch --show-current`
- Project type: detect by file presence (pyproject.toml → python, package.json → node, go.mod → go, Cargo.toml → rust)
- Package manager: detect from lock files (uv.lock → uv, poetry.lock → poetry, package-lock.json → npm, pnpm-lock.yaml → pnpm)
- Python version: `python --version` or read `.python-version`
- Build system: Makefile presence, pyproject.toml [build-system]
- Test framework: pytest config presence, jest config, etc.
- Linter: ruff.toml / .flake8 / .eslintrc presence
- Structure: scan top-level directories (depth 1), produce compact summary
- AGENT.md: read `.myagent/AGENT.md`, `AGENT.md`, or `CLAUDE.md` from project root

- [ ] **Step 1: Write failing tests**

Test detection on temp dirs with known file structures (pyproject.toml + git repo, node project, empty dir).

- [ ] **Step 2: Implement ProjectDetector**

File-existence checks + subprocess calls for git/python version. All methods async. Graceful degradation — missing git → is_git_repo=False, no crash.

- [ ] **Step 3: Run tests**

Run: `pytest tests/agent/test_project.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add myagent/agent/project.py tests/agent/test_project.py
git commit -m "feat: project environment detection"
```

---

## Phase 2: Infrastructure Layer

### Task 3: LiteLLM provider wrapper

**Files:**
- Create: `myagent/llm/provider.py`
- Create: `myagent/llm/__init__.py`
- Test: `tests/llm/test_provider.py`

**Interfaces:**
- Consumes: `ModelConfig` (from Task 2)
- Produces:
  - `LLMProvider` class
  - `async complete(messages: list[dict], tools: list[dict] | None, thinking: str) -> AsyncIterator[LLMEvent]`
  - `LLMEvent` union: `TextDelta(content: str) | ThinkingDelta(content: str) | ToolCall(id: str, name: str, params: dict) | Done(stop_reason: str, usage: Usage)`
  - `Usage` dataclass: `prompt_tokens: int, completion_tokens: int, total_tokens: int`
  - `async token_count(messages: list[dict]) -> int` — estimate tokens via LiteLLM

**Key design:**
- Wraps `litellm.acompletion` with streaming
- Maps thinking mode to DeepSeek V4 Pro's `thinking` parameter:
  - `"Think High"` → `thinking={"type": "enabled"}` (default)
  - `"Think Max"` → `thinking={"type": "enabled", "budget_tokens": 32000}`
  - `"Non-think"` → `thinking={"type": "disabled"}`
- `LLMEvent` is a discriminated union — consumer pattern-matches on type
- **Thinking mode content separation:** DeepSeek returns `reasoning_content` (thinking chain) separately from `content` (visible text). Provider separates these into `ThinkingDelta` and `TextDelta` events respectively. The REPL renders `ThinkingDelta` collapsed/dimmed while `TextDelta` streams visibly.
- Token counting uses LiteLLM's `token_counter` or falls back to char/4 estimate
- Error wrapping: LiteLLM exceptions → `LLMError(code: str, message: str, retryable: bool)`
- **Retry strategy:** Built into `LLMProvider.complete()`, transparent to callers:
  - Transient errors (HTTP 429, 5xx, connection timeout) → exponential backoff, max 3 retries, base delay 2s, cap 30s
  - Fatal errors (HTTP 4xx except 429, auth failure) → no retry, raise immediately
  - Stream interruption → preserve received content, inject truncation notice, let user decide
  - Status bar updates during retries to show progress
  - 3 consecutive failures → surface to user with diagnostic info
  - Sub-agent LLM errors: silent retry, only report final failure to main agent

- [ ] **Step 1: Write failing tests**

Test: streaming text chunks produce TextDelta events. Test: tool call response produces ToolCall event. Test: done event includes usage stats. Test: thinking mode mapping.

- [ ] **Step 2: Implement LLMEvent types and Usage**

Dataclasses with Literal discriminators.

- [ ] **Step 3: Implement LLMProvider.complete()**

Async generator. Calls `litellm.acompletion`, iterates streaming response, yields typed events. Handles:
- `ModelConfig` → LiteLLM model string
- `thinking` param → DeepSeek `thinking` field
- Error wrapping (LiteLLM exceptions → `LLMError` with retry info)

- [ ] **Step 4: Implement LLMProvider.token_count()**

Use `litellm.token_counter` if available; fallback to `len(json.dumps(messages)) // 4`.

- [ ] **Step 5: Run tests**

Run: `pytest tests/llm/test_provider.py -v`
Expected: PASS (use mock for litellm to avoid API calls)

- [ ] **Step 6: Commit**

---

### Task 4: MCP protocol client

**Files:**
- Create: `myagent/tools/mcp/client.py`
- Create: `myagent/tools/mcp/adapter.py`
- Create: `myagent/tools/mcp/__init__.py`
- Test: `tests/tools/mcp/test_client.py`
- Test: `tests/tools/mcp/test_adapter.py`

**Interfaces:**
- Consumes: `AppConfig` (for MCP server list from mcp.json)
- Produces:
  - `MCPClient` — manages one MCP server subprocess
  - `async start() → None` — spawn subprocess, initialize handshake
  - `async list_tools() → list[RawToolDef]` — call tools/list
  - `async call_tool(name: str, params: dict) → dict` — call tools/call
  - `async list_resources() → list[dict]` — call resources/list
  - `async shutdown() → None` — terminate subprocess
  - `MCPToolAdapter` — wraps MCP tool as `Tool` protocol
  - `RawToolDef`: `name, description, inputSchema` (from MCP JSON-RPC)

**Key design:**
- Subprocess communication via stdio (JSON-RPC over stdin/stdout)
- Uses `asyncio.create_subprocess_exec`
- MCP lifecycle: initialize → initialized → tools/list → (runtime calls) → shutdown
- Adapter translates MCP's `inputSchema` to OpenAI function-calling `parameters` format
- SSE transport: deferred to v1.1+. Initial implementation focuses on stdio only. The transport layer abstracts subprocess communication behind a `Transport` protocol so SSE can be added without changing MCPClient's public interface.

**MCP server configuration (mcp.json):**

Project-level: `.myagent/mcp.json`, user-level: `~/.myagent/mcp.json`. Format:
```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@anthropic/mcp-filesystem", "."],
      "env": {}
    }
  }
}
```
MCP client reads both files, merges by server name (project overrides user). Each entry is spawned as a subprocess. On startup, iterate all servers, initialize, discover tools, register adapters in ToolRegistry.

- [ ] **Step 1: Write failing tests for MCPClient**

Mock subprocess with controlled stdin/stdout. Test handshake sequence, tools/list, tools/call, error handling, shutdown.

- [ ] **Step 2: Implement MCPClient**

Async context manager pattern. JSON-RPC message framing with `Content-Length` header. Request/response correlation via `id` field. Stdio transport.

- [ ] **Step 3: Write failing tests for MCPToolAdapter**

Test that MCP tool schema is correctly mapped to OpenAI function-calling format. Test execute delegates to MCPClient.call_tool.

- [ ] **Step 4: Implement MCPToolAdapter**

Implements `Tool` protocol. `execute()` calls `MCPClient.call_tool()`. Schema translation: `inputSchema` → `parameters` dict.

- [ ] **Step 5: Run all MCP tests**

Run: `pytest tests/tools/mcp/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

---

### Task 4b: Logging system

**Files:**
- Create: `myagent/logging/__init__.py`
- Create: `myagent/logging/logger.py`
- Create: `myagent/logging/formatter.py`
- Create: `myagent/logging/context.py`
- Test: `tests/logging/test_logger.py`
- Test: `tests/logging/test_formatter.py`
- Test: `tests/logging/test_context.py`

**Interfaces:**
- Consumes: `LoggingConfig` (from Task 2)
- Produces:
  - `LogManager` class
  - `setup(config: LoggingConfig, session_id: str | None) -> None` — initialize logging tree, create handlers, start queue listener. Called once at startup.
  - `shutdown() -> None` — flush queue, close handlers. Called at exit.
  - `get_logger(name: str) -> logging.Logger` — convenience wrapper, equivalent to `logging.getLogger(f"myagent.{name}")`
  - `JsonLineFormatter(logging.Formatter)` — formats log records as single-line JSON with all standard + custom fields
  - `LogContext` class — thread-safe context binding via `contextvars`
  - `set_context(session_id: str, project_name: str) -> None` — bind session/project to current async context
  - `clear_context() -> None` — unbind

**Key design:**

```
LogManager.setup()
  ├── Creates ~/.myagent/logs/ directory
  ├── Creates root logger "myagent" with level from config
  ├── Attaches QueueHandler → QueueListener → file handlers
  │     ├── TimedRotatingFileHandler (daily, jsonl format, JsonLineFormatter)
  │     └── (optional) StreamHandler for text format if format=both
  ├── Starts QueueListener in background thread
  ├── Registers atexit hook for clean shutdown
  └── Cleans up logs older than retention_days
```

- **Async-safe**: All `logging.info()` etc. calls enqueue to `QueueHandler`. A single `QueueListener` thread drains the queue and writes to files. No I/O on caller's thread.
- **JSON Lines format**: Each log line is a standalone JSON object. Fields: `timestamp` (ISO 8601), `level`, `category`, `session_id`, `project`, `message`, plus category-specific fields.
- **LogContext**: Uses `contextvars.ContextVar` to bind `session_id` and `project_name`. The `JsonLineFormatter` reads these from the context var at format time — callers never pass them explicitly. Works correctly under `asyncio` (each task has its own context).
- **Log category constants**: `LOG_SYSTEM = "system"`, `LOG_LLM = "llm"`, `LOG_TOOL = "tool"`, `LOG_AGENT = "agent"`, `LOG_SUBAGENT = "subagent"`, `LOG_ERROR = "error"`. Passed as `extra={"category": LOG_LLM, ...}` to `logger.info()`.
- **LLM prompt deep logging**: When `config.llm_prompts=True` and level is DEBUG, full prompts are written to `.prompts/<timestamp>-<call_id>-request.json` and `.prompts/<timestamp>-<call_id>-response.json` (separate from main log stream). This is handled by `LLMProvider`, not the logging module itself.
- **Integration points**: Every module calls `get_logger(__name__)` at module level. Key log points:
  - `LLMProvider.complete()`: INFO request + INFO response (metadata only); DEBUG full prompt if enabled
  - `AgentEngine.run()`: INFO each ReAct iteration, WARNING on compression trigger
  - Tool `execute()`: INFO with tool_name, params_summary, duration_ms, permission_result
  - `SubAgentPool.spawn()`: INFO subagent lifecycle events
  - `MCPClient`: INFO connection lifecycle, ERROR on disconnect
  - All `except` blocks: ERROR with exception_type + traceback + context

**Log record schema (category-specific extras):**

```python
# LLM request
logger.info("LLM request", extra={
    "category": "llm", "event": "request",
    "model": "deepseek-v4-pro", "thinking_mode": "Think High",
    "messages_count": 34, "estimated_tokens": 124000,
    "tools_count": 14, "stream": True,
})

# LLM response
logger.info("LLM response", extra={
    "category": "llm", "event": "response",
    "model": "deepseek-v4-pro",
    "latency_ms": 2340, "prompt_tokens": 121500,
    "completion_tokens": 3400, "total_tokens": 124900,
    "tool_calls_count": 2, "retry_count": 0,
})

# Tool execution
logger.info("Tool executed: read", extra={
    "category": "tool",
    "tool_name": "read", "params_summary": "file_path=src/main.py",
    "permission_result": "allow", "duration_ms": 12,
    "result_size_chars": 4500, "error": None,
})

# Sub-agent lifecycle
logger.info("Sub-agent spawned", extra={
    "category": "subagent",
    "subagent_id": "sub-001", "event": "spawned",
    "prompt_summary": "审查 src/auth 目录",
})

# Error
logger.error("LLM API call failed", extra={
    "category": "error",
    "exception_type": "RateLimitError",
    "component": "llm",
    "context": "ReAct loop iteration 5, tool call pending",
    "traceback": "...",
})
```

- [ ] **Step 1: Write failing tests for JsonLineFormatter**

Test: formatter outputs valid single-line JSON. Test: all standard fields present (timestamp, level, logger name, message). Test: custom `extra` fields included. Test: missing optional fields omitted.

- [ ] **Step 2: Implement JsonLineFormatter**

Extend `logging.Formatter`. `format(record)` → `json.dumps(log_dict)`. Read `contextvars` for session_id/project. Sanitize: truncate long strings, escape special chars.

- [ ] **Step 3: Write failing tests for LogContext**

Test: `set_context()` makes session_id available in context var. Test: two concurrent asyncio tasks get correct isolated context. Test: `clear_context()` removes binding.

- [ ] **Step 4: Implement LogContext**

Two `ContextVar` instances: `_session_id` and `_project_name`. `set_context()` / `clear_context()` as module-level functions. `get_context() -> dict` for formatter use.

- [ ] **Step 5: Write failing tests for LogManager**

Test: `setup()` creates log directory and file. Test: log messages appear in file as valid JSONL. Test: daily rotation creates new file. Test: `shutdown()` flushes and closes. Test: retention cleanup deletes old files.

- [ ] **Step 6: Implement LogManager**

`setup()`: resolve log dir, create `QueueHandler` + `QueueListener`, attach `JsonLineFormatter` + `TimedRotatingFileHandler`. Optionally attach text `StreamHandler` if `format=both`. Start listener thread. Cleanup old logs by mtime. `shutdown()`: stop listener, close handlers.

- [ ] **Step 7: Run all logging tests**

Run: `pytest tests/logging/ -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add myagent/logging/ tests/logging/
git commit -m "feat: logging system — JSON Lines, async-safe, daily rotation"
```

---

## Phase 3: Service Layer — Tools & Registry

### Task 5: Tool base types and registry

**Files:**
- Create: `myagent/tools/base.py`
- Create: `myagent/tools/registry.py`
- Test: `tests/tools/test_base.py`
- Test: `tests/tools/test_registry.py`

**Interfaces:**
- Produces:
  - `Tool` Protocol: `name: str`, `description: str`, `parameters: dict` (JSON Schema), `async execute(params: dict, context: ToolContext) -> ToolResult`
  - `ToolContext` dataclass: `session_id: str`, `project_dir: Path`, `permissions: PermissionController`, `config: AppConfig`, `subagent_pool: SubAgentPool | None`, `working_dir: Path`
  - `ToolResult` dataclass: `output: str`, `error: str | None`, `metadata: dict` (arbitrary key-value for downstream use like `exit_code`, `file_path`, `tokens_used`)
  - `ToolRegistry` class:
    - `register(tool: Tool) -> None`
    - `unregister(name: str) -> None`
    - `get(name: str) -> Tool | None`
    - `list_all() -> list[Tool]`
    - `get_schemas() -> list[dict]` — all tool schemas for function calling
    - `get_schemas_for(names: list[str]) -> list[dict]` — subset for sub-agents

**Key design:**
- `Tool` is a `typing.Protocol` — structural subtyping, no base class needed
- Registry uses dict `{name: Tool}` internally
- `get_schemas()` returns list of `{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}` for LLM API

- [ ] **Step 1: Write failing tests for Tool protocol**

Create a class that structurally matches Tool protocol. Test that it passes isinstance check. Test ToolResult creation and defaults.

- [ ] **Step 2: Implement Tool, ToolContext, ToolResult**

Use `typing.Protocol` for Tool. Dataclasses for context and result.

- [ ] **Step 3: Write failing tests for ToolRegistry**

Test register, get, list_all, get_schemas, unregister, get_schemas_for subset.

- [ ] **Step 4: Implement ToolRegistry**

Dict-backed. `get_schemas()` assembles OpenAI-compatible schema dicts from each tool's name/description/parameters.

- [ ] **Step 5: Run tests**

Run: `pytest tests/tools/test_base.py tests/tools/test_registry.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

---

### Task 6: Built-in tools (file, search, web)

**Files:**
- Create: `myagent/tools/builtin/file_tools.py`
- Create: `myagent/tools/builtin/search_tools.py`
- Create: `myagent/tools/builtin/web_tools.py`
- Test: `tests/tools/builtin/test_file_tools.py`
- Test: `tests/tools/builtin/test_search_tools.py`
- Test: `tests/tools/builtin/test_web_tools.py`

**Interfaces:**
- Consumes: `Tool` Protocol, `ToolContext`, `ToolResult` (from Task 5)
- Produces:
  - `ReadTool` — `read(file_path: str, offset?: int, limit?: int) -> ToolResult`
  - `WriteTool` — `write(file_path: str, content: str) -> ToolResult`
  - `EditTool` — `edit(file_path: str, old_string: str, new_string: str, replace_all?: bool) -> ToolResult`
  - `GlobTool` — `glob(pattern: str, path?: str) -> ToolResult`
  - `GrepTool` — `grep(pattern: str, path?: str, output_mode?: str, glob?: str, -A?: int, -B?: int, -C?: int, -i?: bool, type?: str, head_limit?: int, multiline?: bool) -> ToolResult`
  - `WebFetchTool` — `web_fetch(url: str, prompt: str) -> ToolResult`
  - `WebSearchTool` — `web_search(query: str, allowed_domains?: list[str], blocked_domains?: list[str]) -> ToolResult`

**Key design:**
- Each tool is a class with `name`, `description`, `parameters` (JSON Schema dict), `async execute()`
- `read`: reads files, supports offset/limit for large files, renders images/PDFs
- `write`: overwrites file, fails if file exists and wasn't read first
- `edit`: exact string match + replace; `replace_all` flag for global replacement
- `glob`: fast file pattern matching, sorted by modification time
- `grep`: wraps ripgrep via subprocess, supports all ripgrep flags, output modes (content/files_with_matches/count)
- `web_fetch`: HTTP GET, converts HTML to markdown, answers prompt against content
- `web_search`: web search API, returns titles+URLs

- [ ] **Step 1: Write failing tests for file tools**

Test read (existing file, missing file, offset/limit), write (new file, overwrite denied if not read first), edit (exact match, no match raises, replace_all), glob (pattern match, no match empty).

- [ ] **Step 2: Implement file tools**

Path resolution relative to `ToolContext.project_dir`. Safety: write checks file wasn't read if overwriting. Edit validates old_string uniqueness (unless replace_all=True).

- [ ] **Step 3: Run file tool tests**

Run: `pytest tests/tools/builtin/test_file_tools.py -v`
Expected: PASS

- [ ] **Step 4: Write failing tests for search and web tools**

Grep: pattern match, file filtering, output modes, line numbers. Web: mock HTTP responses.

- [ ] **Step 5: Implement search and web tools**

Grep: subprocess.run(["rg", ...]) with args built from params. Web: httpx/aiohttp for fetch, external search API for search.

- [ ] **Step 6: Run tests**

Run: `pytest tests/tools/builtin/test_search_tools.py tests/tools/builtin/test_web_tools.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

---

### Task 7: Built-in tools (exec, session, memory, agent)

**Files:**
- Create: `myagent/tools/builtin/exec_tools.py`
- Create: `myagent/tools/builtin/session_tools.py`
- Create: `myagent/tools/builtin/memory_tools.py`
- Create: `myagent/tools/builtin/agent_tools.py`
- Test: `tests/tools/builtin/test_exec_tools.py`
- Test: `tests/tools/builtin/test_session_tools.py`
- Test: `tests/tools/builtin/test_memory_tools.py`
- Test: `tests/tools/builtin/test_agent_tools.py`

**Interfaces:**
- Consumes: Everything from Tasks 5, plus `SubAgentPool` (from Task 9, stub for now), `MemoryStore` (from Task 10, stub for now)
  - **Stub contract**: Define `SubAgentPoolStub` and `MemoryStoreStub` as Protocol classes matching the final public interfaces of Task 9 and Task 10. This ensures zero integration friction when replacing stubs with real implementations — the agent tools code compiles and passes tests against the stub, then works unchanged against the real thing. Stubs return fixed/predefined responses for tests; they do not simulate real behavior.
- Produces:
  - `BashTool` — `bash(command: str, timeout?: int, description?: str, run_in_background?: bool) -> ToolResult`
  - `TaskCreateTool` — `task_create(subject: str, description: str, activeForm?: str) -> ToolResult`
  - `TaskUpdateTool` — `task_update(taskId: str, status?: str, subject?: str, ...) -> ToolResult`
  - `MemoryWriteTool` — `memory_write(file_path: str, content: str) -> ToolResult`
  - `SpawnSubagentTool` — `spawn_subagent(prompt: str, tools?: list[str], mode?: str, isolation?: str, schema?: dict, background?: bool) -> ToolResult`
  - `SendMessageTool` — `send_message(to: str, summary?: str, message: str) -> ToolResult`

**Key design:**
- `bash`: executes via subprocess and respects timeout (default 120s). `run_in_background` uses asyncio.create_task. Permission checks are centralized in AgentEngine and PermissionController.
- `task_create`/`task_update`: manage a task list in session state (in-memory dict, persisted with session)
- `memory_write`: delegates to MemoryStore (stub → real in Task 10)
- `spawn_subagent`: delegates to SubAgentPool (stub → real in Task 9). Parameters per design doc §四 table
- `send_message`: routes messages between agents by name/id

**Task list data structure (used by task_create/task_update):**

```python
@dataclass
class TaskItem:
    id: str                          # auto-generated sequential ID
    subject: str                     # brief imperative title
    description: str                 # detailed requirements
    active_form: str | None          # present continuous for status display
    status: Literal["pending", "in_progress", "completed", "deleted"]
    owner: str | None                # agent name
    blocks: list[str]                # task IDs blocked by this one
    blocked_by: list[str]            # task IDs blocking this one
    metadata: dict[str, Any]         # arbitrary metadata

class TaskList:
    """In-memory task tracker, persisted per-session."""
    tasks: dict[str, TaskItem]
    counter: int

    def create(self, subject: str, description: str, active_form: str | None) -> TaskItem: ...
    def update(self, task_id: str, **kwargs) -> TaskItem: ...
    def get(self, task_id: str) -> TaskItem | None: ...
    def list_all(self) -> list[TaskItem]: ...
    def delete(self, task_id: str) -> None: ...
```

- [ ] **Step 1: Write failing tests for exec, session, memory tools**

Test bash execution, timeout, background mode. Test task_create/update lifecycle. Test memory_write format validation.

- [ ] **Step 2: Implement exec, session, memory tools**

Bash: async subprocess with timeout. Session tools: in-memory task dict. Memory: validate frontmatter format before write.

- [ ] **Step 3: Run tests**

Run: `pytest tests/tools/builtin/test_exec_tools.py tests/tools/builtin/test_session_tools.py tests/tools/builtin/test_memory_tools.py -v`
Expected: PASS

- [ ] **Step 4: Write failing tests for agent tools**

Test spawn_subagent parameter validation, send_message routing. Mock SubAgentPool.

- [ ] **Step 5: Implement agent tools with stub pool**

`SpawnSubagentTool.execute()` calls `context.subagent_pool.spawn(...)` if pool exists, otherwise returns error. `SendMessageTool` looks up agent by name in pool.

- [ ] **Step 6: Run agent tool tests**

Run: `pytest tests/tools/builtin/test_agent_tools.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

---

## Phase 4: Service Layer — Sub-agents, Memory, Skills

### Task 8: Permission controller

**Files:**
- Create: `myagent/permissions/controller.py`
- Create: `myagent/permissions/__init__.py`
- Test: `tests/permissions/test_controller.py`

**Interfaces:**
- Consumes: `PermissionsConfig` (from Task 2)
- Produces:
  - `PermissionController` class
  - `async check(tool_name: str, level: int, params: dict) -> PermissionResult`
  - `PermissionResult` enum: `ALLOW | DENY | ASK`
  - `async confirm(tool_name: str, params: dict) -> bool` — interactive confirmation dialog
  - `apply_runtime_rule(rule: str)` — parse natural-language rule into allow/deny lists (e.g., "git *" → auto_allow commands add "git *")
  - `set_mode(mode: Literal["ask", "allow_all"])` — switch default mode
  - `skip_all(value: bool)` — toggle `--dangerously-skip-permissions`

**Key design:**
- Level mapping: 0=read(read, glob, grep, web_fetch, web_search, task_create, task_update), 1=write(write, edit, memory_write), 2=exec(bash, spawn_subagent), 3=network_write(MCP network tools)
- Check flow: auto_deny match → DENY; auto_allow match → ALLOW; default_mode=allow_all → ALLOW; else → ASK
- Path matching: glob patterns in auto_allow.paths and auto_deny.paths
- Command matching: prefix/glob patterns (e.g., "git *" matches "git status")
- Confirmation: Rich-based dialog showing tool name, level, params summary, [A]llow/[D]eny/[Y]es to all options
- No timeout on confirm — wait forever for user response

- [ ] **Step 1: Write failing tests**

Test: level 0 tools auto-allowed by default. Test: auto_deny blocks matching path. Test: auto_allow overrides ask. Test: ask mode prompts. Test: deny > allow in priority. Test: runtime rule application. Test: skip_all bypasses everything.

- [ ] **Step 2: Implement PermissionController**

Level-resolution dict. Glob-matching for paths. Pattern-matching for commands. The `confirm()` method uses Rich's `Prompt.ask` or a custom dialog. `check()` is synchronous (pure logic), `confirm()` is async (waits for user).

- [ ] **Step 3: Run tests**

Run: `pytest tests/permissions/test_controller.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

---

### Task 9: Sub-agent pool

**Files:**
- Create: `myagent/subagent/pool.py`
- Create: `myagent/subagent/worker.py`
- Create: `myagent/subagent/__init__.py`
- Test: `tests/subagent/test_pool.py`
- Test: `tests/subagent/test_worker.py`

**Interfaces:**
- Consumes: `SubagentsConfig` (from Task 2), `LLMProvider` (from Task 3), `ToolRegistry` (from Task 5)
- Produces:
  - `SubAgentPool` class
  - `async spawn(prompt: str, tools: list[str] | None, mode: str, isolation: str | None, schema: dict | None, background: bool, parent_session: Session) -> SubAgentHandle`
  - `SubAgentHandle`: `id: str`, `status: AgentStatus` (CREATED | RUNNING | COMPLETED | FAILED | INTERRUPTED), `result: ToolResult | None`, `async wait() -> ToolResult`, `async send_message(msg: str) -> None`
  - `async shutdown() -> None` — cancel all running agents
  - `active_count: int` property
  - `AgentStatus` enum

**Key design:**
- Concurrency: semaphore `min(16, os.cpu_count() - 2)`, configurable via `max_concurrent`
- Global cap: 1000 per session — `spawn()` raises `CapExceededError` if exceeded
- Queue: asyncio.Queue for pending spawns when at concurrency limit
- Worker: each sub-agent runs its own ReAct loop (`worker.py`) with:
  - Independent context window (same model limit as main agent)
  - Tools subset (from `tools` param, or all)
  - No L2 skills, no L4 memory (per design doc §三 table)
  - L3 project context: only task-relevant parts passed via spawn prompt
  - Own transcript persistence under `subagents/sub-NNN/`
- Interruption: `send_message(to=sub_id, msg="stop")` sets an `asyncio.Event` on the worker. Worker checks this event at the top of each ReAct loop iteration and yields `Interrupted` if set. Ctrl+C on the main process also triggers interruption of all foreground sub-agents.
- Isolation: `isolation="worktree"` creates git worktree for the sub-agent (auto-clean if unchanged)
- Background rule: Goal mode → default background=true allowed; non-Goal → background=false unless `speculative_exploration` config enabled

- [ ] **Step 1: Write failing tests for SubAgentPool**

Test spawn, concurrency limit enforcement, global cap, background vs foreground, result retrieval, send_message, shutdown.

- [ ] **Step 2: Implement SubAgentPool**

`asyncio.Semaphore` for concurrency. Track active agents in dict `{id: SubAgentHandle}`. Counter for global cap.

- [ ] **Step 3: Write failing tests for worker ReAct loop**

Test that worker receives prompt, can call tools, returns result. Mock LLMProvider.

- [ ] **Step 4: Implement SubAgentWorker**

Async function that runs a ReAct loop (same pattern as main agent engine from Task 12 but with subset context). Returns `ToolResult`.

- [ ] **Step 5: Run all sub-agent tests**

Run: `pytest tests/subagent/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

---

### Task 10: Memory store and recall

**Files:**
- Create: `myagent/memory/store.py`
- Create: `myagent/memory/recall.py`
- Create: `myagent/memory/__init__.py`
- Test: `tests/memory/test_store.py`
- Test: `tests/memory/test_recall.py`

**Interfaces:**
- Consumes: `AppConfig` (for paths)
- Produces:
  - `MemoryStore` class
  - `async write(file_path: str, content: str) -> MemoryFile` — create/update memory file, validate frontmatter, update MEMORY.md index, track in session write log
  - `async read(name: str) -> MemoryFile | None` — read single memory by slug
  - `async delete(name: str) -> None` — remove file, update MEMORY.md index
  - `async list_all(scope: Literal["project", "user"]) -> list[MemoryEntry]` — from MEMORY.md index
  - `async update_index() -> None` — rebuild MEMORY.md from file scan
  - `get_session_writes() -> SessionMemoryLog` — return all writes/updates/deletes in current session for end-of-session summary
  - `reset_session_log() -> None` — clear after reporting
  - `MemoryFile` dataclass: `name: str`, `description: str`, `metadata: dict`, `content: str`, `path: Path`
  - `MemoryEntry` (index entry): `name: str`, `description: str`, `type: str` (user|feedback|project|reference), `file: str`
  - `SessionMemoryLog` dataclass: `created: list[str]`, `updated: list[str]`, `deleted: list[str]` (memory names)

**Key design:**
- Memory file format per design doc §六: frontmatter YAML (name, description, metadata{type, ...}) + markdown body
- MEMORY.md index: `- [Title](file.md) — one-line hook`
- Project memory: `<project>/.myagent/memory/`
- User memory: `~/.myagent/memory/`
- write() checks for existing file covering same fact → update instead of create duplicate
- Index is regenerated on each write to stay consistent

- [ ] **Step 1: Write failing tests for MemoryStore**

Test write (new, update existing), read, delete, list_all, index consistency after operations, frontmatter validation, duplicate detection.

- [ ] **Step 2: Implement MemoryStore**

File I/O with frontmatter parsing (YAML frontmatter + markdown body). Index file read/write. Duplicate detection via name slug.

- [ ] **Step 3: Run store tests**

Run: `pytest tests/memory/test_store.py -v`
Expected: PASS

- [ ] **Step 4: Write failing tests for semantic recall**

Test: given a query, return relevant memories ranked by relevance. Test: empty index returns empty list. Test: exact keyword match ranks higher.

- [ ] **Step 5: Implement recall module**

`async recall(query: str, store: MemoryStore, limit: int = 10) -> list[MemoryFile]` — keyword matching on name + description + content, ranked by TF-like scoring. Semantic matching via embedding can be added later (optional `sentence-transformers` dependency).

- [ ] **Step 6: Run recall tests**

Run: `pytest tests/memory/test_recall.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

---

### Task 11: Dream mechanism

**Files:**
- Create: `myagent/memory/dream.py`
- Test: `tests/memory/test_dream.py`

**Interfaces:**
- Consumes: `DreamConfig` (from Task 2), `MemoryStore` (from Task 10), `LLMProvider` (from Task 3), session transcript access
- Produces:
  - `DreamEngine` class
  - `async should_run(session_rounds: int) -> bool` — check trigger conditions
  - `async run(session_store: SessionStore) -> DreamResult` — execute dream cycle
  - `DreamResult` dataclass: `memories_created: int`, `memories_updated: int`, `memories_deleted: int`, `log_path: Path`

**Key design:**
- Trigger: distance from last dream > `trigger_hours` (default 6h) AND cumulative rounds > `trigger_rounds` (default 50). Both must be true.
- State tracked in `~/.myagent/last_dream.json`: `{last_run: ISO8601, round_count: 0}`
- Dream cycle (background, silent, no user confirmation):
  1. Scan unprocessed session transcripts since last dream
  2. Spawn a sub-agent (via LLM) to analyze transcripts for: new conventions → write memory; repeated corrections (≥2 times) → consolidate; outdated memories → delete; contradictory memories → merge
  3. Apply changes via MemoryStore
  4. Write dream log to `~/.myagent/dreams/YYYY-MM-DD.md`
  5. Update last_dream.json, reset round counter
- Principles: never modify project code, never ask user, always background

- [ ] **Step 1: Write failing tests**

Test: should_run (conditions met, not met, only one condition). Test: dream cycle with mock LLM returns expected operations. Test: last_dream.json state management.

- [ ] **Step 2: Implement DreamEngine**

State file read/write. Trigger logic. Dream cycle uses LLMProvider to analyze transcripts and produce memory operations.

- [ ] **Step 3: Run dream tests**

Run: `pytest tests/memory/test_dream.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

---

### Task 12: Skills registry and loader

**Files:**
- Create: `myagent/skills/registry.py`
- Create: `myagent/skills/loader.py`
- Create: `myagent/skills/__init__.py`
- Test: `tests/skills/test_registry.py`
- Test: `tests/skills/test_loader.py`

**Interfaces:**
- Consumes: `AppConfig` (for paths)
- Produces:
  - `SkillRegistry` class
  - `async discover() -> None` — scan three tiers (built-in → user → project), merge with priority override
  - `list_all() -> list[SkillEntry]` — all discovered skills (name + description only, for L2)
  - `get(name: str) -> Skill | None` — full skill with SKILL.md content + resource list
  - `SkillEntry` dataclass: `name: str`, `description: str`, `source: str` (builtin|user|project)
  - `Skill` dataclass: `name: str`, `description: str`, `content: str` (SKILL.md body), `resources: SkillResources`
  - `SkillResources`: `references: list[Path]`, `scripts: list[Path]`, `templates: list[Path]`, `assets: list[Path]`
  - `SkillLoader` — static methods to parse SKILL.md frontmatter + enumerate resources

**Key design:**
- Scan order: built-in package skills → `~/.myagent/skills/` → `.myagent/skills/`
- Same-named skill at higher priority **completely replaces** lower (not merge)
- L2 context injection: only `name + description` per skill (keeps context small)
- On invoke (natural language match or `/skill-name`): load full SKILL.md → inject into system prompt
- Resources are NOT loaded on invoke — only their paths are listed; Agent reads them via `read` tool as needed
- Resource types: `references/` (docs, Agent reads), `scripts/` (Agent calls via bash), `templates/` (Agent reads + fills + writes), `assets/` (Agent reads)

- [ ] **Step 1: Write failing tests for SkillLoader**

Test SKILL.md parsing (frontmatter extraction, body extraction). Test resource enumeration from skill directory.

- [ ] **Step 2: Implement SkillLoader**

YAML frontmatter parser. Directory walker for resources subdirectories.

- [ ] **Step 3: Write failing tests for SkillRegistry**

Test: discover finds skills across tiers. Test: project skill overrides user skill of same name. Test: built-in skill used when no override. Test: list_all returns name+description only. Test: get returns full skill with resources.

- [ ] **Step 4: Implement SkillRegistry**

Three-tier scan with override logic. `discover()` walks directories, calls loader for each SKILL.md.

- [ ] **Step 5: Run all skills tests**

Run: `pytest tests/skills/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

---

### Task 13: Context builder and compression

**Files:**
- Create: `myagent/context/builder.py`
- Create: `myagent/context/compression.py`
- Create: `myagent/context/__init__.py`
- Test: `tests/context/test_builder.py`
- Test: `tests/context/test_compression.py`

**Interfaces:**
- Consumes: `ContextConfig` (from Task 2), `MemoryStore` (from Task 10), `SkillRegistry` (from Task 12), `ToolRegistry` (from Task 5), `ProjectContext` (from Task 2b)
- Produces:
  - `Message` dataclass — unified message representation for internal use and API serialization
  - `ContextBuilder` class
  - `async build(current_input: str, history: list[Message], project_context: ProjectContext) -> LLMRequest`
  - `LLMRequest` dataclass: `system: str`, `messages: list[dict]`, `tools: list[dict]`
  - `to_api_format() -> dict` — returns `{"system": ..., "messages": ..., "tools": ...}` for LiteLLM
  - Six-layer assembly: L0 (system prompt) + L3 (project) + L4 (memory) + L2 (skills index) → system; L1 (tools) → tools; L5 (history) + L6 (current input) → messages

**Core data types:**

```python
@dataclass
class Message:
    """Unified message type — internal representation and API serialization."""
    role: Literal["user", "assistant", "tool", "system"]
    content: str
    tool_calls: list[ToolCallRecord] | None = None   # for assistant messages with tool calls
    tool_call_id: str | None = None                   # for tool result messages
    name: str | None = None                           # tool name for tool messages
    timestamp: datetime = field(default_factory=datetime.now)
    tokens_used: int | None = None

    def to_api_dict(self) -> dict:
        """Serialize to OpenAI-compatible message dict for LLM API."""
        ...
```

- [ ] **Step 1: Implement ContextBuilder with L0-L6 assembly**

```python
class ContextBuilder:
    # L0 System Prompt (fixed, never evicted):
    SYSTEM_PROMPT = """You are MyAgent, a CLI-based AI assistant powered by DeepSeek V4 Pro.
You operate in a ReAct loop: Think → Decide → Execute → Observe.
You have access to tools for file operations, code search, shell execution,
web access, sub-agent orchestration, and task tracking.

## Behavior Rules
- Use tools to accomplish user tasks. Prefer reading files over guessing.
- For complex multi-step tasks, use spawn_subagent to parallelize independent work.
- Large tool results (>5000 chars) will be summarized; full results are persisted to files.
- You may ask the user clarifying questions when needed. Questions have a 120s timeout;
  if unanswered, you should make a reasonable decision and proceed.
- The user may interrupt you with natural language to stop, correct, or insert new tasks.
  Interpret their intent from context — do not expect structured commands.
- Always persist important findings to memory for future sessions.
- Be thorough but concise. Verify your work before claiming completion."""

    def __init__(self, tool_registry: ToolRegistry,
                 memory_store: MemoryStore, skill_registry: SkillRegistry,
                 config: ContextConfig):
        ...

    async def build(
        self,
        current_input: str,
        history: list[Message],
        project_context: ProjectContext,
        tool_subset: list[str] | None = None,
    ) -> LLMRequest:
        # L0: SYSTEM_PROMPT (fixed behavioral rules, never evicted)
        l0 = self.SYSTEM_PROMPT

        # L3: Project context from ProjectContext detection
        l3 = self._format_project_context(project_context)
        # → AGENT.md content, git status, project type, dir structure summary

        # L4: Recall relevant memories via semantic matching against current_input
        memories = await recall(current_input, self.memory_store, limit=10)
        l4 = self._format_memories(memories)

        # L2: Skills index (name + description per skill, NOT full content)
        skills = self.skill_registry.list_all()
        l2 = self._format_skills_index(skills)

        # Assemble system prompt
        system = f"{l0}\n\n## Project Context\n{l3}\n\n## Relevant Memories\n{l4}\n\n## Available Skills\n{l2}"

        # L1: Tool schemas (all or subset)
        if tool_subset:
            tool_schemas = self.tool_registry.get_schemas_for(tool_subset)
        else:
            tool_schemas = self.tool_registry.get_schemas()

        # L5 + L6: History + current input
        messages = [m.to_api_dict() for m in history]
        messages.append({"role": "user", "content": current_input})

        return LLMRequest(system=system, messages=messages, tools=tool_schemas)
```

The system prompt assembly is the key deliverable. Each layer helper formats its section to keep the final prompt well-structured. L0 is a module-level constant — it never changes and is never compressed away.

- [ ] **Step 2: Write and run tests**

Test that each layer is included in the right position. Test tool_subset filtering. Test empty history. Test empty memory.

- [ ] **Step 3: Implement compression engine**

`CompressionEngine` class with four-layer progressive compression:

```python
class CompressionEngine:
    def __init__(self, config: CompressionConfig, llm: LLMProvider):
        ...

    async def compact(self, messages: list[Message], current_usage_pct: float) -> CompactResult:
        # Guard: return no-op if len(messages) < config.minimum_messages
        #
        # Layer 1 — Cleanup (zero-cost):
        #   Remove tool calls that were denied or returned empty results
        #   Recalculate usage; if savings < minimum_savings, skip remaining layers
        #
        # Layer 2 — Summarize tool results (low-cost):
        #   For tool results > tool_result_max_chars, replace with semantic summary + file reference
        #   Protect last 5 rounds from summarization
        #
        # Layer 3 — Conversation summary (one API call):
        #   Take oldest messages beyond protected window
        #   Ask LLM to produce structured summary: key decisions, constraints, dependencies, findings
        #   Replace summarized block with single system-message-like summary
        #   Target: guide toward target_after (default 30%). If summary doesn't reach target,
        #   accept actual result — target_after is guidance, NOT a hard constraint.
        #   Only Layer 4 (hard_limit at 90%) is a hard constraint.
        #
        # Layer 4 — Hard truncation:
        #   Only if still > hard_limit (90%)
        #   Drop oldest message blocks until under limit
        #   Warn user to /clear
        #
        # Return CompactResult with new messages, usage_after, layers_applied
```

**Anti-flap protection:**
- Track consecutive failures per layer — if Layer 3 fails 3 times, disable it (downgrade)
- Minimum 10 messages before any compression
- Skip layers if savings < `minimum_savings` (10%)

- [ ] **Step 4: Write failing tests for compression**

Test: cleanup removes denied tool calls. Test: summarization replaces large results. Test: conversation summary invoked for old messages. Test: hard truncation as last resort. Test: anti-flap (10-message minimum, minimum savings, 3-failure disable).

- [ ] **Step 5: Implement compression engine**

Each layer as a separate method. `CompactResult` tracks what was applied and final state.

- [ ] **Step 6: Run compression tests**

Run: `pytest tests/context/test_compression.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

---

### Task 14: Session persistence

**Files:**
- Create: `myagent/context/persistence.py`
- Test: `tests/context/test_persistence.py`

**Interfaces:**
- Consumes: `SessionConfig` (from Task 2)
- Produces:
  - `SessionStore` class
  - `async create_session(project_dir: Path) -> Session` — new session with generated ID
  - `async save_turn(session: Session, turn: Turn) -> None` — append to transcript
  - `async save_tool_call(session: Session, call: ToolCallRecord) -> None` — persist full tool I/O
  - `async save_subagent_transcript(session: Session, sub_id: str, transcript: list[Turn]) -> None`
  - `async save_summary(session: Session, summary: CompactSummary) -> None`
  - `async list_sessions(project_dir: Path) -> list[SessionSummary]`
  - `async load_session(session_id: str, project_dir: Path) -> Session | None`
  - `async export_session(session_id: str, format: str, project_dir: Path) -> Path`

**Key data structures:**

```python
@dataclass
class Session:
    id: str              # "2026-07-02-abc123"
    project_name: str    # from directory name
    project_hash: str    # SHA256 of absolute path, first 7 chars
    created_at: datetime
    updated_at: datetime
    goal: str | None
    goal_achieved: bool | None
    total_tokens: int
    turn_count: int

@dataclass
class Turn:
    index: int
    role: Literal["user", "assistant", "tool", "system"]
    content: str
    tool_calls: list[ToolCallRecord] | None
    timestamp: datetime
    tokens_used: int | None

@dataclass
class ToolCallRecord:
    call_id: str         # "call-001"
    tool_name: str
    params: dict
    result: ToolResult | None
    permission: PermissionResult
    timestamp: datetime

@dataclass
class SessionSummary:
    session_id: str
    created_at: datetime
    first_message: str   # truncated preview
    duration: float      # hours
    total_tokens: int
    goal_achieved: bool | None  # None = no goal set
```

**Directory structure per design doc §三:**
```
~/.myagent/sessions/<project_name>/<project_hash>/<session_id>/
├── transcript.json, transcript.md
├── subagents/sub-NNN/transcript.{json,md}
├── tools/call-NNN.json
└── summaries/compact-NNN.md
```

- [ ] **Step 1: Write failing tests**

Test: create session generates valid ID and paths. Test: save/load turn round-trips. Test: list sessions returns summaries sorted by date. Test: export produces valid markdown.

- [ ] **Step 2: Implement SessionStore**

File I/O with `transcript.json` (JSON Lines for append) and `transcript.md` (Markdown appendix). Directory creation on first write. SHA256 hashing for project_hash.

- [ ] **Step 3: Run persistence tests**

Run: `pytest tests/context/test_persistence.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

---

## Phase 5: Application Layer

### Task 15: Agent engine (ReAct loop)

**Files:**
- Create: `myagent/agent/engine.py`
- Create: `myagent/agent/__init__.py`
- Test: `tests/agent/test_engine.py`

**Interfaces:**
- Consumes: `LLMProvider` (Task 3), `ToolRegistry` (Task 5), `PermissionController` (Task 8), `SubAgentPool` (Task 9), `ContextBuilder` + `CompressionEngine` (Task 13), `SessionStore` (Task 14), `SkillRegistry` (Task 12), `ProjectContext` (Task 2b)
- Produces:
  - `AgentEngine` class
  - `async run(user_input: str, session: Session) -> AsyncIterator[AgentEvent]`
  - `AgentEvent` union: `TextChunk(content: str) | ThinkingChunk(content: str) | ToolCallStart(name: str, call_id: str) | ToolCallEnd(call_id: str, result: ToolResult) | AskUserQuestion(question: str, options: list[str]) | Done(usage: Usage) | Error(message: str) | Interrupted | IntentSignal(intent: str)`

**Core ReAct loop pseudo-code:**

```python
class AgentEngine:
    TOOL_RESULT_MAX_CHARS = 5000

    def __init__(
        self,
        llm: LLMProvider,
        tool_registry: ToolRegistry,
        permissions: PermissionController,
        subagent_pool: SubAgentPool,
        context_builder: ContextBuilder,
        compression: CompressionEngine,
        session_store: SessionStore,
        skill_registry: SkillRegistry,
        goal_tracker: GoalTracker,
        project_context: ProjectContext,
        config: AppConfig,
        project_dir: Path,
    ):
        self.llm = llm
        self.tool_registry = tool_registry
        self.permissions = permissions
        self.subagent_pool = subagent_pool
        self.context_builder = context_builder
        self.compression = compression
        self.session_store = session_store
        self.skill_registry = skill_registry
        self.goal_tracker = goal_tracker
        self.project_context = project_context
        self.config = config
        self.project_dir = project_dir

    async def run(self, user_input: str, session: Session) -> AsyncIterator[AgentEvent]:
        # 0. NL intent interpretation (before entering ReAct loop)
        # The model checks if user_input is:
        #   - STOP: "停下", "别改了" → yield IntentSignal("stop"), return
        #   - CORRECT: "不对，你应该..." → extract correction, use as next input
        #   - INSERT: "先帮我查一下..." → queue new task, re-enter with original goal
        #   - CONTINUE: "继续" → resume from last state
        # This is done by the model in the first LLM call — not hardcoded rules.
        # If the model's first response is a pure text (no tool calls) that reads
        # like an acknowledgment of a stop/correct, the engine breaks the loop.

        # 1. Build context (L0-L6)
        request = await self.context_builder.build(
            current_input=user_input,
            history=session.get_recent_messages(),
            project_context=self.project_context,
        )

        # 2. Check compression before sending
        estimated_tokens = await self.llm.token_count(
            [{"role": "system", "content": request.system}] + request.messages
        )
        usage_pct = estimated_tokens / MODEL_MAX_TOKENS
        if usage_pct >= self.config.compression.primary_threshold:
            result = await self.compression.compact(session.messages, usage_pct)
            if result.messages_changed:
                session.messages = result.messages
                request = await self.context_builder.build(
                    current_input=user_input,
                    history=session.messages,
                    project_context=self.project_context,
                )

        # 3. ReAct loop
        while True:
            # Think: stream LLM response
            tool_calls_in_turn = []
            has_done = False
            done_usage = None

            async for event in self.llm.complete(
                messages=request.to_api_format(),
                tools=request.tools,
                thinking=self.config.model.thinking,
            ):
                if isinstance(event, ThinkingDelta):
                    yield ThinkingChunk(content=event.content)
                elif isinstance(event, TextDelta):
                    yield TextChunk(content=event.content)
                elif isinstance(event, ToolCall):
                    tool_calls_in_turn.append(event)
                elif isinstance(event, Done):
                    has_done = True
                    done_usage = event.usage

            # If model signaled done with no tool calls, break
            if has_done and not tool_calls_in_turn:
                yield Done(usage=done_usage)
                break

            # Decision → Execute → Observe (for each tool call)
            for tc in tool_calls_in_turn:
                # Check if this is a skill invocation
                # Model can output: tool_call(name="skill_invoke", params={"skill": "code-review"})
                if tc.name == "skill_invoke":
                    skill = self.skill_registry.get(tc.params["skill"])
                    if skill:
                        # Inject full SKILL.md into system prompt for next turn
                        session.inject_skill(skill)
                        yield ToolCallEnd(call_id=tc.id,
                            result=ToolResult(output=f"Skill '{skill.name}' loaded."))
                        continue

                yield ToolCallStart(name=tc.name, call_id=tc.id)

                # Permission check
                perm = self.permissions.check(tc.name, self._get_tool_level(tc.name), tc.params)
                if perm == PermissionResult.DENY:
                    result = ToolResult(output="", error="Permission denied")
                elif perm == PermissionResult.ASK:
                    allowed = await self.permissions.confirm(tc.name, tc.params)
                    if not allowed:
                        result = ToolResult(output="", error="User denied")
                    else:
                        result = await self._execute_tool(tc, session)
                else:
                    result = await self._execute_tool(tc, session)

                yield ToolCallEnd(call_id=tc.id, result=result)

                # Persist full tool result, inject summary into context
                await self.session_store.save_tool_call(session, ToolCallRecord(...))
                summary = await self._summarize_if_large(result, tc.name)
                session.add_tool_result(tc.id, summary)

            # After tool execution, check if model wants to ask user a question
            # (Detected by model emitting text that is a question + no more tool calls)
            if self._is_asking_question(last_response):
                yield AskUserQuestion(question=..., options=[...])
                # Wait for user response with 120s timeout
                user_answer = await self._wait_for_user_response(timeout=120)
                if user_answer is None:
                    # Timeout — agent auto-decides
                    session.add_message(Message(role="system",
                        content="User did not respond within 120s. Proceed with your best judgment."))
                else:
                    session.add_message(Message(role="user", content=user_answer))
                # Re-enter loop with the answer

            # If goal is set and model signaled done, check goal
            if has_done and session.goal:
                goal_check = await self._check_goal(session)
                if goal_check.achieved:
                    yield Done(usage=done_usage)
                    break
                else:
                    # Feed remaining_work as input, re-enter loop
                    session.add_message(Message(role="system",
                        content=f"Goal not yet achieved. Remaining work: {goal_check.remaining_work}"))
                    has_done = False  # re-enter loop
                    continue

            if has_done:
                yield Done(usage=done_usage)
                break

    async def _execute_tool(self, tc: ToolCall, session: Session) -> ToolResult:
        tool = self.tool_registry.get(tc.name)
        if not tool:
            return ToolResult(output="", error=f"Unknown tool: {tc.name}")
        try:
            ctx = ToolContext(
                session_id=session.id,
                project_dir=self.project_dir,
                permissions=self.permissions,
                config=self.config,
                subagent_pool=self.subagent_pool,
                working_dir=self.project_dir,
            )
            return await tool.execute(tc.params, ctx)
        except Exception as e:
            return ToolResult(output="", error=str(e))

    async def _summarize_if_large(self, result: ToolResult, tool_name: str) -> str:
        """If tool result > 5000 chars, spawn a sub-agent summarizer.
        Returns summary text + file path reference to full result."""
        if len(result.output) <= self.TOOL_RESULT_MAX_CHARS:
            return result.output

        # Spawn a lightweight sub-agent to summarize
        summary_prompt = (
            f"Summarize this tool result from '{tool_name}'. "
            f"Keep key findings, numbers, file paths, and error messages. "
            f"Be concise but complete. The full result is saved to disk.\n\n"
            f"{result.output[:50000]}"  # truncate to avoid sub-agent context overload
        )
        handle = await self.subagent_pool.spawn(
            prompt=summary_prompt,
            tools=["read"],  # minimal tools
            mode="Non-think",  # fast mode for summarization
            isolation=None,
            schema=None,
            background=False,
            parent_session=session,
        )
        summary_result = await handle.wait()
        return (
            f"[Summary] {summary_result.output}\n"
            f"[Full result saved to session tool call record: {tool_name}]"
        )

    async def _check_goal(self, session: Session) -> GoalCheckResult:
        """Delegate to GoalTracker to judge if goal is achieved."""
        return await self.goal_tracker.check_goal(session, session.get_recent_messages())

    async def _wait_for_user_response(self, timeout: float) -> str | None:
        """Wait for user to answer an agent question. Returns None on timeout.
        This creates a temporary prompt_toolkit input session with a countdown
        timer displayed in the status bar."""
        ...  # Implemented in REPL layer; engine exposes an async event/callback

    def _get_tool_level(self, tool_name: str) -> int:
        LEVEL_MAP = {
            "read": 0, "glob": 0, "grep": 0,
            "web_fetch": 0, "web_search": 0,
            "task_create": 0, "task_update": 0,
            "write": 1, "edit": 1, "memory_write": 1,
            "bash": 2, "spawn_subagent": 2, "send_message": 2,
        }
        return LEVEL_MAP.get(tool_name, 3)  # unknown/MCP tools default to level 3
```

**Key behaviors:**
- Text response → stream ThinkingDelta (collapsed) + TextDelta (visible), break when Done
- Tool call → execute (with permission check), inject result, continue loop
- Interleaved text + tool calls: stream text first, then execute tools
- `spawn_subagent` tool → delegated to SubAgentPool; if not background, `await handle.wait()`
- Ctrl+C during execution → yield Interrupted, engine cancels current tool and waits for next input
- Large tool results (>5000 chars, configurable) → spawn summarizer sub-agent → return summary + file path reference
- **NL intent interpretation:** Model's first response in each turn is inspected. If it's a pure text acknowledgment (no tool calls, short, reads like "好的，已停止"), engine treats it as a stop/correct/insert intent and adjusts accordingly.
- **Agent questions:** Model can emit `AskUserQuestion` by writing a question-text response + no tool calls. Engine detects this pattern, yields `AskUserQuestion` event, waits 120s for user response. On timeout, feeds auto-decide instruction to model.
- **Skill auto-invoke:** Skills are not loaded as tools. Instead, a virtual `skill_invoke` tool exists. When the model calls it, the engine loads the full SKILL.md and injects it into the next system prompt. On next loop iteration, the model follows the skill's instructions.
  - **Design rationale:** Using a virtual tool (intercepted in the engine before ToolRegistry lookup) rather than real tool registration avoids: (a) polluting L1 tools schema with skills that aren't real tools, (b) permission checks on skill loading (skills are instructions, not actions), (c) coupling between skill registry and tool registry. The model discovers available skills via L2 context (skills index with name+description) and "calls" them through the same function-calling mechanism it already uses for tools — no new model capability needed.
- **Goal re-entry:** When goal check fails, `remaining_work` is injected as a system message and the loop continues (not a new user input).

- [ ] **Step 1: Write failing tests**

Test: simple text response yields TextChunk + Done. Test: tool call yields ToolCallStart + ToolCallEnd + continues loop. Test: multiple sequential tool calls. Test: permission deny short-circuits. Test: interrupt handling. Test: goal check when goal set.

- [ ] **Step 2: Implement AgentEngine**

Core loop as above. `_execute_tool` looks up tool in registry, calls `execute()`, handles errors. `_get_tool_level` maps tool names to permission levels. `_summarize_if_large` checks char count threshold. `_check_goal` uses LLM to judge if goal is achieved.

- [ ] **Step 3: Run engine tests**

Run: `pytest tests/agent/test_engine.py -v`
Expected: PASS (mock LLMProvider for deterministic behavior)

- [ ] **Step 4: Commit**

---

### Task 16: Goal tracker

**Files:**
- Create: `myagent/agent/goal.py`
- Test: `tests/agent/test_goal.py`

**Interfaces:**
- Consumes: `LLMProvider` (Task 3), `Session` (Task 14)
- Produces:
  - `GoalTracker` class
  - `set_goal(goal: str) -> None` — set current goal
  - `clear_goal() -> None` — remove goal
  - `get_goal() -> str | None`
  - `async check_goal(session: Session, recent_history: list[Message]) -> GoalCheckResult` — LLM judges if goal achieved
  - `GoalCheckResult` dataclass: `achieved: bool`, `reasoning: str`, `remaining_work: str | None`

**Key design:**
- Goal mode is an overlay on the ReAct loop — not a separate execution mode
- When model emits `done` and goal is set, GoalTracker interjects: "Is this goal achieved? <goal text>"
- LLM judges with a structured prompt, returns structured response
- If not achieved, `remaining_work` is fed back as next user input — re-enter loop
- If achieved, report completion summary

- [ ] **Step 1: Write failing tests**

Test: set/get/clear goal. Test: check_goal returns achieved=True when LLM confirms. Test: check_goal returns achieved=False with remaining_work. Test: no goal set returns achieved=None.

- [ ] **Step 2: Implement GoalTracker**

Lightweight wrapper. `check_goal` constructs a structured prompt asking the LLM to evaluate goal completion status.

- [ ] **Step 3: Run goal tests**

Run: `pytest tests/agent/test_goal.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

---

### Task 17: Session manager

**Files:**
- Create: `myagent/agent/session.py`
- Test: `tests/agent/test_session.py`

**Interfaces:**
- Consumes: `SessionStore` (Task 14), `ProjectContext` (Task 2b), `MemoryStore` (Task 10), `PermissionController` (Task 8)
- Produces:
  - `SessionManager` class
  - `async start_new(project_dir: Path, goal: str | None) -> Session` — create and initialize
  - `async resume(session_id: str | None, project_dir: Path) -> Session` — resume latest or specific session
  - `async list_sessions(project_dir: Path) -> list[SessionSummary]` — delegate to store
  - `async end_session(session: Session) -> None` — finalize, write completion marker, **prompt to persist permission changes**, **summarize memories written/updated during session**
  - `async export_session(session_id: str, format: str) -> Path` — delegate to store

**Session end flow (end_session):**
1. AgentEngine stops current ReAct loop if running; StatusBar Live display ends
2. Mark session as closed in transcript
3. Check if runtime permission rules were modified during session → if yes, prompt user via `Console.print()` (prompt_toolkit is stopped at this point): "本次会话中调整了 N 条权限规则，是否持久化到配置文件？[Y/n]"
4. If user confirms, write updated rules to appropriate config file (project or user level)
5. Call `memory_store.get_session_writes()` → display: "本次对话中新写入/更新了 N 条记忆: [name1, name2, ...]"
6. Reset session write log
7. Process exits with code 0

**REPL coordination:** The shutdown sequence is triggered by `/exit`, `/quit`, or Ctrl+D (EOF). Ctrl+C at idle also triggers it after "Exit? (y/n)" confirmation. `end_session()` uses `rich.Console.print()` for I/O because prompt_toolkit's `PromptSession` is stopped before the session-end prompts. The method is synchronous — all file writes complete before the process exits.

**Key design:**
- Session ID format: `YYYY-MM-DD-<6-char-random>` (e.g., `2026-07-02-abc123`)
- Project detection (reused `ProjectContext.detect()`):
  - project_name = `os.path.basename(project_dir)`
  - project_hash = `hashlib.sha256(str(project_dir).encode()).hexdigest()[:7]`
- `--resume` without ID → find most recent session under project_hash
- `--list-sessions` → pretty-print table with Rich (session ID, first message preview, duration, tokens, goal status)
- `--export` → generate single markdown file from transcript

- [ ] **Step 1: Write failing tests**

Test: start_new creates valid session with correct ID format. Test: resume returns latest session. Test: resume with specific ID returns that session. Test: resume nonexistent ID raises. Test: list_sessions across multiple sessions.

- [ ] **Step 2: Implement SessionManager**

Thin orchestration over SessionStore + ProjectContext. Session ID generation with date prefix and random suffix.

- [ ] **Step 3: Run session tests**

Run: `pytest tests/agent/test_session.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

---

## Phase 6: CLI Layer

### Task 18: CLI main entry and argument parsing

**Files:**
- Create: `myagent/cli/main.py`
- Test: `tests/cli/test_main.py`

**Interfaces:**
- Consumes: `ConfigLoader` (Task 2), `SessionManager` (Task 17), `PermissionController` (Task 8)
- Produces:
  - `main()` — async entry point, no args (called by console_scripts)
  - CLI args parsed via argparse:
    - `--resume [SESSION_ID]` — resume session
    - `--list-sessions` — list all sessions
    - `--session SESSION_ID --export FORMAT` — export session
    - `--mode {think-high,think-max,non-think}` — thinking mode override
    - `--dangerously-skip-permissions` — full trust mode (calls `permissions.skip_all(True)`)
    - `--goal "..."` — start with goal
    - `--config PATH` — custom config path
    - `--project-dir PATH` — override project directory

**Wiring for --dangerously-skip-permissions:**
CLI arg → `ConfigLoader.from_cli_args()` creates config with `dangerously_skip_permissions=True` → passed to `PermissionController.skip_all(True)` during initialization in `main()`. This bypasses ALL permission checks (even level 3). A warning banner is displayed at startup: "⚠️  全权限模式 — 所有操作将自动执行，不进行确认。"

- [ ] **Step 1: Write failing tests**

Test: argument parsing produces correct overrides. Test: --resume without value triggers latest session lookup. Test: conflicting args raise error.

- [ ] **Step 2: Implement argument parsing and main()**

argparse setup. `main()` orchestrates: parse args → load config with CLI overrides → detect project → handle --list-sessions / --export (one-shot, exit) → start REPL. Async entry via `asyncio.run(main())`.

- [ ] **Step 3: Run main tests**

Run: `pytest tests/cli/test_main.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

---

### Task 19: REPL engine

**Files:**
- Create: `myagent/cli/repl.py`
- Test: `tests/cli/test_repl.py`

**Interfaces:**
- Consumes: `AgentEngine` (Task 15), `SlashCommand` dispatcher (Task 20)
- Produces:
  - `REPLEngine` class
  - `async run() -> None` — start REPL loop
  - `async process_input(text: str) -> None` — handle one input line

**Key design:**
- prompt_toolkit `PromptSession` with:
  - Auto-completion: slash commands, file paths, tool names
  - History: `FileHistory` in `~/.myagent/history`
  - Multi-line editing: auto-detect or Ctrl+Enter
  - Syntax highlighting: markdown/code blocks via Pygments lexer
- Input handling:
  - Empty input → skip
  - Starts with `/` → dispatch to slash command handler (Task 20)
  - Otherwise → pass to AgentEngine.run() as natural language
- Stream rendering: iterate AgentEvent from engine, render each via `Renderer` (Task 21)
- Ctrl+C handling:
  - During idle → prompt "Exit? (y/n)"
  - During agent execution → yield Interrupted event, agent handles gracefully
- Ctrl+D (EOF) → exit

- [ ] **Step 1: Write failing tests**

Test: empty input skipped. Test: slash command dispatched. Test: natural language passed to engine. Test: events streamed and collected.

- [ ] **Step 2: Implement REPLEngine**

prompt_toolkit setup with custom keybindings, completer, and style. Event loop integration with asyncio.

- [ ] **Step 3: Run REPL tests**

Run: `pytest tests/cli/test_repl.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

---

### Task 20: Slash commands

**Files:**
- Create: `myagent/cli/commands.py`
- Test: `tests/cli/test_slash_commands.py`

**Interfaces:**
- Consumes: `AgentEngine` (Task 15), `GoalTracker` (Task 16), `SkillRegistry` (Task 12), `DreamEngine` (Task 11), `SessionManager` (Task 17)
- Produces:
  - `SlashCommand` Protocol: `name: str`, `description: str`, `async execute(args: str, context: CommandContext) -> CommandResult`
  - `CommandContext`: `engine, goal_tracker, skill_registry, dream_engine, session_manager, session, config`
  - `CommandDispatcher` class: `dispatch(line: str, ctx: CommandContext) -> CommandResult`
  - Built-in commands: `/mode <mode>`, `/goal <text>`, `/skills`, `/dream`, `/clear`, `/history`, `/exit`

**Command implementations:**

| Command | Behavior |
|---------|----------|
| `/mode think-high\|think-max\|non-think` | Update `config.model.thinking` at runtime, confirm change |
| `/goal <text>` | Set goal via GoalTracker, confirm. `/goal` alone shows current goal |
| `/goal clear` | Clear current goal |
| `/skills` | List all skills (name + description) from SkillRegistry, formatted table |
| `/dream` | Manually trigger DreamEngine.run(), show results when complete |
| `/clear` | Clear current conversation history in session (keep transcript on disk) |
| `/history` | Show recent conversation summary (last N turns) |
| `/exit` or `/quit` | Graceful shutdown: save session, prompt if goal not checked |

- [ ] **Step 1: Write failing tests for each command**

Test mode switch updates config. Test goal set/clear cycles. Test skills list formatting. Test clear resets history. Test exit triggers save.

- [ ] **Step 2: Implement SlashCommand classes and dispatcher**

Each command is a class implementing `SlashCommand` protocol. Dispatcher matches `/command` prefix, extracts args, routes to handler.

- [ ] **Step 3: Run slash command tests**

Run: `pytest tests/cli/test_slash_commands.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

---

### Task 21: Status bar and stream renderer

**Files:**
- Create: `myagent/cli/status.py`
- Create: `myagent/cli/renderer.py`
- Test: `tests/cli/test_status.py`
- Test: `tests/cli/test_renderer.py`

**Interfaces:**
- Consumes: `UIConfig` (from Task 2), `AgentEvent` types (from Task 15)
- Produces:
  - `StatusBar` class — Rich Live layout
  - `async start() -> None` — begin live display
  - `update(**kwargs) -> None` — refresh displayed values
  - `stop() -> None` — end live display
  - Display items (configurable via `ui.status_bar_items`): sub-agents count/status, token usage, thinking mode
  - `Renderer` class — converts AgentEvent stream to Rich renderables
  - `render_event(event: AgentEvent) -> RenderableType` — dispatch by event type

**Status bar layout:**
```
┌─ MyAgent ─────────────────────────────────────────────┐
│ 🤖 Sub-agents: 3 active │ Token: 156K │ Think High    │
│  ├─ review-auth      ⏳ 审查中... (82%)                │
│  ├─ review-api       ✅ 完成 (2 个问题)                │
│  └─ review-middleware 🔄 重试中 (1/3)                  │
└───────────────────────────────────────────────────────┘
```

**Renderer behavior:**
- `TextChunk` → stream inline (no newline, live update)
- `ThinkingChunk` → dim/collapsed by default, expandable in status
- `ToolCallStart` → show tool name + params preview
- `ToolCallEnd` → show result summary (first ~200 chars)
- `Done` → final usage stats
- `Error` → red panel with error message
- Markdown content within TextChunk → Rich `Markdown()` renderable

- [ ] **Step 1: Write failing tests for renderer**

Test each event type produces correct Rich renderable. Test markdown rendering. Test error formatting.

- [ ] **Step 2: Implement Renderer**

Event dispatch dict. Markdown rendering via Rich's Markdown class. Code block syntax highlighting.

- [ ] **Step 3: Write failing tests for status bar**

Test sub-agent status updates. Test token counter. Test mode display. Test configurable items.

- [ ] **Step 4: Implement StatusBar**

Rich `Live` with `Layout`. Background thread/task to refresh at ~4Hz. Updates pushed via `update()` method called by engine during loop.

- [ ] **Step 5: Run all CLI rendering tests**

Run: `pytest tests/cli/test_renderer.py tests/cli/test_status.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

---

## Phase 7: Integration & Polish

### Task 22: End-to-end integration

**Files:**
- Create: `tests/integration/test_full_loop.py`
- Create: `tests/integration/test_subagent_flow.py`
- Create: `tests/integration/test_session_lifecycle.py`

**Interfaces:**
- Consumes: All modules from Tasks 1-21
- Produces: Integration test suite

- [ ] **Step 1: Write full ReAct loop integration test**

Mock LLM to return a sequence: think text → tool call → observe → think text → done. Verify events flow through engine → renderer → session persistence.

- [ ] **Step 2: Write sub-agent flow integration test**

Spawn sub-agent via spawn_subagent tool → sub-agent runs own loop → returns result → main agent continues. Verify sub-agent transcript is saved independently.

- [ ] **Step 3: Write session lifecycle integration test**

Start session → run turns → save → resume → verify history intact → export.

- [ ] **Step 4: Run integration tests**

Run: `pytest tests/integration/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 23: Default built-in skills

**Files:**
- Create: `myagent/skills/builtin/brainstorming/SKILL.md`
- Create: `myagent/skills/builtin/code-review/SKILL.md`
- Create: `myagent/skills/builtin/systematic-debugging/SKILL.md`
- Create: `myagent/skills/builtin/tdd/SKILL.md`
- Create: `myagent/skills/builtin/writing-plans/SKILL.md`
- Create: `myagent/skills/builtin/executing-plans/SKILL.md`

**Interfaces:**
- Consumes: SKILL.md format (from Task 12)
- Produces: Six built-in skill definitions

Each SKILL.md follows the format from design doc §七: frontmatter (name, description) + body (when to use, process, notes, available resources). Package as package data in pyproject.toml.

- [ ] **Step 1: Write each SKILL.md**

Content per design doc §七 skill table. Include resource references where applicable.

- [ ] **Step 2: Verify skills are discoverable**

Test that SkillRegistry discovers all six built-in skills.

- [ ] **Step 3: Commit**

---

## Phase 8: Packaging & Distribution

### Task 24: pipx/pip packaging and final polish

**Files:**
- Modify: `pyproject.toml` — verify all package data, entry points, classifiers
- Create: `README.md` — installation and usage
- Create: `myagent/cli/main.py` — verify `main()` entry point

- [ ] **Step 1: Verify pyproject.toml completeness**

Check: `[project.scripts]` entry point, `[tool.setuptools.package-data]` for built-in skills, classifiers, license. Package data must include `myagent/skills/builtin/**/*.md`.

- [ ] **Step 2: Write README.md**

Installation (`pipx install myagent`), quick start, configuration, key features summary.

- [ ] **Step 3: Test pip install in dev mode**

Run: `pip install -e .` in a clean venv
Expected: `myagent --help` works

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -v --cov=myagent --cov-report=term-missing`
Expected: All tests pass, coverage > 80%

- [ ] **Step 5: Run lint**

Run: `ruff check myagent/`
Expected: No errors

- [ ] **Step 6: Commit**

---

## Dependency Graph

```
Phase 1 (Setup)
  1: scaffold ──► 2: config ──► 2b: project detection
                    │
Phase 2 (Infra)     │
  3: LLM ◄──────────┤
  4: MCP ◄──────────┤
  4b: logging ◄─────┤ (depends on 2)
                    │
Phase 3 (Tools)     │
  5: base+registry ◄┤
  6: builtin-1 ◄────┤
  7: builtin-2 ◄────┤ (depends on stubs from 9,10; logging from 4b)
                    │
Phase 4 (Services)  │
  8: permissions ◄──┤
  9: sub-agents ◄───┤ (depends on 3,5)
  10: memory ◄──────┤
  11: dream ◄───────┤ (depends on 3,10)
  12: skills ◄──────┤
  13: context ◄─────┤ (depends on 2b,3,5,10,12)
  14: persistence ◄─┤
                    │
Phase 5 (App)       │
  15: engine ◄──────┤ (depends on 2b,3,5,8,9,12,13,14)
  16: goal ◄────────┤ (depends on 3,14)
  17: session ◄─────┤ (depends on 2b,8,10,14)
                    │
Phase 6 (CLI)       │
  18: main ◄────────┤ (depends on 2,8,17)
  19: repl ◄────────┤ (depends on 15,20)
  20: commands ◄────┤ (depends on 11,12,15,16,17)
  21: status+render ┤ (depends on 2,15)
                    │
Phase 7 (Integration)
  22: integration ◄─┘ (all)
  23: builtin skills ┘

Phase 8 (Ship)
  24: packaging ◄────┘ (all)
```

---

## Test Strategy

| Layer | Test Type | Mock Strategy |
|-------|-----------|---------------|
| Config | Unit | Filesystem (tmp_path) |
| Logging | Unit | Filesystem (tmp_path), mock time |
| LLM Provider | Unit | Mock litellm.acompletion |
| MCP | Unit | Mock subprocess stdin/stdout |
| Tools | Unit | Mock ToolContext, filesystem |
| Permissions | Unit | Pure logic, no mocks needed |
| Sub-agents | Unit | Mock LLMProvider |
| Memory | Unit | Filesystem (tmp_path) |
| Skills | Unit | Filesystem with SKILL.md fixtures |
| Context | Unit | Mock MemoryStore, SkillRegistry |
| Session | Unit | Filesystem (tmp_path) |
| Agent Engine | Unit | Mock LLMProvider, ToolRegistry |
| Goal Tracker | Unit | Mock LLMProvider |
| CLI | Unit | Mock AgentEngine, prompt_toolkit |
| Integration | Integration | Mock LLMProvider only |

**Core invariant to test:** The ReAct loop must always make progress — never loop infinitely on the same tool call, always advance state. Every tool call must produce an observation that differs from the previous turn.
