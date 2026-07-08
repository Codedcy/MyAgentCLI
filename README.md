# MyAgentCLI

MyAgentCLI 是一个个人 AI Agent CLI 助手，默认使用 DeepSeek V4 Pro，通过 LiteLLM 接入模型，提供固定聊天窗口、ReAct 工具循环、子 Agent、项目记忆、会话恢复和权限控制。

项目仍处于 Alpha 阶段，但核心 CLI 工作流已经可用。本仓库当前自动化测试集覆盖 561 个用例。

## 功能概览

- **固定聊天窗口**：交互模式默认进入全屏 TUI，对话记录可滚动，输入框常驻底部，右侧显示 Agent Inspector。
- **流式 ReAct 循环**：Agent 按 Think → Decide → Execute → Observe 执行，支持 Think High、Think Max、Non-think 三种模式。
- **Goal 模式**：可在启动时或会话中设定目标，目标会立即生效，并显示在 Inspector 中。
- **子 Agent 编排**：支持创建子 Agent、并行任务、消息传递和任务追踪。
- **内置工具**：文件读写编辑、glob、grep、bash、Web fetch/search、MCP 资源/提示、记忆写入、配置调整、任务管理、子 Agent 管理。
- **权限系统**：按 read/write/exec/network 分级确认，支持 allow/deny 规则和 `--dangerously-skip-permissions` 全信任模式。
- **会话持久化**：保存 JSON 与 Markdown transcript，支持恢复、列出、导出历史会话。
- **项目记忆**：项目级 `.myagent/memory/` 与用户级记忆共同参与上下文构建，支持 `MEMORY.md` 索引和后台 dream 整合。
- **CLI Runtime UX**：权限申请显示在底部临时托盘，工具输出默认折叠并可用 F3 展开，Agent 思考时显示计时。
- **结构化日志**：使用 JSON Lines 异步日志，覆盖 LLM、工具、Agent、MCP、系统、记忆和子 Agent 事件。

## 安装

开发安装：

```bash
git clone <repo-url>
cd MyAgentCLI
pip install -e ".[dev]"
```

安装后会注册 `myagent` 命令：

```bash
myagent --help
```

项目要求 Python 3.12+。当前代码在本地开发环境中也会被 Python 3.14 测试覆盖。

## 快速开始

启动当前目录作为项目上下文：

```bash
myagent
```

常用启动参数：

```bash
# 恢复最近会话
myagent --resume

# 恢复指定会话
myagent --resume <session-id>

# 列出当前项目会话
myagent --list-sessions

# 导出指定会话
myagent --session <session-id> --export markdown
myagent --session <session-id> --export json

# 指定思考模式
myagent --mode think-high
myagent --mode think-max
myagent --mode non-think

# 启动时设置目标
myagent --goal "完成登录模块重构"

# 指定项目目录
myagent --project-dir D:\code\some-project

# 跳过权限确认，适合完全可信的本地自动化场景
myagent --dangerously-skip-permissions
```

一次性命令不会进入全屏聊天窗口，例如 `--list-sessions` 和 `--session ... --export ...` 会输出结果后退出。

## 交互窗口

`myagent` 默认启动固定聊天窗口：

- 左侧主窗格显示系统、用户、Agent、工具和队列消息。
- 右侧 Agent Inspector 显示 session、project、model、thinking、token、context、goal、subagent、tool 和 health 状态。
- 输入框固定在底部，不会被流式输出推走。
- 对话内容按终端宽度换行，常见 Markdown-ish 回复会在显示层整理成更适合阅读的标题、列表、表格和目录树。
- 当 `ui.syntax_highlight` 开启时，Agent 回复和展开后的工具详情中的 fenced code block 会进行显示层语法高亮；语言名后缺少换行的紧凑代码围栏会先规范为块级代码再渲染；支持 Python、JavaScript/TypeScript、SQL、JSON/YAML、Shell/PowerShell、HTML/CSS/XML、C/C++ 和 Rust 等语言族。
- Agent 忙碌时继续发送的普通消息会先进入可见队列，等上一轮完成后再进入 transcript。
- `/goal <text>` 等即时控制命令不会进入队列，会立即更新状态。
- 工具结果默认折叠成一行摘要，按 F3 展开最近工具详情。
- 权限确认会显示在底部临时托盘，选择后自动消失。

