# MyAgentCLI

个人 AI Agent 助手，CLI 形式。基于 **DeepSeek V4 Pro**（1M 上下文窗口，1.6T/49B MoE），Python 实现。

## 项目状态

✅ **已实现** — 24 个任务已落地，251 个自动化测试通过。

## 核心特性

- **REPL + 流式输出**: Rich + prompt_toolkit，Markdown 渲染，固定右侧 Agent Inspector Pane
- **ReAct Agent 循环**: Think → Decide → Execute → Observe，默认 Think High
- **Goal 模式**: 设定目标后 Agent 自主拆解、编排、持续推进，支持人工介入
- **子 Agent 系统**: 独立上下文、并行/流水线编排，最大并发 min(16, CPU-2)
- **16 个内置工具**: read/write/edit/glob、grep（ripgrep 优先 + Python 回退）、bash、web_fetch/search、spawn_subagent/send_message、task_create/update、memory_write、config_set、mcp_read_resource/mcp_get_prompt
- **MCP 协议**: 手写 JSON-RPC client，支持 stdio 子进程通信和 SSE 传输抽象，自动发现工具/资源/提示
- **分层上下文**: L0-L6 六层结构，75% 自动压缩，四层渐进式压缩
- **文件级记忆**: 跨会话持久化 + MEMORY.md 索引 + 梦境机制自动整合
- **技能系统**: 6 个内置技能 + 用户自定义，三优先级覆盖（内置 < 用户 < 项目）
- **权限系统**: 四级分级（0=只读/1=写入/2=执行/3=网络），allow/deny 列表，对话内调整
- **会话持久化**: JSON + Markdown transcript，`--resume` 恢复，`--list-sessions` 浏览
- **7 级配置合并**: CLI 参数 → 运行时 → 项目配置 → 项目 AGENT.md → 用户配置 → 用户 AGENT.md → 默认值
- **结构化日志**: JSON Lines，异步安全（QueueHandler），按天轮转，自动清理
- **零二进制依赖**: grep 工具优先调用 ripgrep，不可用时自动回退到纯 Python（`re` + `pathlib`）

## 架构

```
CLI Layer (Rich + prompt_toolkit)
  ├─ main.py        — 入口，参数解析，组件装配
  ├─ repl.py        — REPL 引擎
  ├─ commands.py    — 斜杠命令 (/mode, /goal, /skills, /dream, ...)
  ├─ renderer.py    — AgentEvent → Rich 渲染
  ├─ layout.py      — Rich Layout/Live 固定窗格控制器
  └─ status.py      — Agent Inspector Pane 渲染与兼容 StatusBar alias

Application Layer
  ├─ engine.py      — ReAct 循环（唯一执行模式）
  ├─ goal.py        — Goal 追踪叠加层
  ├─ session.py     — 会话生命周期管理
  └─ project.py     — 项目环境检测（Git、语言、包管理器等）

Service Layer
  ├─ tools/         — 工具注册表 + 16 个内置工具 + MCP 适配
  ├─ subagent/      — 子 Agent 池（并发控制、生命周期）
  ├─ context/       — 上下文构建器 + 压缩引擎 + 会话持久化
  ├─ memory/        — 记忆存储 + 语义召回 + 梦境引擎
  └─ skills/        — 技能发现 + 加载 + 注册

Infrastructure Layer
  ├─ llm/           — LiteLLM Provider（流式、思考模式、重试）
  ├─ config/        — 7 级 YAML 加载 + deep merge
  ├─ permissions/   — 4 级权限控制
  ├─ logging/       — JSON Lines 异步日志
  └─ tools/mcp/     — MCP JSON-RPC 客户端（stdio/SSE transport）
```

## 技术栈

| 组件 | 选型 | 原因 |
|------|------|------|
| 语言 | Python 3.12+ | AI/LLM 生态完善 |
| CLI 输入 | prompt_toolkit | 自动补全、历史搜索、多行编辑 |
| CLI 输出 | Rich | Markdown 渲染、Live 刷新、Panel/Layout |
| 模型接入 | LiteLLM | 上百模型统一抽象，换模型只改配置 |
| 基础模型 | DeepSeek V4 Pro | 1M 上下文，1.6T/49B MoE，MIT 协议 |
| MCP | 手写 JSON-RPC client | 行业标准协议，stdio/SSE transport |
| 配置 | YAML | 人类可读，多层合并 |
| 持久化 | JSON + Markdown | JSON 结构化，Markdown 可读 |
| 分发 | pipx / pip (PyPI) | Python CLI 标准分发 |

## 安装

```bash
# 开发安装
git clone <repo-url>
cd myagentcli
pip install -e ".[dev]"

# (计划) PyPI 发布后
pipx install myagent
```

## 使用

```bash
# 启动 REPL
myagent

# 恢复最近会话
myagent --resume

# 恢复指定会话
myagent --resume <session-id>

# 列出所有会话
myagent --list-sessions

# 导出会话
myagent --session <session-id> --export markdown

# 指定思考模式
myagent --mode think-max

# 设定目标
myagent --goal "重构认证模块"

# 全权限模式（跳过所有确认）
myagent --dangerously-skip-permissions

# 指定项目目录
myagent --project-dir /path/to/project
```

### REPL 中的斜杠命令

| 命令 | 功能 |
|------|------|
| `/mode think-high\|think-max\|non-think` | 切换思考模式 |
| `/goal <text>` | 设定目标（无参数查看当前） |
| `/goal clear` | 清除目标 |
| `/skills` | 列出可用技能 |
| `/dream` | 手动触发梦境 |
| `/clear` | 清空对话历史（保留磁盘 transcript） |
| `/history` | 查看近期对话摘要 |
| `/exit` 或 `/quit` | 退出 |

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行全部测试
pytest tests/ -v

# 运行单个测试
pytest tests/tools/test_registry.py::TestToolRegistry::test_register_and_execute -v

# 运行特定模块
pytest tests/config/ -v

# 代码检查
ruff check myagent/

# 覆盖率
pytest tests/ -v --cov=myagent --cov-report=term-missing
```

## 配置

配置文件位置（优先级从低到高）：

1. 硬编码默认值
2. `~/.myagent/AGENT.md`
3. `~/.myagent/config.yaml`
4. `.myagent/AGENT.md`（项目级）
5. `.myagent/config.yaml`（项目级）
6. 运行时覆盖
7. CLI 参数

完整配置项见 [设计文档 §九](docs/superpowers/specs/2026-07-02-myagentcli-design.md)。

### Agent Inspector Pane 配置

CLI 默认在右侧显示固定 `Agent Inspector Pane`，用于展示会话、token、上下文占用、目标、子 Agent、工具调用和健康状态。终端宽度低于 `collapse_below_columns` 时会自动折叠成窄 rail；`F2` 可在当前布局中展开或收起 Inspector，不会提交当前输入。

```yaml
ui:
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
```

兼容性：旧配置 `ui.show_status_bar` 仍会映射到 `ui.status_pane.enabled`，`ui.status_bar_items` 仍会映射到 `ui.status_pane.sections`。显式配置 `ui.status_pane.*` 时优先使用新配置。

## 文档

- [设计文档](docs/superpowers/specs/2026-07-02-myagentcli-design.md)
- [实现计划](docs/superpowers/plans/2026-07-03-myagentcli-implementation.md)

## License

MIT
