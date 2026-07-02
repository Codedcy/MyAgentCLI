# MyAgentCLI 设计实现完整性校验报告

> 审计日期: 2026-07-03 | 代码版本: `71ccb48` | 设计文档: `2026-07-02-myagentcli-design.md` | 实现计划: 24 任务 / 8 阶段

---

## 一、总体结论

**实现完成度: ~60%**

项目骨架、模块划分、数据模型定义已基本完成，但**多个核心模块存在存根实现（stub）、关键逻辑缺失、组件间集成未完成**的问题。应用层（Agent Engine、CLI）的问题最为集中——ReAct 循环是单次执行而非迭代循环，多个 CLI 组件虽已编写但从未被实例化或连线。

---

## 二、关键发现 (Critical/High Severity)

### 🔴 Critical — 核心功能不可用

| # | 模块 | 问题 | 影响 |
|---|------|------|------|
| 1 | `agent/engine.py` | **ReAct 循环是单次执行**：`_react_loop()` 只做一轮 LLM 调用→工具执行→Done，不将工具结果反馈给模型进行下一轮决策 | Agent 无法执行需要多步推理的任务 |
| 2 | `subagent/worker.py` | **`run()` 是完全存根**：无 ReAct 循环、无 LLM 调用、无工具执行，立即返回占位符 | 子 Agent 功能完全不可用 |
| 3 | `tools/builtin/web_tools.py` | **`web_search` 是完全存根**：返回硬编码的占位字符串，无任何搜索引擎集成 | Web 搜索工具不可用 |
| 4 | `memory/dream.py` | **`run()` 是完全存根**：不执行合并/更新/清理，只写占位日志，DreamResult 始终为 0 | 记忆整合机制(梦想引擎)完全不可用 |

### 🟠 High — 组件间集成断裂

| # | 模块 | 问题 | 影响 |
|---|------|------|------|
| 5 | `cli/main.py` | **`--resume` 参数被解析但忽略**：`async_main()` 始终调用 `start_new`，无恢复路径 | 会话恢复功能不可用 |
| 6 | `cli/main.py` | **斜杠命令调度器从未连线到 REPL**：`commands=None` 传入 REPLEngine | 全部 7 个斜杠命令不可用 |
| 7 | `cli/main.py` | **StatusBar 和 Renderer 从未实例化**：两个类已实现但 main.py 中无创建和连线代码 | Rich 格式化输出和状态栏不可用 |
| 8 | `agent/engine.py` | **AskUserQuestion / IntentSignal 从未被生成**：事件类已定义但引擎从不 yield 它们 | Agent 无法主动问用户问题，无法解释自然语言意图 |
| 9 | `agent/engine.py` | **大结果摘要不使用子 Agent**：规范要求 `>5000 字符 → 子Agent摘要 → 摘要+文件引用`，实际只是简单字符串截断 | 长结果丢失全部超出 5000 字符的内容 |
| 10 | `agent/goal.py` | **`check_goal()` 始终返回 True**：无 LLM 评估，无进度跟踪 | 目标追踪完全不可用 |
| 11 | `agent/engine.py` | **目标模式重入循环断裂**：goal 未达成时应重新进入循环，实际直接 return | 目标达成前就退出 |
| 12 | `subagent/pool.py` | **`spawn()` 不使用 Worker**：`_run_background/foreground` 用 `asyncio.sleep(0.01)` 替代实际 worker 启动 | 子 Agent 池空有架构无实际执行 |
| 13 | `agent/project.py` | **无项目根目录自动检测**：只检查传入的单一目录，不上溯查找 git root | 子目录中运行时无法识别项目和 git 状态 |

### 🟠 High — 规范要求的功能缺失

