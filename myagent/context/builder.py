"""Context builder — assembles L0-L6 layers into LLM API request."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from myagent.agent.project import ProjectContext


@dataclass
class ToolCallRecord:
    call_id: str
    tool_name: str
    params: dict
    result: Any | None = None
    permission: str = "allow"
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Message:
    """Unified message representation for internal use and API serialization."""
    role: Literal["user", "assistant", "tool", "system"]
    content: str
    tool_calls: list[ToolCallRecord] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)
    tokens_used: int | None = None

    def to_api_dict(self) -> dict:
        msg: dict = {"role": self.role, "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.call_id,
                    "type": "function",
                    "function": {"name": tc.tool_name, "arguments": str(tc.params)},
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.name:
            msg["name"] = self.name
        return msg


@dataclass
class LLMRequest:
    system: str
    messages: list[dict]
    tools: list[dict]

    def to_api_format(self) -> dict:
        return {"system": self.system, "messages": self.messages, "tools": self.tools}


class ContextBuilder:
    """Assembles six-layer context for LLM API calls."""

    L0_SYSTEM_PROMPT = """You are MyAgent, a CLI-based AI assistant powered by DeepSeek V4 Pro.
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

    def __init__(self, tool_registry, memory_store, skill_registry, config=None):
        self.tool_registry = tool_registry
        self.memory_store = memory_store
        self.skill_registry = skill_registry
        self.config = config
        # Session-scoped memory cache (gap-27): load once, reuse across turns
        self._memory_cache: dict[str, list] = {}
        self._cache_key: str | None = None

    async def build(
        self,
        current_input: str,
        history: list[Message],
        project_context: ProjectContext,
        tool_subset: list[str] | None = None,
        active_skill: str | None = None,
        goal: str | None = None,
    ) -> LLMRequest:
        # L3: Project context
        l3 = self._format_project_context(project_context)

        # L4: Relevant memories — use session-scoped cache (gap-27)
        l4 = ""
        if self.memory_store:
            try:
                # Use the initial input as cache key for the session
                cache_key = self._cache_key or current_input[:100]
                if self._memory_cache.get(cache_key) is not None:
                    memories = self._memory_cache[cache_key]
                else:
                    from myagent.memory.recall import recall
                    memories = await recall(cache_key, self.memory_store, limit=10)
                    self._memory_cache[cache_key] = memories
                    self._cache_key = cache_key
                l4 = self._format_memories(memories)
            except Exception:
                pass

        # L5: Active skill content — inject full skill instructions when invoked
        l5 = ""
        if active_skill and self.skill_registry:
            skill = self.skill_registry.get(active_skill)
            if skill:
                l5 = self._format_skill_content(skill)

        # L2: Skills index
        l2 = ""
        if self.skill_registry:
            skills = self.skill_registry.list_all()
            l2 = self._format_skills_index(skills)

        # L6: Goal context — inject current goal when in goal mode
        l6 = ""
        if goal:
            l6 = f"## Current Goal\n{goal}\n\nWork toward this goal. When you believe it is achieved, indicate completion."

        # Assemble system prompt: L0 + L3 + L4 + L5 + L2 + L6
        system_parts = [self.L0_SYSTEM_PROMPT]
        if l3:
            system_parts.append(f"## Project Context\n{l3}")
        if l4:
            system_parts.append(f"## Relevant Memories\n{l4}")
        if l5:
            system_parts.append(f"## Active Skill\n{l5}")
        if l2:
            system_parts.append(f"## Available Skills\n{l2}")
        if l6:
            system_parts.append(l6)
        system = "\n\n".join(system_parts)

        # L1: Tool schemas
        if tool_subset:
            tool_schemas = self.tool_registry.get_schemas_for(tool_subset)
        else:
            tool_schemas = self.tool_registry.get_schemas() if self.tool_registry else []

        # History + current input
        messages = [m.to_api_dict() for m in history]
        messages.append({"role": "user", "content": current_input})

        return LLMRequest(system=system, messages=messages, tools=tool_schemas)

    def _format_project_context(self, ctx: ProjectContext) -> str:
        parts = []
        if ctx.project_type != "unknown":
            parts.append(f"Project type: {ctx.project_type}")
        if ctx.is_git_repo:
            parts.append(f"Git branch: {ctx.git_branch or 'unknown'}")
            if ctx.git_status:
                parts.append(f"Git status: {ctx.git_status}")
        if ctx.structure_summary:
            parts.append(f"Structure: {ctx.structure_summary}")
        if ctx.agent_md_content:
            parts.append(f"Project guidance:\n{ctx.agent_md_content[:2000]}")
        return "\n".join(parts)

    def _format_memories(self, memories) -> str:
        if not memories:
            return ""
        lines = []
        for m in memories:
            lines.append(f"- **{m.name}**: {m.description}")
        return "\n".join(lines)

    def _format_skills_index(self, skills) -> str:
        if not skills:
            return ""
        return "\n".join(f"- `{s.name}`: {s.description}" for s in skills)

    def _format_skill_content(self, skill) -> str:
        """Format full skill content for injection as L5 context."""
        lines = [f"### {skill.name}", skill.description, ""]
        if skill.content:
            # Truncate very long skill content to avoid exceeding context window
            content = skill.content[:4000]
            if len(skill.content) > 4000:
                content += "\n... (content truncated for context window)"
            lines.append(content)
        if skill.resources:
            refs = skill.resources.references or []
            scripts = skill.resources.scripts or []
            if refs:
                lines.append("References: " + ", ".join(str(r.name) for r in refs))
            if scripts:
                lines.append("Scripts: " + ", ".join(str(s.name) for s in scripts))
        return "\n".join(lines)