按键：

| Key | 行为 |
|---|---|
| `Enter` | 提交当前输入 |
| `Esc+Enter` / `Alt+Enter` | 插入换行 |
| `F2` | 展开或折叠 Agent Inspector |
| `F3` | 展开或折叠最近工具详情 |
| `Esc` | 运行中中断当前 Agent 对话；空闲时不退出 |
| `Ctrl+C` | 运行中中断 Agent；空闲时清空输入或触发退出确认 |
| `Ctrl+D` | 输入为空时退出 |
| `PageUp` / `PageDown` | 滚动对话记录 |
| 鼠标滚轮 | 滚动对话记录，默认开启，可通过 `mouse_support` 关闭 |

`mouse_support: true` 会启用对话窗内滚轮滚动。Windows 下优先使用 prompt_toolkit 的原生 Win32 鼠标输入，并主动关闭 VT/SGR 鼠标上报，避免 Windows Terminal 将鼠标报告打印到输入框。如果你更需要终端原生鼠标选择复制，可以关闭鼠标事件：

```yaml
ui:
  chat_window:
    mouse_support: false
```

## 斜杠命令

| 命令 | 说明 |
|---|---|
| `/mode think-high\|think-max\|non-think` | 切换思考模式 |
| `/goal [text]` | 查看或设置当前目标 |
| `/goal clear` | 清除目标 |
| `/skills` | 列出可用技能 |
| `/dream` | 手动运行记忆整合 |
| `/compact` | 非破坏性压缩当前上下文 |
| `/clear` | 清空当前 UI 内存对话，磁盘 transcript 保留 |
| `/history [N]` | 查看最近 N 条会话历史 |
| `/export [markdown\|json]` | 导出当前会话 |
| `/help` | 查看命令帮助 |
| `/exit` / `/quit` | 退出 |

也可以通过 `/<skill-name>` 强制调用已注册技能。

## 配置

配置使用 YAML，并按以下优先级合并，越靠后优先级越高：

1. 默认值
2. `~/.myagent/AGENT.md`
3. `~/.myagent/config.yaml`
4. `.myagent/AGENT.md`
5. `.myagent/config.yaml`
6. 运行时覆盖
7. CLI 参数

常用配置示例：

```yaml
model:
  provider: deepseek
  model: deepseek-v4-pro
  thinking: Think High

permissions:
  default_mode: ask
  auto_allow:
    levels: [0]
    paths: []
    commands: []
  auto_deny:
    paths: [".env", "*.key", "*.pem"]
    commands: ["sudo", "rm -rf /"]

ui:
  syntax_highlight: true
  chat_window:
    enabled: true
    scrollback_lines: 2000
    input_min_lines: 1
    input_max_lines: 6
    follow_output: auto
    mouse_support: true
  status_pane:
    enabled: true
    placement: right
    width: 34
    min_width: 28
    max_width: 48
    collapse_below_columns: 120
    rail_width: 5
    toggle_key: f2
    sections: [session, tokens, goal, subagents, tools, health]

session:
  save_transcripts: true
  transcript_format: [json, markdown]
  sessions_dir: ~/.myagent/sessions/

logging:
  level: INFO
  dir: ~/.myagent/logs/
  format: jsonl
  retention_days: 30
```

完整设计见 [设计文档](docs/superpowers/specs/2026-07-02-myagentcli-design.md)。

## 数据位置

- **项目配置**：`.myagent/config.yaml`
- **项目记忆**：`.myagent/memory/*.md` 与 `.myagent/memory/MEMORY.md`
- **用户配置**：`~/.myagent/config.yaml`
- **用户记忆**：`~/.myagent/memory/`
- **会话记录**：默认 `~/.myagent/sessions/<project-name>/<project-hash>/<session-id>/`
- **日志**：默认 `~/.myagent/logs/`

每个会话目录会保存 transcript 数据，可用于 `--resume`、`--list-sessions` 和 `--export`。