| # | 模块 | 问题 |
|---|------|------|
| 14 | `llm/provider.py` | **零 LLM 交互日志**：规范要求每次 API 调用记录 `model, messages_count, latency_ms, token_usage, retry_count`，实现中无任何 `logging` 调用 |
| 15 | `logging/logger.py` | **缺少按大小轮转**：规范要求"按天 + 按大小"双轮转，只有 `TimedRotatingFileHandler`（按天），无 `RotatingFileHandler`（按大小），`max_size_mb` 配置字段被读取但从未使用 |
| 16 | `tools/base.py` | **`ToolResult` 缺少 `success` bool 和 `artifacts` 字段**：调用者必须通过 `error is not None` 推断失败 |
| 17 | `tools/registry.py` | **无源追踪**：所有工具存入平铺 dict，无法区分 built-in vs MCP；**无内置优先冲突处理**：`register()` 简单覆盖 |
| 18 | `permissions/controller.py` | **`confirm()` 是无操作存根**：不显示 Rich 对话框，不等待用户输入，始终返回 True |
| 19 | `tools/builtin/web_tools.py` | **`web_fetch` 不转换 HTML 为 Markdown**：返回原始 HTML，`prompt` 参数未被使用 |
| 20 | `tools/builtin/exec_tools.py` | **无沙箱强制执行**：`dangerouslyDisableSandbox` 参数被接受但从不检查 |
| 21 | `config/loader.py` | **无环境变量插值**：`~` 路径不展开，`${VAR}` 不替换 |
| 22 | `config/loader.py` | **AGENT.md 两个优先级层级无效**：`_load_agent_md()` 始终返回 `{}` |

---

## 三、中等严重度发现 (Medium Severity)

### 模块级问题

| # | 模块 | 问题 |
|---|------|------|
| 23 | `agent/session.py` | 每次迭代无自动保存；`end_session()` 不持久化任何内容 |
| 24 | `context/compression.py` | 第 3 层不调用 LLM 做摘要（使用 `[Conversation summary: ...]` 占位文本） |
| 25 | `context/compression.py` | 词元使用量计算不准确（使用启发式乘数而非实际测量） |
| 26 | `context/builder.py` | L5(技能)不加载实际 SKILL.md 指令内容，只有名称摘要 |
| 27 | `context/builder.py` | L3(工具结果)和 L6(目标上下文)未被注入 |
| 28 | `context/persistence.py` | `load_session()` 不恢复消息列表；转录只保存最后 50 条 |
| 29 | `memory/store.py` | **无去重**：`write()` 不检查同名记忆是否存在 |
| 30 | `memory/store.py` | **不支持 `[[name]]` 链接语法** |
| 31 | `memory/recall.py` | 纯关键词重叠，无语义嵌入；每次召回重读全部记忆文件 |
| 32 | `skills/registry.py` | 无自动调用逻辑（无嵌入匹配、无意图检测） |
| 33 | `skills/registry.py` | 不递归搜索多层目录（只搜索直接子目录） |
| 34 | `tools/builtin/memory_tools.py` | 无 MEMORY.md 索引集成；无路径范围限制 |
| 35 | `tools/builtin/session_tools.py` | 纯内存存储不持久化；全局单例破坏多会话隔离 |
| 36 | `tools/builtin/file_tools.py` | `read` 无 2000 行上限强制 |
| 37 | `tools/builtin/search_tools.py` | 缺少 `-n`, `-o`, `type`, `offset`, `multiline` 参数 |
| 38 | `tools/builtin/agent_tools.py` | `spawn_subagent` 无 `model` 覆盖参数 |
| 39 | `tools/mcp/client.py` | stderr 管道从不消费（资源泄漏）；无 OpenAI function-calling 模式转换 |
| 40 | `tools/mcp/adapter.py` | 最小化模式转换（无 `$ref`/`oneOf` 支持）；MCP 工具无 permission_level |
| 41 | `logging/formatter.py` | 缺少 `pid`, `traceback`, `component`, `context` 字段；时间戳格式不符 |
| 42 | `llm/provider.py` | 工具调用流可能发送重复/部分的 ToolCall 事件 |
| 43 | `cli/repl.py` | 多行输入未启用；使用 `print()` 而非 Rich Renderer；Ctrl+C 退出而非中断 |
| 44 | `cli/commands.py` | `/exit` 无确认提示；`/clear`, `/history` 无实际功能 |
| 45 | `llm/provider.py` | 无 fallback 模型支持（配置字段存在但未使用） |
| 46 | `subagent/pool.py` | 后台任务绕过并发信号量（并发泄漏）；`send_message` 是无操作存根 |

