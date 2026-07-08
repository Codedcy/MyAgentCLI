"""Context builder — assembles L0-L6 layers into LLM API request."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from myagent.agent.project import ProjectContext

logger = logging.getLogger("myagent.context.builder")


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

    _MEMORY_CONTENT_LIMIT = 1200

    # Base L0 system prompt template. {model_description} is dynamically
    # populated at build time from the configured model name.
    # (gap-17-05: replaced static hardcoded model name with dynamic template)
    # Note: all literal curly braces in the template must be doubled
    # so that str.format() processes only {model_description}.
    _L0_TEMPLATE = """You are MyAgent, a CLI-based AI assistant powered by {model_description}.
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
- Be thorough but concise. Verify your work before claiming completion.

## Intent Signaling
When you determine that the user's latest message expresses an intent to interrupt
or redirect your current work, signal this by prefixing your response with one of
these structured markers on its own line:

  [INTENT: stop]    — User wants you to halt the current operation immediately.
  [INTENT: correct] — User wants to correct your approach or redirect your work.
  [INTENT: insert]  — User wants to insert a new sub-task before continuing.
  [INTENT: continue] — User wants you to resume/continue after an interruption.

Use these markers ONLY when the user's message clearly expresses the corresponding
intent. Do NOT use them for routine conversational transitions. The marker must be
the very first line of your response, followed by your natural language reply.

