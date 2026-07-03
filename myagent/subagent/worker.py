"""Sub-agent worker — runs ReAct loop in isolation.

Each sub-agent has its own context window (same model limit as main agent),
tool subset, and transcript persistence. Skills and memory are NOT loaded
for sub-agents.

Design doc reference: §八 子 Agent 池与工作流编排
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from pathlib import Path

from myagent.llm.provider import Done as LLMDone
from myagent.llm.provider import TextDelta as LLMTextDelta
from myagent.llm.provider import ThinkingDelta as LLMThinkingDelta
from myagent.llm.provider import ToolCall as LLMToolCall
from myagent.llm.provider import LLMError
from myagent.tools.base import ToolContext, ToolResult

# Retry constants for sub-agent LLM calls (gap-8-01)
# Same strategy as LLMProvider: exponential backoff, max 3 retries, 2s-30s
_SUBAGENT_MAX_RETRIES = 3
_SUBAGENT_BASE_DELAY = 2.0
_SUBAGENT_MAX_DELAY = 30.0

logger = logging.getLogger("myagent.subagent")


def _tokenize_for_relevance(text: str) -> set[str]:
    """Tokenize text into a set of lowercase words for relevance matching.

    Splits on whitespace and punctuation, returns unique lowercase tokens
    of length >= 2. Used by _filter_project_context for keyword matching.
    """
    import re
    tokens = set()
    for word in re.split(r'[\s,;:.!?()\[\]{}"\']+', text.lower()):
        word = word.strip()
        if len(word) >= 2:
            tokens.add(word)
    # Also add bigrams for compound concepts
    words = [w for w in re.split(r'[\s,;:.!?()\[\]{}"\']+', text.lower())
             if len(w.strip()) >= 2]
    for i in range(len(words) - 1):
        tokens.add(f"{words[i]} {words[i + 1]}")
    return tokens


class SubAgentWorker:
    """Runs a sub-agent's ReAct loop with isolated context."""

    MAX_ITERATIONS = 30

    def __init__(
        self,
        prompt: str,
        tools: list[str] | None = None,
        mode: str = "Think High",
        isolation: str | None = None,
        schema: dict | None = None,
        model: str | None = None,
        llm=None,
        tool_registry=None,
        interrupt_event: asyncio.Event | None = None,
        tool_context: ToolContext | None = None,
        project_context=None,
        message_store: list | None = None,
        project_dir: Path | None = None,
        progress_callback=None,
    ):
        self.prompt = prompt
        self.tools = tools
        self.mode = mode
        self.isolation = isolation
        self.schema = schema
        self.model = model
        self.llm = llm
        self.tool_registry = tool_registry
        self.interrupt_event = interrupt_event
        self.tool_context = tool_context
        self.project_context = project_context
        self._message_store = message_store
        self._transcript_messages: list[dict] = []
        self._transcript_tool_calls: list[dict] = []
        self._project_dir = project_dir
        self._worktree_path: Path | None = None
        self._progress_callback = progress_callback
        self._current_iteration: int = 0

    async def run(self) -> str:
        """Execute the sub-agent task and return a result string.

        Runs a full ReAct loop with LLM calls and tool execution.
        Sub-agents have:
        - No L2 skills index
        - No L4 memory (avoid context pollution)
        - Tool subset from spawn params
        - Independent context (no history from parent)
        """
        # Worktree isolation (gap-14): create isolated workspace
        self._worktree_path = None
        if self.isolation == "worktree" and self._project_dir:
            self._worktree_path = await self._create_worktree()

        try:
            return await self._run_impl()
        finally:
            # Cleanup worktree if created
            if self._worktree_path:
                await self._cleanup_worktree()

    async def _run_impl(self) -> str:
        """Inner run implementation after worktree setup."""
        if not self.llm:
            logger.warning("Sub-agent spawned without LLM provider")
            return "Error: No LLM provider configured for sub-agent"

        # Build system prompt with optional project context (gap-31)
        system_content = (
            "You are a sub-agent assistant. Complete the assigned task "
            "using available tools. Be concise and direct. Report your "
            "final answer when done."
        )
        # Schema: enforce structured output format (gap-2-16)
        if self.schema:
            import json as _json
            schema_str = _json.dumps(self.schema, ensure_ascii=False)
            system_content += (
                f"\n\n## Output Format Requirement\n"
                f"Your final response MUST be valid JSON conforming to this schema:\n"
                f"```json\n{schema_str}\n```\n"
                f"Do NOT include any text outside the JSON object. "
                f"Return ONLY the JSON object."
            )
        if self.project_context:
            ctx_lines = self._filter_project_context(self.prompt, self.project_context)
            if ctx_lines:
                system_content += "\n\n## Project Context\n" + "\n".join(ctx_lines)

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": self.prompt},
        ]

        tools_schemas = (
            self.tool_registry.get_schemas_for(self.tools)
            if self.tool_registry and self.tools
            else []
        )

        iteration = 0
        while iteration < self.MAX_ITERATIONS:
            iteration += 1
            self._current_iteration = iteration

            # Report iteration progress to pool/status bar (gap-8-06)
            if self._progress_callback:
                try:
                    self._progress_callback(iteration, self.MAX_ITERATIONS)
                except Exception:
                    pass

            # Check for pending messages from the parent (gap-20)
            if self._message_store and self._message_store:
                pending_msg = self._message_store.pop(0)
                logger.info(
                    "Sub-agent received message: %s", pending_msg[:100],
                    extra={"category": "subagent"},
                )
                if pending_msg.lower() == "stop":
                    return "[Interrupted]"
                # Inject non-stop message as user message
                messages.append({
                    "role": "user",
                    "content": f"[Message from parent]: {pending_msg}",
                })

            # Check for interrupt before each LLM call
            if self.interrupt_event and self.interrupt_event.is_set():
                logger.info(
                    "Sub-agent interrupted at iteration %d",
                    iteration,
                    extra={"category": "subagent"},
                )
                return "[Interrupted]"

            text_buffer: list[str] = []
            tool_calls_in_turn: list = []

            # ── Stream LLM response with retry (gap-8-01) ───────
            stream_error = await self._stream_llm_with_retry(
                messages, tools_schemas, iteration, text_buffer, tool_calls_in_turn,
            )
            if stream_error is not None:
                return stream_error

            # ── Execute tool calls ───────────────────────────────
            if tool_calls_in_turn:
                assistant_content = "".join(text_buffer) or None
                assistant_msg: dict = {"role": "assistant", "content": assistant_content}
                built_tool_calls = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.params, ensure_ascii=False),
                        },
                    }
                    for tc in tool_calls_in_turn
                ]
                assistant_msg["tool_calls"] = built_tool_calls
                messages.append(assistant_msg)

                # G3: record assistant message in transcript
                self._transcript_messages.append({
                    "role": "assistant",
                    "content": assistant_content,
                    "tool_calls": built_tool_calls,
                })

                for tc in tool_calls_in_turn:
                    tool = (
                        self.tool_registry.get(tc.name)
                        if self.tool_registry else None
                    )
                    if tool:
                        ctx = self.tool_context or ToolContext(
                            session_id="subagent",
                            project_dir=None,
                            permissions=None,
                            config=None,
                            subagent_pool=(
                                getattr(self.tool_context, 'subagent_pool', None)
                                if self.tool_context else None
                            ),
                        )
                        try:
                            t0 = time.monotonic()
                            result = await tool.execute(tc.params, ctx)
                            duration_ms = (time.monotonic() - t0) * 1000
                            result_text = (
                                result.output
                                if not result.error
                                else f"Error: {result.error}"
                            )
                            logger.info(
                                "Tool '%s' executed in %.1fms (%d chars)",
                                tc.name,
                                duration_ms,
                                len(result.output),
                                extra={"category": "tool"},
                            )
                        except Exception as e:
                            logger.error(
                                "Tool '%s' failed: %s",
                                tc.name,
                                str(e),
                                exc_info=True,
                                extra={"category": "error", "component": "tool", "context": f"subagent_tool:{tc.name}"},
                            )
                            result_text = f"Error executing {tc.name}: {e}"
                    else:
                        result_text = f"Error: Unknown tool '{tc.name}'"

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    })

                    # G3: record tool call in transcript
                    self._transcript_tool_calls.append({
                        "tool_name": tc.name,
                        "params": tc.params,
                        "result": result_text[:5000],
                        "call_id": tc.id,
                    })

                # Loop again — LLM sees tool results in next iteration
                continue

            # ── No tool calls — text response complete ───────────
            output = "".join(text_buffer)
            if self.schema:
                output = self._validate_schema_output(output)
            return output

        return f"Error: Sub-agent reached max iterations ({self.MAX_ITERATIONS})"

    # ── helpers ────────────────────────────────────────────────────

    async def _stream_llm_with_retry(
        self,
        messages: list[dict],
        tools_schemas: list[dict],
        iteration: int,
        text_buffer: list[str],
        tool_calls_in_turn: list,
    ) -> str | None:
        """Stream LLM response with exponential backoff retry (gap-8-01).

        Retries the entire complete() call up to _SUBAGENT_MAX_RETRIES times
        on transient LLMErrors (rate_limit, connection_error, server_error,
        timeout). Fatal errors (auth_error, bad_request) fail immediately.

        Returns None on success (results in text_buffer/tool_calls_in_turn),
        or an error string on final failure.
        """
        last_error = None
        for attempt in range(_SUBAGENT_MAX_RETRIES + 1):
            try:
                async for event in self.llm.complete(
                    messages=messages,
                    tools=tools_schemas if tools_schemas else None,
                    thinking=self.mode,
                    model_override=self.model,
                ):
                    kind = self._classify_event(event)
                    if kind == "text":
                        text_buffer.append(event.content)
                    elif kind == "tool_call":
                        tool_calls_in_turn.append(event)
                    # "done", "thinking", "unknown" — absorbed
                return None  # Success
            except LLMError as e:
                last_error = e
                if not e.retryable:
                    # Fatal error — fail immediately (silent for sub-agent)
                    logger.error(
                        "Sub-agent LLM fatal error (iteration %d, attempt %d): %s",
                        iteration, attempt + 1, str(e),
                        exc_info=True,
                        extra={"category": "error", "component": "llm",
                               "context": "subagent_llm_fatal"},
                    )
                    return f"Error: LLM call failed — {e}"
                # Retryable error
                if attempt < _SUBAGENT_MAX_RETRIES:
                    delay = min(
                        _SUBAGENT_BASE_DELAY * (2 ** attempt),
                        _SUBAGENT_MAX_DELAY,
                    )
                    logger.warning(
                        "Sub-agent LLM retry %d/%d after %.1fs (iteration %d): %s",
                        attempt + 1, _SUBAGENT_MAX_RETRIES, delay,
                        iteration, str(e)[:200],
                        extra={"category": "llm", "event": "subagent_retry",
                               "retry_count": attempt + 1},
                    )
                    await asyncio.sleep(delay)
                # else: max retries exhausted, will raise below
            except Exception as e:
                # Non-LLMError — treat as non-retryable
                logger.error(
                    "Sub-agent LLM unexpected error (iteration %d): %s",
                    iteration, str(e),
                    exc_info=True,
                    extra={"category": "error", "component": "llm",
                           "context": "subagent_llm_unexpected"},
                )
                return f"Error: {e}"

        # All retries exhausted
        logger.error(
            "Sub-agent LLM retries exhausted after %d attempts (iteration %d): %s",
            _SUBAGENT_MAX_RETRIES + 1, iteration, str(last_error),
            exc_info=True,
            extra={"category": "error", "component": "llm",
                   "context": "subagent_llm_retries_exhausted"},
        )
        return f"Error: LLM call failed after {_SUBAGENT_MAX_RETRIES + 1} attempts — {last_error}"

    def _validate_schema_output(self, output: str) -> str:
        """Validate sub-agent output against the expected JSON Schema (gap-2-16).

        Attempts to parse the output as JSON, then validates against self.schema
        using jsonschema if available. Falls back to basic structural checks.
        Returns the original output wrapped with validation status if invalid.
        """
        import json as _json

        # Try to extract JSON from the output (may have surrounding text)
        stripped = output.strip()
        try:
            data = _json.loads(stripped)
        except _json.JSONDecodeError:
            # Try to find JSON object in the text
            brace_start = stripped.find("{")
            brace_end = stripped.rfind("}")
            if brace_start >= 0 and brace_end > brace_start:
                try:
                    data = _json.loads(stripped[brace_start:brace_end + 1])
                except _json.JSONDecodeError:
                    return f"{output}\n\n[Schema validation: output is not valid JSON]"
            else:
                return f"{output}\n\n[Schema validation: output is not valid JSON]"

        # Validate against schema
        try:
            import jsonschema
            jsonschema.validate(instance=data, schema=self.schema)
            # Valid — return the extracted JSON
            return _json.dumps(data, ensure_ascii=False, indent=2)
        except ImportError:
            # jsonschema not available — do basic structural check
            schema_type = self.schema.get("type", "object")
            if schema_type == "object" and not isinstance(data, dict):
                return f"{_json.dumps(data)}\n\n[Schema validation: expected object, got {type(data).__name__}]"
            if schema_type == "array" and not isinstance(data, list):
                return f"{_json.dumps(data)}\n\n[Schema validation: expected array, got {type(data).__name__}]"
            return _json.dumps(data, ensure_ascii=False, indent=2)
        except jsonschema.ValidationError as e:
            return f"{_json.dumps(data)}\n\n[Schema validation failed: {e.message}]"

    async def _create_worktree(self) -> Path | None:
        """Create a git worktree for isolated sub-agent execution (gap-14).

        Creates under .claude/worktrees/ with a unique name.
        Returns the worktree path or None on failure.
        """
        if not self._project_dir:
            return None
        try:
            worktrees_dir = self._project_dir / ".claude" / "worktrees"
            worktrees_dir.mkdir(parents=True, exist_ok=True)
            suffix = secrets.token_hex(4)
            worktree_name = f"subagent-{suffix}"
            worktree_path = worktrees_dir / worktree_name

            # Check if this is a git repo
            git_dir = self._project_dir / ".git"
            if not git_dir.exists():
                logger.debug("Worktree isolation skipped: not a git repo")
                return None

            # Create the worktree using git
            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "add", str(worktree_path),
                "--detach",
                cwd=str(self._project_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(
                    "Failed to create worktree: %s",
                    stderr.decode("utf-8", errors="replace")[:200],
                )
                return None

            logger.info(
                "Created worktree for sub-agent at %s",
                worktree_path,
                extra={"category": "subagent", "event": "worktree_created"},
            )
            return worktree_path
        except Exception as e:
            logger.warning("Failed to create worktree: %s", e)
            return None

    async def _cleanup_worktree(self) -> None:
        """Remove the git worktree created for this sub-agent (gap-14)."""
        if not self._worktree_path or not self._project_dir:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "remove", str(self._worktree_path),
                "--force",
                cwd=str(self._project_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            logger.debug("Cleaned up worktree: %s", self._worktree_path)
        except Exception as e:
            logger.warning("Failed to cleanup worktree %s: %s", self._worktree_path, e)

    @staticmethod
    def _filter_project_context(prompt: str, project_context) -> list[str]:
        """Filter project context fields by relevance to the sub-task prompt.

        Design spec §三: '按需传递（仅传递与子任务相关的项目上下文）'.

        Each project context field has associated relevance keywords. A field
        is included only if the prompt contains at least one of its keywords
        OR is a universally relevant field (project_type is always included).
        """
        pc = project_context
        lines: list[str] = []
        prompt_lower = prompt.lower()

        # Field definitions: (field_name, getter, relevance_keywords, universal)
        fields = [
            # project_type is universally relevant — always include
            ("project_type",
             lambda: f"Project type: {pc.project_type}",
             set(), True),
            ("git_branch",
             lambda: f"Git branch: {getattr(pc, 'git_branch', 'unknown')}",
             {"git", "branch", "commit", "merge", "rebase", "pull",
              "push", "checkout", "diff", "log", "stash", "tag",
              "版本", "分支", "提交", "合并", "git"},
             False),
            ("git_status",
             lambda: f"Git status: {getattr(pc, 'git_status', '')}",
             {"git", "status", "commit", "modified", "staged", "unstaged",
              "diff", "change", "更改", "修改", "变更"},
             False),
            ("structure_summary",
             lambda: f"Structure: {pc.structure_summary}",
             {"structure", "directory", "folder", "layout", "tree",
              "file", "path", "project layout", "目录", "结构", "文件",
              "src", "tests", "docs", "lib", "package"},
             False),
            ("test_framework",
             lambda: f"Test framework: {getattr(pc, 'test_framework', 'pytest')}",
             {"test", "pytest", "unittest", "spec", "coverage",
              "测试", "单元测试", "用例", "mock"},
             False),
            ("package_manager",
             lambda: f"Package manager: {getattr(pc, 'package_manager', 'pip')}",
             {"pip", "npm", "yarn", "pnpm", "poetry", "uv", "install",
              "dependency", "package", "依赖", "包"},
             False),
            ("linter",
             lambda: f"Linter: {getattr(pc, 'linter', 'ruff')}",
             {"lint", "ruff", "flake8", "eslint", "pylint", "style",
              "format", "代码风格", "代码检查"},
             False),
            ("build_system",
             lambda: f"Build system: {getattr(pc, 'build_system', 'make')}",
             {"build", "make", "cmake", "compile", "构建", "编译",
              "setup", "install"},
             False),
            ("python_version",
             lambda: f"Python version: {getattr(pc, 'python_version', '3.12')}",
             {"python", "py", "version", "3.", "版本"},
             False),
        ]

        # Conditional: has is_git_repo + git_branch + git_status
        is_git = getattr(pc, 'is_git_repo', False) if hasattr(pc, 'is_git_repo') else False

        for field_name, getter, keywords, universal in fields:
            # Skip fields that require git if not a git repo
            if field_name in ("git_branch", "git_status") and not is_git:
                continue
            # Skip fields with no value
            if field_name == "project_type":
                if getattr(pc, 'project_type', 'unknown') == "unknown":
                    continue
            if field_name == "structure_summary":
                if not getattr(pc, 'structure_summary', ''):
                    continue
            # Universal fields always included
            if universal:
                lines.append(getter())
                continue
            # Check keyword relevance
            if keywords & _tokenize_for_relevance(prompt_lower):
                lines.append(getter())

        return lines

    def _classify_event(self, event) -> str:
        """Classify an LLM stream event by type.

        Uses isinstance against provider types; falls back to duck-typing
        for test doubles (mock objects with matching attributes).
        """
        if isinstance(event, LLMTextDelta):
            return "text"
        if isinstance(event, LLMThinkingDelta):
            return "thinking"
        if isinstance(event, LLMToolCall):
            return "tool_call"
        if isinstance(event, LLMDone):
            return "done"

        # Duck-typing fallback for test doubles
        if hasattr(event, "name") and hasattr(event, "params") and hasattr(event, "id"):
            return "tool_call"
        if hasattr(event, "reasoning_content"):
            return "thinking"
        if hasattr(event, "content") and not hasattr(event, "name"):
            return "text"
        if hasattr(event, "stop_reason"):
            return "done"
        if "Done" in type(event).__name__:
            return "done"
        return "unknown"