## 架构

MyAgentCLI 是四层异步单体：

```text
CLI Layer
  ├─ chat_window.py       固定聊天窗口
  ├─ input_controller.py  输入与快捷键
  ├─ status.py            Agent Inspector
  ├─ repl.py              REPL 事件桥接
  └─ commands.py          斜杠命令

Application Layer
  ├─ engine.py            ReAct 循环
  ├─ goal.py              Goal 追踪
  ├─ session.py           会话生命周期
  └─ project.py           项目环境检测

Service Layer
  ├─ tools/               内置工具与 MCP 适配
  ├─ subagent/            子 Agent 池
  ├─ context/             上下文构建与压缩
  ├─ memory/              记忆存储、召回、dream
  └─ skills/              技能加载与注册

Infrastructure Layer
  ├─ llm/                 LiteLLM Provider
  ├─ config/              YAML 加载与合并
  ├─ permissions/         权限控制
  └─ logging/             异步结构化日志
```

## 内置工具

| 工具 | 用途 |
|---|---|
| `read` / `write` / `edit` | 文件读取、写入、编辑 |
| `glob` / `grep` | 文件匹配与内容搜索 |
| `bash` | 执行 Bash 命令；Windows 上优先使用 Git Bash / `MYAGENT_BASH` |
| `web_fetch` / `web_search` | 抓取网页与搜索 |
| `spawn_subagent` / `send_message` | 子 Agent 创建与通信 |
| `task_create` / `task_update` | 任务追踪 |
| `memory_write` | 写入项目或用户记忆 |
| `config_set` | 运行时配置调整 |
| `mcp_read_resource` / `mcp_get_prompt` | MCP 资源与提示访问 |

`bash` 工具会执行真正的 Bash 语义。Windows 上会优先使用 `MYAGENT_BASH`、
`PATH` 中的 `bash.exe` 或 Git Bash 默认安装路径；如果找不到 Bash 且命令
包含 `mkdir -p`、heredoc、`touch`、`rm -rf` 等 POSIX 语法，会直接报错，
不会回退到 `cmd.exe`/PowerShell 误创建无用文件。

## 开发

安装开发依赖：

```bash
pip install -e ".[dev]"
```

常用验证命令：

```bash
# 全量测试
pytest tests/ -v

# 快速全量测试
pytest tests/ -q

# 单个测试
pytest tests/tools/test_registry.py::test_register_and_execute -v

# 特定模块
pytest tests/cli/test_chat_window.py -q
pytest tests/config/ -v

# Lint
ruff check myagent/

# 覆盖率
pytest tests/ -v --cov=myagent --cov-report=term-missing
```

## 相关文档

- [总体设计文档](docs/superpowers/specs/2026-07-02-myagentcli-design.md)
- [Chat Window UI 设计](docs/superpowers/specs/2026-07-05-chat-window-ui-design.md)
- [TUI 语法高亮设计](docs/superpowers/specs/2026-07-07-tui-syntax-highlighting-design.md)
- [实现计划](docs/superpowers/plans/2026-07-03-myagentcli-implementation.md)
- [CLI Runtime UX 计划](docs/superpowers/plans/2026-07-07-cli-runtime-ux.md)
- [TUI 语法高亮计划](docs/superpowers/plans/2026-07-07-tui-syntax-highlighting.md)

## Sub-agent status and output

When `spawn_subagent` runs in foreground mode, the tool result includes the
sub-agent's final output so the main Agent can continue in the same ReAct loop.
When the model starts background sub-agents, completion observations are kept
across turns. When a background sub-agent finishes, fails, or is interrupted,
the REPL schedules an internal `continue`, injects the completion output back
into the main ReAct loop, and continues without requiring another user message.

Use `/subagents` to list active and recent sub-agents, including status,
summary, and transcript path. Use `/subagent <id>` to show one sub-agent's full
output. These status inspection commands run immediately and do not wait behind
the normal chat submission queue. Persisted transcripts are stored under the
session directory at
`subagents/<id>/transcript.json` and `subagents/<id>/transcript.md`.

## License

MIT
