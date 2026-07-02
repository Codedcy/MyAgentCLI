"""LiteLLM async provider wrapper.

Wraps litellm.acompletion with streaming, thinking mode mapping,
error handling, and retry logic.

Design doc reference: §二 核心 Agent 循环 — LLM Error Handling
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal

import litellm


# ── Event types emitted by the provider ─────────────────────────


@dataclass
class Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass
class TextDelta:
    content: str


@dataclass
class ThinkingDelta:
    content: str


@dataclass
class ToolCall:
    id: str
    name: str
    params: dict


@dataclass
class Done:
    stop_reason: str
    usage: Usage | None = None


# Union type for consumers to pattern-match against
LLMEvent = TextDelta | ThinkingDelta | ToolCall | Done


# ── Error type ──────────────────────────────────────────────────


@dataclass
class LLMError(Exception):
    """Wrapped LLM error with retry information."""

    code: str
    message: str
    retryable: bool = False

    def __str__(self):
        return f"LLMError({self.code}): {self.message}"


# ── Retry configuration ─────────────────────────────────────────

MAX_RETRIES = 3
BASE_DELAY = 2.0  # seconds
MAX_DELAY = 30.0  # seconds

RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}


# ── Provider ────────────────────────────────────────────────────


class LLMProvider:
    """Async wrapper around LiteLLM for streaming completions.

    Usage:
        provider = LLMProvider(model_config)
        async for event in provider.complete(messages, tools, thinking):
            match event:
                case TextDelta(content): ...
                case ThinkingDelta(content): ...
                case ToolCall(id, name, params): ...
                case Done(stop_reason, usage): ...
    """

    def __init__(self, model_config=None):
        """Initialize with ModelConfig (or None for defaults).

        Args:
            model_config: ModelConfig dataclass from myagent.config.schema.
                          If None, uses deepseek-v4-pro defaults.
        """
        if model_config is None:
            self.provider = "deepseek"
            self.model = "deepseek/deepseek-v4-pro"
        else:
            self.provider = model_config.provider
            self.model = f"{model_config.provider}/{model_config.model}"

    # ── public API ─────────────────────────────────────────────

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        thinking: str = "Think High",
    ) -> AsyncIterator[LLMEvent]:
        """Stream completion from the LLM.

        Args:
            messages: Conversation history in OpenAI format.
            tools: Tool definitions for function calling.
            thinking: Thinking mode — "Think High", "Think Max", or "Non-think".

        Yields:
            LLMEvent instances: TextDelta, ThinkingDelta, ToolCall, Done.

        Raises:
            LLMError: On fatal errors after retries exhausted.
        """
        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                thinking_param = self._build_thinking_param(thinking)

                # litellm doesn't accept 'thinking' directly — pass as
                # extra_body for DeepSeek or via the provider-specific mechanism
                kwargs = {
                    "model": self.model,
                    "messages": messages,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                }

                if tools:
                    kwargs["tools"] = tools

                # Pass thinking parameter via litellm's extra_body for DeepSeek
                if "deepseek" in self.model.lower():
                    kwargs["extra_body"] = {"thinking": thinking_param}

                response = await litellm.acompletion(**kwargs)

                async for event in self._stream_response(response):
                    yield event
                return

            except litellm.exceptions.RateLimitError as e:
                last_error = LLMError(
                    code="rate_limit",
                    message=str(e),
                    retryable=True,
                )
            except litellm.exceptions.APIConnectionError as e:
                last_error = LLMError(
                    code="connection_error",
                    message=str(e),
                    retryable=True,
                )
            except litellm.exceptions.InternalServerError as e:
                last_error = LLMError(
                    code="server_error",
                    message=str(e),
                    retryable=True,
                )
            except litellm.exceptions.AuthenticationError as e:
                raise LLMError(
                    code="auth_error",
                    message=str(e),
                    retryable=False,
                )
            except litellm.exceptions.BadRequestError as e:
                raise LLMError(
                    code="bad_request",
                    message=str(e),
                    retryable=False,
                )
            except litellm.exceptions.APIError as e:
                # Check HTTP status code for retryability
                status = getattr(e, "status_code", None)
                if status in RETRYABLE_HTTP_CODES:
                    last_error = LLMError(
                        code=f"api_error_{status}",
                        message=str(e),
                        retryable=True,
                    )
                else:
                    raise LLMError(
                        code=f"api_error_{status}",
                        message=str(e),
                        retryable=getattr(e, "status_code", 0) >= 500,
                    )
            except (asyncio.TimeoutError, TimeoutError) as e:
                last_error = LLMError(
                    code="timeout",
                    message=str(e),
                    retryable=True,
                )
            except Exception as e:
                raise LLMError(
                    code="unknown",
                    message=str(e),
                    retryable=False,
                )

            # Exponential backoff before retry
            if attempt < MAX_RETRIES:
                delay = min(BASE_DELAY * (2**attempt), MAX_DELAY)
                await asyncio.sleep(delay)

        # Exhausted all retries
        raise last_error or LLMError(
            code="max_retries",
            message="Max retries exhausted",
            retryable=False,
        )

    def token_count(self, messages: list[dict]) -> int:
        """Estimate token count for a list of messages.

        Uses litellm.token_counter if available, falls back to
        character-based estimate.
        """
        try:
            return litellm.token_counter(model=self.model, messages=messages)
        except Exception:
            # Fallback: ~4 chars per token (rough estimate)
            text = json.dumps(messages, ensure_ascii=False)
            return len(text) // 4

    # ── internal methods ───────────────────────────────────────

    def _build_thinking_param(self, thinking: str) -> dict:
        """Map user-facing thinking mode to DeepSeek API parameter."""
        if thinking == "Think Max":
            return {"type": "enabled", "budget_tokens": 32000}
        elif thinking == "Non-think":
            return {"type": "disabled"}
        else:  # Think High (default)
            return {"type": "enabled"}

    async def _stream_response(self, response) -> AsyncIterator[LLMEvent]:
        """Process streaming response into LLMEvent instances."""
        tool_call_buffers: dict[int, dict] = {}  # index → {id, name, args_str}

        async for chunk in response:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            # Check for thinking/reasoning content
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield ThinkingDelta(content=reasoning)

            # Check for text content
            content = getattr(delta, "content", None)
            if content:
                yield TextDelta(content=content)

            # Check for tool calls
            tool_calls = getattr(delta, "tool_calls", None)
            if tool_calls:
                for tc in tool_calls:
                    idx = tc.index or 0
                    if idx not in tool_call_buffers:
                        tool_call_buffers[idx] = {
                            "id": tc.id or "",
                            "name": "",
                            "args_str": "",
                        }
                    buf = tool_call_buffers[idx]
                    if tc.id:
                        buf["id"] = tc.id
                    func = getattr(tc, "function", None)
                    if func:
                        if func.name:
                            buf["name"] = func.name
                        if func.arguments:
                            buf["args_str"] += func.arguments

                    # If we have complete info, emit and clear
                    if buf["args_str"] and buf["name"]:
                        try:
                            params = json.loads(buf["args_str"])
                        except json.JSONDecodeError:
                            params = {}
                        yield ToolCall(id=buf["id"], name=buf["name"], params=params)
                        buf["args_str"] = ""

            # Check for usage in final chunk
            usage = getattr(chunk, "usage", None)
            if usage:
                yield Done(
                    stop_reason="end_turn",
                    usage=Usage(
                        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                        total_tokens=getattr(usage, "total_tokens", 0) or 0,
                    ),
                )

    # Allow setting model manually (for testing)
    def _set_test_model(self, model: str):
        self.model = model