You may also use a virtual tool call `skill_invoke` to activate a skill from the
Available Skills list (see below). Emit `tool_call(name="skill_invoke",
params={{{{"skill"}}: "<name>"}})` when you determine a listed skill matches the current
task. This tool call is intercepted by the engine and does not count against your
tool usage limit."""

    def __init__(self, tool_registry, memory_store, skill_registry, config=None):
        self.tool_registry = tool_registry
        self.memory_store = memory_store
        self.skill_registry = skill_registry
        self.config = config
        # Session-scoped memory cache (gap-27, gap-r6-06):
        # - Caches recall results to avoid repeated semantic searches
        # - Detects topic drift by comparing current input to cached key
        # - Auto-expires after DRIFT_TURN_LIMIT turns to ensure freshness
        self._memory_cache: dict[str, list] = {}
        self._cache_key: str | None = None
        self._turn_count_since_refresh: int = 0
        self._recent_inputs: list[str] = []  # sliding window for drift detection

    def _build_l0_system_prompt(self) -> str:
        """Build the L0 system prompt with the configured model name (gap-17-05).

        Dynamically populates the model description from the active config.
        Uses the provider and model name if available, otherwise falls back
        to a generic phrase. This ensures the system prompt never claims a
        model identity that does not match the actual configured model.
        """
        model_desc = "a large language model"
        if self.config is not None:
            try:
                model_cfg = getattr(self.config, "model", None)
                if model_cfg is not None:
                    provider = getattr(model_cfg, "provider", None) or ""
                    model = getattr(model_cfg, "model", None) or ""
                    if provider and model:
                        # Build a human-readable model description
                        # e.g. "DeepSeek V4 Pro", "GPT-4o", "Claude 3.5 Sonnet"
                        if "-" in model and provider.lower() in model.lower():
                            # Model string already contains provider (e.g. "deepseek-v4-pro")
                            # Convert to title case with spaces
                            parts = model.replace("-", " ").split()
                            model_desc = " ".join(
                                p.capitalize() if p.lower() != "pro" else "Pro"
                                for p in parts
                            )
                        else:
                            model_desc = f"{provider}/{model}"
            except Exception:
                logger.exception(
                    "Failed to build dynamic model description",
                    extra={
                        "category": "error",
                        "component": "agent",
                        "context": "context_l0_model_description",
                    },
                )
        return self._L0_TEMPLATE.format(model_description=model_desc)

    # Number of turns after which the cache auto-refreshes regardless of drift
    _CACHE_TURN_LIMIT = 20
    # Minimum keyword overlap ratio to consider the topic unchanged
    _DRIFT_OVERLAP_THRESHOLD = 0.30

    @staticmethod
    def _tokenize_for_cache(text: str) -> set[str]:
        """Extract significant lowercase words from text for cache/drift logic."""
        import re
        tokens = set()
        for word in re.split(r'[\s,;:.!?()\[\]{}"\']+', text.lower()):
            word = word.strip()
            # Skip stop words and very short tokens
            if len(word) < 3:
                continue
            if word in {'the', 'and', 'for', 'you', 'can', 'that', 'this',
                        'with', 'have', 'from', 'are', 'not', 'but', 'all',
                        'was', 'has', 'had', 'its', 'his', 'her', 'our',
                        'will', 'would', 'could', 'should', 'been', 'being',
                        'did', 'does', 'just', 'like', 'than', 'then', 'also',
                        'into', 'over', 'such', 'only', 'very', 'much', 'some',
                        '这些', '那些', '这个', '那个', '什么', '怎么', '为什么',
                        'when', 'where', 'what', 'which', 'about', '他们',
                        '我们', '你们', '它们', '因为', '所以', '但是', '虽然',
                        '已经', '可以', '需要', '应该', '可能', '或者', '以及'}:
                continue
            tokens.add(word)
        return tokens

    @staticmethod
    def _compute_cache_key(query: str) -> str:
        """Compute a stable cache key from significant keywords in the query.

        gap-20-09: Uses a content hash instead of raw query[:100] prefix.
        Extracts significant keywords, sorts them for determinism, then
        computes a SHA256 hash prefix. This avoids false cache hits when
        two different inputs share the same first 100 characters.
        """
        import hashlib
        tokens = sorted(ContextBuilder._tokenize_for_cache(query))
        # Use the sorted, de-duplicated keyword set as input to hash
        key_material = "|".join(tokens) if tokens else query[:100]
        return hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:16]

    def _detect_topic_drift(self, current_input: str) -> bool:
        """Check if the current input represents a topic change from the cache key.

        Uses keyword overlap ratio: if less than _DRIFT_OVERLAP_THRESHOLD of
        significant words from the current input overlap with the cached key's
        significant words, consider it a topic drift.
        """
        if not self._cache_key:
            return True

        current_tokens = self._tokenize_for_cache(current_input)
        cached_tokens = self._tokenize_for_cache(self._cache_key)

        if not current_tokens:
            return False

        overlap = len(current_tokens & cached_tokens)
        ratio = overlap / len(current_tokens) if current_tokens else 0
        return ratio < self._DRIFT_OVERLAP_THRESHOLD

    async def build(
        self,
        current_input: str,
        history: list[Message],
        project_context: ProjectContext,
        tool_subset: list[str] | None = None,
        active_skill: str | None = None,
        goal: str | None = None,
    ) -> LLMRequest:
        # L3: Project context (spec §三 六层模型 L3)
        l3 = self._format_project_context(project_context)

        # L4: Relevant memories — session-scoped cache with drift detection (gap-r6-06)
        # (spec §三 六层模型 L4, §六 记忆生命周期)
        l4 = ""
        if self.memory_store:
            try:
                # Track recent inputs for drift detection
                self._recent_inputs.append(current_input[:200])
                if len(self._recent_inputs) > 5:
                    self._recent_inputs = self._recent_inputs[-5:]

                self._turn_count_since_refresh += 1
                # Condition 1: No cache yet — initial load
                should_refresh = (
                    self._cache_key is None
                    or self._turn_count_since_refresh >= self._CACHE_TURN_LIMIT
                    or self._detect_topic_drift(current_input)
                )

                if should_refresh:
                    query = current_input[:200]
                    from myagent.memory.recall import recall
                    memories = await recall(query, self.memory_store, limit=10)
                    # gap-20-09: Use a hash-based cache key instead of raw
                    # query[:100] prefix. This prevents false cache hits when
                    # two different inputs share the same first 100 characters
                    # but diverge in topic thereafter.
                    cache_key = self._compute_cache_key(current_input)
                    self._memory_cache[cache_key] = memories
                    self._cache_key = cache_key
                    self._turn_count_since_refresh = 0
                else:
                    memories = self._memory_cache.get(self._cache_key, [])

                l4 = self._format_memories(memories)
            except Exception:
                logger.exception(
                    "Failed to load memory context layer",
                    extra={
                        "category": "error",
                        "component": "memory",
                        "context": "context_l4_memory_layer",
                    },
                )

        # Active skill content — full skill instructions injected into system prompt
        # when a skill is invoked (not a context layer; injected alongside L2)
        skill_content = ""
        if active_skill and self.skill_registry:
            skill = self.skill_registry.get(active_skill)
            if skill:
                skill_content = self._format_skill_content(skill)

        # L2: Skills index (spec §三 六层模型 L2 — name + description only)
        l2 = ""
        if self.skill_registry:
            skills = self.skill_registry.list_all()
            l2 = self._format_skills_index(skills)

        # Goal context — inject current goal when in goal mode
        # (not a context layer; injected into system prompt alongside L0)
        goal_context = ""
        if goal:
            goal_context = (
                f"## Current Goal\n{goal}\n\n"
                "Work toward this goal. When you believe it is achieved, "
                "indicate completion."
            )

        # Assemble system prompt: L0 + L3 + L4 + skill_content + L2 + goal_context
        # (spec §三: L0=system prompt, L3=project, L4=memory, L2=skills index;
        #  skill_content and goal_context are not layers but injected here)
        system_parts = [self._build_l0_system_prompt()]
        if l3:
            system_parts.append(f"## Project Context\n{l3}")
        if l4:
            system_parts.append(f"## Relevant Memories\n{l4}")
        if skill_content:
            system_parts.append(f"## Active Skill\n{skill_content}")
        if l2:
            system_parts.append(f"## Available Skills\n{l2}")
        # G10: MCP resources and prompts — expose as reference information
        mcp_ref = self._format_mcp_refs()
        if mcp_ref:
            system_parts.append(mcp_ref)
        if goal_context:
            system_parts.append(goal_context)
        system = "\n\n".join(system_parts)

        # L1: Tool schemas
        if tool_subset:
            tool_schemas = self.tool_registry.get_schemas_for(tool_subset)
        else:
            tool_schemas = self.tool_registry.get_schemas() if self.tool_registry else []

        # History + current input
        messages = [
            m.to_api_dict()
            for m in history
            if getattr(m, "role", None) != "system"
        ]
        messages.append({"role": "user", "content": current_input})

        return LLMRequest(system=system, messages=messages, tools=tool_schemas)

    def _format_project_context(self, ctx: ProjectContext) -> str:
        parts = []
        # Core project identity
        if ctx.project_type != "unknown":
            parts.append(f"Project type: {ctx.project_type}")
        # Detected environment (spec §十 环境感知, gap-15-01)
        if ctx.package_manager:
            parts.append(f"Package manager: {ctx.package_manager}")
        if ctx.python_version:
            parts.append(f"Python version: {ctx.python_version}")
        if ctx.build_system:
            parts.append(f"Build system: {ctx.build_system}")
        if ctx.test_framework:
            parts.append(f"Test framework: {ctx.test_framework}")
        if ctx.linter:
            parts.append(f"Linter: {ctx.linter}")
        # Git info
        if ctx.is_git_repo:
            parts.append(f"Git branch: {ctx.git_branch or 'unknown'}")
            if ctx.git_status:
                parts.append(f"Git status: {ctx.git_status}")
        # Directory structure
        if ctx.structure_summary:
            parts.append(f"Structure: {ctx.structure_summary}")
        # Project guidance files
        if ctx.agent_md_content:
            parts.append(f"Project guidance:\n{ctx.agent_md_content[:2000]}")
        return "\n".join(parts)

    def _format_memories(self, memories) -> str:
        if not memories:
            return ""
        lines = []
        for m in memories:
            description = f": {m.description}" if m.description else ""
            lines.append(f"- **{m.name}**{description}")
            excerpt = self._memory_excerpt(m.content)
            if excerpt:
                lines.append("  Content:")
                for line in excerpt.splitlines():
                    lines.append(f"  {line}" if line else "  ")
        return "\n".join(lines)

    def _memory_excerpt(self, content: str) -> str:
        text = content.strip()
        if not text:
            return ""
        if len(text) > self._MEMORY_CONTENT_LIMIT:
            text = text[:self._MEMORY_CONTENT_LIMIT].rstrip() + "\n..."
        return text

    def _format_skills_index(self, skills) -> str:
        if not skills:
            return ""
        lines = [
            "To activate a skill, use the virtual tool call: "
            "`tool_call(name=\"skill_invoke\", params={\"skill\": \"<name>\"})`.",
            "",
        ]
        lines.extend(f"- `{s.name}`: {s.description}" for s in skills)
        return "\n".join(lines)

    def _format_skill_content(self, skill) -> str:
        """Format full skill content for injection into the system prompt.

        Per spec §七: "加载完整 SKILL.md 注入 system prompt" — the full
        SKILL.md content is loaded when a skill is invoked. Context window
        management is handled by the four-layer progressive compression
        system at higher layers (spec §三).

        Per spec §七 "脚本和 references 的处理方式":
        - References are listed with full paths so the agent can read them on demand.
        - Scripts are listed with full paths so the agent can execute them via bash.
        - Templates and assets are also listed.
        """
        lines = [f"### {skill.name}", skill.description, ""]
        if skill.base_dir:
            lines.append(f"Skill directory: {skill.base_dir}")
            lines.append("")
        if skill.content:
            lines.append(skill.content)
        if skill.resources:
            refs = skill.resources.references or []
            scripts = skill.resources.scripts or []
            templates = skill.resources.templates or []
            assets = skill.resources.assets or []
            if refs:
                ref_list = ", ".join(str(r) for r in refs)
                lines.append(f"\n**Available references** (read on demand): {ref_list}")
            if scripts:
                script_list = ", ".join(str(s) for s in scripts)
                lines.append(f"\n**Available scripts** (execute via bash): {script_list}")
            if templates:
                tmpl_list = ", ".join(str(t) for t in templates)
                lines.append(f"\n**Available templates** (read and fill): {tmpl_list}")
            if assets:
                asset_list = ", ".join(str(a) for a in assets)
                lines.append(f"\n**Available assets** (read on demand): {asset_list}")
        return "\n".join(lines)

    def _format_mcp_refs(self) -> str:
        """G10: Format MCP resources and prompts as reference information.

        Provides the LLM with awareness of available MCP resources (which
        can be read as data) and prompt templates (which can be invoked).
        Limited to 2000 chars total to avoid bloating the system prompt.
        """
        if not self.tool_registry:
            return ""

        resources = getattr(self.tool_registry, 'mcp_resources', []) or []
        prompts = getattr(self.tool_registry, 'mcp_prompts', []) or []
        if not resources and not prompts:
            return ""

        lines = ["## MCP Reference"]
        max_len = 2000
        current_len = len(lines[0]) + 2  # +2 for newline

        if resources:
            lines.append("### Available Resources")
            current_len += len(lines[-1]) + 1
            for r in resources:
                uri = r.get("uri", r.get("name", "unknown"))
                name = r.get("name", uri)
                desc = r.get("description", "")[:100]
                entry = f"- `{name}`: {desc}" if desc else f"- `{name}`"
                if current_len + len(entry) > max_len:
                    lines.append(f"- ... and {len(resources) - resources.index(r)} more")
                    break
                lines.append(entry)
                current_len += len(entry) + 1

        if prompts:
            lines.append("### Available Prompts")
            current_len += len(lines[-1]) + 1
            for p in prompts:
                name = p.get("name", "unknown")
                desc = p.get("description", "")[:100]
                entry = f"- `{name}`: {desc}" if desc else f"- `{name}`"
                if current_len + len(entry) > max_len:
                    lines.append(f"- ... and {len(prompts) - prompts.index(p)} more")
                    break
                lines.append(entry)
                current_len += len(entry) + 1

        return "\n".join(lines)
