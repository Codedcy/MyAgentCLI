"""Sub-agent worker — runs ReAct loop in isolation.

Each sub-agent has its own context window (same model limit as main agent),
tool subset, and transcript persistence. Skills and memory are NOT loaded
for sub-agents.

Design doc reference: §八 子 Agent 池与工作流编排
"""

from __future__ import annotations


class SubAgentWorker:
    """Runs a sub-agent's ReAct loop with isolated context."""

    def __init__(
        self,
        prompt: str,
        tools: list[str] | None = None,
        mode: str = "Think High",
        isolation: str | None = None,
    ):
        self.prompt = prompt
        self.tools = tools
        self.mode = mode
        self.isolation = isolation

    async def run(self) -> str:
        """Execute the sub-agent task and return result.

        In production, this runs a full ReAct loop with LLM calls,
        tool execution, and context management. The sub-agent has:
        - No L2 skills index
        - No L4 memory (avoid context pollution)
        - Tool subset from spawn params
        - Own transcript under subagents/sub-NNN/
        """
        # Placeholder — full ReAct loop integration in Task 15
        return f"Sub-agent completed: {self.prompt[:100]}"
