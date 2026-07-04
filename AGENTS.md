# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

MyAgentCLI 是一个个人 AI Agent 助手，CLI 形式。使用 DeepSeek V4 Pro 作为基础模型，Python 实现。

## Tech Stack

- **Language**: Python 3.12+
- **CLI**: Rich + prompt_toolkit (REPL + 流式输出 + Agent Inspector Pane)
- **Model Access**: LiteLLM (统一抽象层，主模型 DeepSeek V4 Pro)
- **MCP**: 子进程通信 (stdio/SSE)
- **Distribution**: pipx / pip (PyPI)
- **Config**: YAML
- **Persistence**: JSON + Markdown files

## Build, Test, and Lint

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest tests/ -v

# Run a single test
pytest tests/tools/test_registry.py::test_register_and_execute -v

# Run tests for a specific module
pytest tests/config/ -v

# Lint
ruff check myagent/

# Run the CLI (development)
python -m myagent.cli.main
```

## Implementation Plan

See [Implementation Plan](docs/superpowers/plans/2026-07-02-myagentcli-implementation.md) — 16 tasks across 5 phases.

## Architecture

Four-layer asynchronous monolith:

```
CLI Layer (Rich + prompt_toolkit)
Application Layer (Agent Engine + Goal Tracker + Session Manager)
Service Layer (Tool Registry + Sub-Agent Pool + Memory Store)
Infrastructure Layer (LiteLLM + MCP Protocol)
```

### Key Design Decisions

- **Execution Model**: Single ReAct Loop for all interactions. Goal mode is a tracking overlay — on `done`, check if goal is achieved; if not, re-enter loop.
- **Sub-agents**: Spawned via `spawn_subagent` tool. Model decides parallelism/pipeline/loop-until patterns. Max concurrent = min(16, CPU cores - 2). Global cap: 1000 per session. Sub-agent context window = 1M (same as main).
- **Context Management**: Six-layer structure (L0-L6). Auto-compact at 75% usage, guided toward 30% (non-binding). Hard truncation only at 90%. Four-layer progressive compression. All records persisted to files; tool results summarized in context.
- **No Complexity Router**: Model autonomously judges task complexity and orchestration strategy. No hardcoded rules.
- **User Interaction**: Pure natural language — no `/stop`, `/insert` commands. Agent interprets intent (stop/correct/insert new task) from user messages.
- **Thinking Mode**: User-selected, default Think High. `/mode think-high|think-max|non-think`.
- **Permission System**: 4-level (read/write/exec/network). Configurable allow/deny lists. Users adjust rules mid-conversation in natural language. `--dangerously-skip-permissions` for full trust.
- **Memory**: File-level (frontmatter Markdown per fact). MEMORY.md index. Dream mechanism runs silently in background (6h interval + 50 round threshold) to consolidate, update, and clean memories.
- **Skills**: Built-in + user-custom (`SKILL.md` files). `/skill-name` forces invocation. Model can also auto-invoke based on context.
- **Goal mode sub-agents default to background allowed**; non-Goal speculative exploration requires explicit user config opt-in.
- **Tools**: Unified interface (built-in + MCP). Large tool results (>5000 chars) → sub-agent summarizes → returns summary + file reference.
- **Sessions**: Organized by `<project-name>/<project-hash>/<session-id>/`. transcript.json + transcript.md per session. `--resume` and `--list-sessions` supported.
- **Config**: 7-level priority (CLI args → runtime override → project config → project AGENT.md → user config → user AGENT.md → defaults).

See [Design Spec](docs/superpowers/specs/2026-07-02-myagentcli-design.md) for full details.

## File Structure (planned)

```
myagent/
├── cli/              # CLI layer (REPL, Agent Inspector Pane, commands)
├── agent/            # Agent engine (ReAct loop, goal tracker)
├── tools/            # Built-in tools + MCP bridge
├── subagent/         # Sub-agent pool and lifecycle
├── context/          # Context manager (layers, compression)
├── memory/           # Memory store + dream engine
├── skills/           # Skill loader and registry
├── session/          # Session persistence and management
├── config/           # Configuration loading and merging
├── permissions/      # Sandbox and permission controller
├── llm/              # LiteLLM provider wrapper
└── logging/          # Structured logging (JSON Lines, async-safe)
```

## Conventions

- **文档同步更新**: 每次操作完成后，必须检查是否需要更新技术文档，包括但不限于 `AGENTS.md`、`README.md`、设计文档、任务文档等。如果代码变更影响了架构、构建方式、测试方式或项目约定，相关文档必须同步更新。
- **仅本地提交**: 只做 `git commit`，不执行 `git push`。GitHub 在当前网络环境下不可达。
- **计划不含代码**: 实现计划不包含代码块，仅描述文件路径、接口签名、任务依赖、测试场景。代码在实现阶段写入实际文件。
- **日志规范**: 所有模块必须通过 `logging.getLogger("myagent.<module>")` 输出日志。关键事件分类如下：
  - **LLM 交互** (`category="llm"`): 每次 API 调用记录 request（model, messages_count, estimated_tokens）和 response（latency_ms, token 消耗, retry_count）
  - **工具调用** (`category="tool"`): 每次执行记录 tool_name, params_summary（截断 200 字符）, permission_result, duration_ms, result_size_chars
  - **子Agent 生命周期** (`category="subagent"`): spawn/completed/failed/interrupted, prompt_summary, duration_ms
  - **ReAct 循环** (`category="agent"`): 每轮迭代的 iteration 计数, event 类型, tokens_used
  - **系统事件** (`category="system"`): startup, shutdown, config 加载
  - **异常** (`category="error"`): 所有 except 块必须记录 exception_type + traceback + context（触发时的操作描述）+ component（llm/tool/agent/mcp/system/memory/subagent；与现有日志 category 对齐：LLM、工具、Agent、MCP、系统、记忆、子Agent）
  - 禁止使用 `print()` 输出日志；CLI 层面向用户的输出通过 Rich/renderer 处理，内部事件通过 logging