---

## 四、各阶段完成度评估

| 阶段 | 任务数 | 完成度 | 关键缺失 |
|------|--------|--------|----------|
| Phase 1: 骨架 & 配置 | 3 | **70%** | 无 env var 插值、AGENT.md 层级无效、无项目根目录自动检测 |
| Phase 2: 基础设施 | 3 | **60%** | LLM 交互无日志、缺少按大小日志轮转、stderr 泄漏、无模式转换 |
| Phase 3: 工具 & 注册表 | 3 | **65%** | web_search 存根、web_fetch 不转换 HTML、ToolResult 缺字段、无源追踪 |
| Phase 4: 子Agent/记忆/技能 | 7 | **40%** | Worker 存根、Dream 存根、Pool 不使用 Worker、去重/Link 缺失、压缩不调 LLM |
| Phase 5: 应用层 | 3 | **35%** | ReAct 单次执行、Goal 始终 True、无自动保存、无 IntentSignal/AskUserQuestion |
| Phase 6: CLI 层 | 4 | **30%** | Renderer/StatusBar 未连线、斜杠命令未连线、`--resume` 忽略、多行未启用 |
| Phase 7: 集成测试 & 技能 | 2 | **80%** | 集成测试和内置 SKILL.md 已实现，但测试覆盖因存根而受限 |
| Phase 8: 打包 | 1 | **90%** | pyproject.toml、入口点、README 齐全 |

---

## 五、严格遵守设计的领域 ✅

以下方面实现完全符合或超过设计规范：

- **配置模式**：9 个数据类完整覆盖所有配置域，默认值与规范精确匹配
- **7 级配置合并**：优先级和深度合并逻辑完全正确
- **权限模型**：4 级层级、auto-allow/deny 优先级、完全信任跳过均正确建模
- **子 Agent 池**：并发公式 `min(16, CPU-2)`、全局上限 1000、CapExceededError 正确
- **记忆存储**：文件级 frontmatter Markdown + MEMORY.md 索引模式正确
- **技能注册表**：3 层优先级覆盖发现正确
- **会话持久化**：目录结构 `~/.myagent/sessions/<project>/<hash>/<session-id>/` 正确
- **日志系统**：异步安全 QueueHandler+QueueListener 模式正确
- **JSON-RPC MCP 客户端**：初始化→列举工具→调用工具的协议流程正确
- **所有事件类型已定义**：9 个 AgentEvent 子类均在 engine.py 中定义
- **工具协议结构**：Tool/ToolContext/ToolResult 的基本形状和统一接口存在

---

## 六、修复优先级建议

### 第一批：让核心 Agent 循环工作
1. 修复 `engine.py` 的 ReAct 循环为真正的迭代循环
2. 修复 `goal.py` 的 check_goal 为 LLM 驱动的评估
3. 修复 `engine.py` 中目标未达成时重新进入循环

### 第二批：让 CLI 可用
4. 在 `main.py` 中连线 Renderer、StatusBar、CommandDispatcher
5. 修复 `--resume` 和 `--config` 参数实际生效
6. REPL 启用多行输入、使用 Renderer 替代 print()

### 第三批：让子 Agent 工作
7. 实现 `worker.py` 中真正的 ReAct 循环
8. 将 `pool.py` 中的 spawn 分发到实际 Worker

### 第四批：补全存根实现
9. 实现 `web_search` (集成搜索引擎)
10. 实现 `dream.py` 的记忆整合逻辑
11. 修复 `web_fetch` 的 HTML→Markdown 转换
12. 实现 `controller.py` 的交互式 confirm

### 第五批：规范对齐
13. 补全全模块日志记录
14. ToolResult 添加 `success`/`artifacts`
15. 实现环境变量插值和路径展开
16. 实现大结果子 Agent 摘要而非截断
