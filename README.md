# MyAgentCLI

一个个人 AI Agent 助手，CLI 形式。基于 DeepSeek V4 Pro（1M 上下文窗口），Python 实现。

## 项目状态

🚧 **设计阶段** — 架构设计已完成，待实现。

详见 [设计文档](docs/superpowers/specs/2026-07-02-myagentcli-design.md)。

## 核心特性 (计划)

- **REPL + 流式输出**: Rich + prompt_toolkit 驱动，支持 Markdown 渲染
- **ReAct Agent 循环**: 默认 Think High 推理模式，支持手动切换
- **Goal 模式**: 设定目标后 Agent 自主拆解、编排、持续推进，支持人工中途介入
- **子 Agent 系统**: 独立上下文、并行/流水线编排、自然语言驱动工作流
- **MCP 协议**: 从第一天纳入架构，内置核心工具 + MCP 扩展
- **分层上下文**: 六层结构，75% 触发自动压缩，智能摘要
- **文件级记忆**: 跨会话持久化 + 梦境机制自动整合更新
- **技能系统**: 内置技能 + 用户自定义，支持 `/skill-name` 强制调用
- **可配置沙箱**: 分级权限，对话内可调整，支持全权限模式
- **会话持久化**: JSON/Markdown transcript，支持恢复和追溯

## 技术栈

- **语言**: Python 3.12+
- **CLI**: Rich + prompt_toolkit
- **模型接入**: LiteLLM
- **基础模型**: DeepSeek V4 Pro (1.6T 参数, 49B 激活, MoE, 1M 上下文)
- **MCP**: 子进程通信
- **分发**: pipx / pip (PyPI)

## 安装 (计划)

```bash
pipx install myagent
# 或
pip install myagent
```

## 使用 (计划)

```bash
myagent                    # 启动 REPL
myagent -p "修复这个 bug"   # 单次查询
myagent --resume           # 恢复上次会话
myagent /goal "重构认证模块" # Goal 模式
```
