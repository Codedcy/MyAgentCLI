"""LiteLLM async provider wrapper.

Wraps litellm.acompletion with streaming, thinking mode mapping,
error handling, retry logic (delegated to LiteLLM built-in retry mechanism),
request/response logging, and fallback model support.

Design doc reference: §二 核心 Agent 循环 — LLM Error Handling
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import AsyncIterator, Any

import litellm

logger = logging.getLogger("myagent.llm")


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


# ── Retry configuration (spec §二: 指数退避重试) ────────────────

# Maximum number of retries for LLM API calls (spec §二: 最多 3 次)
MAX_RETRIES = 3
# Base delay for exponential backoff in seconds (spec §二: 初始间隔 2s)
RETRY_BASE_DELAY = 2.0
# Maximum delay for exponential backoff in seconds (spec §二: 上限 30s)
RETRY_MAX_DELAY = 30.0

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

    def __init__(self, model_config=None, logging_config=None, retry_callback=None, streaming: bool = True):
        """Initialize with ModelConfig (or None for defaults).

        Args:
            model_config: ModelConfig dataclass from myagent.config.schema.
                          If None, uses deepseek-v4-pro defaults.
            logging_config: LoggingConfig for prompt logging (gap-10).
            retry_callback: Optional callable(attempt, max_retries, delay) for UI
                            retry progress updates (gap-33). When using LiteLLM's
                            built-in retry, this is called via litellm's failure
                            hook on each retry attempt.
            streaming: Whether to stream responses (default True). When False,
                       the full response is collected and emitted as a single
                       TextDelta (G5).
        """
        if model_config is None:
            self.provider = "deepseek"
            self.model = "deepseek/deepseek-v4-pro"
            self._fallback_models: list[str] = []
        else:
            self.provider = model_config.provider
            self.model = f"{model_config.provider}/{model_config.model}"
            self._fallback_models = getattr(model_config, "fallback_models", []) or []

        self._current_fallback_index = -1  # -1 = primary model
        self._logging_config = logging_config
        self._retry_callback = retry_callback
        self._session_id: str | None = None
        self._prompt_counter: int = 0
        self._streaming = streaming

        # gap-20-02: Implement our own retry loop with the exact spec-mandated
        # backoff parameters (2s base, 30s max, 3 retries). We disable litellm's
        # built-in retry (num_retries=0) and handle retries in _complete_with_model.
        # This satisfies both spec requirements: "指数退避重试，最多 3 次，初始间隔
        # 2s，上限 30s" AND uses LiteLLM for the actual API calls.
        # We still use litellm's failure_callback hook for UI progress updates.
        litellm.num_retries = 0  # Disable litellm built-in retry — we retry ourselves

        # Register failure hook with litellm for UI progress updates on retries (gap-33).
        # Even with num_retries=0, litellm calls failure_callback when it raises
        # retryable exceptions, allowing the status bar to show retry progress.
        if self._retry_callback:
            self._register_litellm_failure_hook()

    def set_session_id(self, session_id: str) -> None:
        """Set session ID for prompt log file naming."""
        self._session_id = session_id

    # ── public API ─────────────────────────────────────────────

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        thinking: str = "Think High",
        model_override: str | None = None,
    ) -> AsyncIterator[LLMEvent]:
        """Stream completion from the LLM.

        Args:
            messages: Conversation history in OpenAI format.
            tools: Tool definitions for function calling.
            thinking: Thinking mode — "Think High", "Think Max", or "Non-think".
            model_override: If provided, use this model instead of the
                            configured primary model. No fallbacks are tried
                            when using an override. Used by sub-agents to
                            switch models per-invocation.

        Yields:
            LLMEvent instances: TextDelta, ThinkingDelta, ToolCall, Done.

        Raises:
            LLMError: On fatal errors after retries exhausted (including all fallbacks).
        """
        estimated_tokens = self.token_count(messages)

        # Use model_override if provided (no fallbacks for overrides)
        if model_override:
            models_to_try = [model_override]
        else:
            fallback_models = getattr(self, "_fallback_models", None) or []
            models_to_try = [self.model] + fallback_models
        last_error = None

        for model_idx, model_name in enumerate(models_to_try):
            self._current_fallback_index = model_idx - 1  # -1 for primary

            if model_idx > 0:
                logger.warning(
                    "Falling back to model: %s (attempt %d of %d)",
                    model_name, model_idx, len(models_to_try) - 1,
                    extra={
                        "category": "llm",
                        "event": "fallback",
                        "model": model_name,
                        "fallback_index": model_idx,
                    },
                )

            try:
                async for event in self._complete_with_model(
                    model_name, messages, tools, thinking, estimated_tokens
                ):
                    yield event
                # Success — reset fallback index for next call
                self._current_fallback_index = -1
                return
            except LLMError as e:
                last_error = e
                if not e.retryable:
                    # Non-retryable error — try next fallback
                    logger.warning(
                        "Non-retryable error on model %s: %s — trying fallback",
                        model_name, str(e),
                        extra={"category": "error", "component": "llm"},
                    )
                    continue
                else:
                    # Retryable error — already retried in _complete_with_model,
                    # so now try fallback
                    logger.warning(
                        "Retryable error exhausted on model %s: %s — trying fallback",
                        model_name, str(e),
                        extra={"category": "error", "component": "llm"},
                    )
                    continue

        # All models exhausted
        raise last_error or LLMError(
            code="all_models_exhausted",
            message="All models (primary + fallbacks) exhausted",
            retryable=False,
        )

    def _register_litellm_failure_hook(self) -> None:
        """Register the retry callback via LiteLLM's failure_callback hook (gap-33).

        LiteLLM's built-in retry loop calls failure_callback on each failed
        attempt before retrying. We hook into this to update the status bar
        with retry progress. Since litellm.failure_callback is a global list,
        we create a closure that captures self and appends to the list.
        The hook is cleaned up in _deregister_litellm_failure_hook.
        """
        if self._retry_callback is None:
            return

        provider_self = self

        def _on_litellm_failure(kwargs, completion_response, start_time, end_time):
            """Called by litellm on each failed retry attempt."""
            try:
                count = getattr(provider_self, '_failure_count', 0) + 1
                provider_self._failure_count = count
                if provider_self._retry_callback:
                    provider_self._retry_callback(count, MAX_RETRIES, 0)
            except Exception:
                pass

        # Initialize the litellm failure_callback list if needed
        if not hasattr(litellm, 'failure_callback') or litellm.failure_callback is None:
            litellm.failure_callback = []
        litellm.failure_callback.append(_on_litellm_failure)
        self._litellm_failure_hook = _on_litellm_failure

        logger.debug(
            "Retry callback registered via litellm failure_callback hook",
            extra={"category": "llm"},
        )

    def _reset_failure_count(self) -> None:
        """Reset the per-call failure counter for retry progress tracking."""
        self._failure_count: int = 0

    async def _complete_with_model(
        self,
        model_name: str,
        messages: list[dict],
        tools: list[dict] | None,
        thinking: str,
        estimated_tokens: int,
    ) -> AsyncIterator[LLMEvent]:
        """Internal: try completion with a specific model.

        Retries are handled by our own retry loop with the exact spec-mandated
        exponential backoff (2s base, 30s max, 3 retries). litellm's built-in
        retry is disabled (num_retries=0) so we control the backoff precisely.

        Non-retryable errors (auth, bad request) fail immediately and raise
        LLMError with retryable=False, allowing the caller to try fallback models.

        The retry loop wraps the litellm.acompletion() call only — once a
        response is received, streaming proceeds without retry (stream
        interruptions are handled by the caller).
        """
        self._reset_failure_count()

        # ── Build kwargs once (they don't change between retries) ──
        thinking_param = self._build_thinking_param(thinking)

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "stream": self._streaming,
            "num_retries": 0,  # gap-20-02: disable litellm internal retry
        }
        if self._streaming:
            kwargs["stream_options"] = {"include_usage": True}
        if tools:
            kwargs["tools"] = tools
        if "deepseek" in model_name.lower():
            kwargs["extra_body"] = {"thinking": thinking_param}

        # Log request (before any retry attempt)
        t0 = time.monotonic()
        logger.info(
            "LLM request: model=%s messages=%d tokens_est=%d tools=%d stream=%s",
            model_name, len(messages), estimated_tokens,
            len(tools) if tools else 0, self._streaming,
            extra={
                "category": "llm",
                "event": "request",
                "model": model_name,
                "thinking_mode": thinking,
                "messages_count": len(messages),
                "estimated_tokens": estimated_tokens,
                "tools_count": len(tools) if tools else 0,
                "stream": self._streaming,
            },
        )
        self._write_prompt_logs(model_name, messages, tools, 0)

        # ── Retry loop with spec-mandated exponential backoff (gap-20-02) ──
        # Parameters: 3 retries, 2s base delay, 30s max delay (§二 核心 Agent 循环)
        last_error = None
        for attempt in range(MAX_RETRIES + 1):  # 0, 1, 2, 3 (initial + 3 retries)
            try:
                response = await litellm.acompletion(**kwargs)
                break  # Success — exit retry loop
            except litellm.exceptions.AuthenticationError as e:
                raise LLMError(code="auth_error", message=str(e), retryable=False)
            except litellm.exceptions.BadRequestError as e:
                raise LLMError(code="bad_request", message=str(e), retryable=False)
            except (
                litellm.exceptions.RateLimitError,
                litellm.exceptions.APIConnectionError,
                litellm.exceptions.InternalServerError,
                TimeoutError,
            ) as e:
                last_error = e
                if attempt < MAX_RETRIES:
                    # Compute exponential backoff: base * 2^attempt, capped at max
                    delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                    self._failure_count = attempt + 1
                    logger.warning(
                        "LLM retry %d/%d after %.1fs on model %s: %s",
                        attempt + 1, MAX_RETRIES, delay, model_name, str(e)[:200],
                        extra={
                            "category": "llm",
                            "event": "retry",
                            "retry_count": attempt + 1,
                            "model": model_name,
                        },
                    )
                    if self._retry_callback:
                        try:
                            self._retry_callback(attempt + 1, MAX_RETRIES, delay)
                        except Exception:
                            pass
                    await asyncio.sleep(delay)
                    # Update failure count for the hook
                    continue
                else:
                    # All retries exhausted — classify and raise
                    if isinstance(e, litellm.exceptions.RateLimitError):
                        raise LLMError(code="rate_limit", message=str(e), retryable=True)
                    elif isinstance(e, litellm.exceptions.APIConnectionError):
                        raise LLMError(code="connection_error", message=str(e), retryable=True)
                    elif isinstance(e, litellm.exceptions.InternalServerError):
                        raise LLMError(code="server_error", message=str(e), retryable=True)
                    elif isinstance(e, TimeoutError):
                        raise LLMError(code="timeout", message=str(e), retryable=True)
                    else:
                        raise LLMError(code="max_retries", message=str(e), retryable=True)
            except litellm.exceptions.APIError as e:
                last_error = e
                status = getattr(e, "status_code", None)
                is_retryable = status is not None and (status in RETRYABLE_HTTP_CODES or status >= 500)
                if is_retryable and attempt < MAX_RETRIES:
                    delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                    self._failure_count = attempt + 1
                    logger.warning(
                        "LLM retry %d/%d after %.1fs on model %s (HTTP %s): %s",
                        attempt + 1, MAX_RETRIES, delay, model_name, status, str(e)[:200],
                        extra={
                            "category": "llm",
                            "event": "retry",
                            "retry_count": attempt + 1,
                            "model": model_name,
                        },
                    )
                    if self._retry_callback:
                        try:
                            self._retry_callback(attempt + 1, MAX_RETRIES, delay)
                        except Exception:
                            pass
                    await asyncio.sleep(delay)
                    continue
                else:
                    raise LLMError(code=f"api_error_{status}", message=str(e),
                                   retryable=is_retryable)
            except Exception as e:
                raise LLMError(code="unknown", message=str(e), retryable=False)

        # If we exhausted all retries without success
        if last_error is not None and 'response' not in dir():
            # This should not happen because the loop either breaks on success
            # or raises — but guard against edge cases
            raise LLMError(code="max_retries", message=str(last_error), retryable=True)

        # ── Stream response (no retry — stream interruptions handled by caller) ──
        total_tokens = 0
        prompt_tokens = 0
        completion_tokens = 0
        tool_calls_count = 0
        response_text_chunks: list[str] = []
        response_tool_calls: list[dict] = []

        if self._streaming:
            async for event in self._stream_response(response):
                if isinstance(event, ToolCall):
                    tool_calls_count += 1
                    response_tool_calls.append({
                        "id": event.id,
                        "name": event.name,
                        "params": event.params,
                    })
                if isinstance(event, TextDelta):
                    response_text_chunks.append(event.content)
                yield event
                if isinstance(event, Done) and event.usage:
                    total_tokens = event.usage.total_tokens
                    prompt_tokens = event.usage.prompt_tokens
                    completion_tokens = event.usage.completion_tokens
        else:
            # G5: Non-streaming path — collect full response then emit
            await self._process_non_streaming(
                response, response_text_chunks, response_tool_calls,
            )
            full_text = "".join(response_text_chunks)
            if full_text:
                yield TextDelta(content=full_text)
            for tc in response_tool_calls:
                tool_calls_count += 1
                yield ToolCall(
                    id=tc.get("id", ""),
                    name=tc.get("name", ""),
                    params=tc.get("params", {}),
                )
            if hasattr(response, 'usage') and response.usage:
                usage = response.usage
                prompt_tokens = getattr(usage, 'prompt_tokens', 0) or 0
                completion_tokens = getattr(usage, 'completion_tokens', 0) or 0
                total_tokens = getattr(usage, 'total_tokens', 0) or 0
                yield Done(
                    stop_reason="end_turn",
                    usage=Usage(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                    ),
                )
            else:
                yield Done(stop_reason="end_turn")

        # Success — log response and return
        retry_count = getattr(self, '_failure_count', 0)

        latency_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "LLM response: model=%s latency_ms=%.1f tokens=%d tool_calls=%d retries=%d",
            model_name, latency_ms, total_tokens, tool_calls_count, retry_count,
            extra={
                "category": "llm",
                "event": "response",
                "model": model_name,
                "latency_ms": round(latency_ms, 1),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "token_consumption": total_tokens,
                "tool_calls_count": tool_calls_count,
                "retry_count": retry_count,
            },
        )
        self._write_response_log(
            model_name, response_text_chunks, response_tool_calls,
            prompt_tokens, completion_tokens, total_tokens, latency_ms, retry_count,
        )
        return

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
        """Process streaming response into LLMEvent instances.

        Tool calls arrive in streaming chunks: the id, name, and
        arguments may be spread across multiple deltas for the same
        tool call index.  We accumulate them in a buffer and emit a
        single ToolCall event per index once the arguments form valid
        JSON.  A ``_yielded`` flag prevents duplicate emissions when
        subsequent chunks carry additional data for the same index.

        Fixes audit #42.
        """
        tool_call_buffers: dict[int, dict] = {}  # index → {id, name, args_str, _yielded}

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
                            "_yielded": False,
                        }
                    buf = tool_call_buffers[idx]

                    # Skip if already emitted for this index
                    if buf["_yielded"]:
                        continue

                    if tc.id:
                        buf["id"] = tc.id
                    func = getattr(tc, "function", None)
                    if func:
                        if func.name:
                            buf["name"] = func.name
                        if func.arguments:
                            buf["args_str"] += func.arguments

                    # Emit tool call once we have a name and parseable args
                    if buf["args_str"] and buf["name"]:
                        try:
                            params = json.loads(buf["args_str"])
                        except json.JSONDecodeError:
                            # Args still incomplete — wait for more chunks
                            continue
                        buf["_yielded"] = True
                        yield ToolCall(id=buf["id"], name=buf["name"], params=params)

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

    def _process_non_streaming(
        self,
        response,
        text_chunks: list[str],
        tool_calls: list[dict],
    ) -> None:
        """Process a non-streaming litellm response (G5).

        Extracts text content and tool calls from the full response object
        and populates the provided lists so the caller can emit the appropriate
        events.

        Non-streaming responses have a different structure:
        - response.choices[0].message.content → full text
        - response.choices[0].message.tool_calls → list of tool calls
        - response.usage → token usage
        """
        if not response.choices:
            return

        choice = response.choices[0]
        message = getattr(choice, "message", None)
        if message is None:
            return

        # Extract text content
        content = getattr(message, "content", None)
        if content:
            text_chunks.append(content)

        # Extract tool calls
        raw_tool_calls = getattr(message, "tool_calls", None)
        if raw_tool_calls:
            for tc in raw_tool_calls:
                fn = getattr(tc, "function", None)
                if fn:
                    try:
                        params = json.loads(getattr(fn, "arguments", "{}") or "{}")
                    except json.JSONDecodeError:
                        params = {}
                    tool_calls.append({
                        "id": getattr(tc, "id", "") or "",
                        "name": getattr(fn, "name", "") or "",
                        "params": params,
                    })

    def _write_prompt_logs(
        self, model_name: str, messages: list[dict],
        tools: list[dict] | None, attempt: int,
    ) -> None:
        """Write full prompt/response to .prompts/ directory when enabled (gap-10).

        Triggered when logging_config.llm_prompts is True and log level is DEBUG.
        Writes to ~/.myagent/logs/.prompts/<timestamp>-<session>-request.json
        and response file.
        """
        logging_config = getattr(self, '_logging_config', None)
        if logging_config is None:
            return
        if not getattr(logging_config, "llm_prompts", False):
            return

        # Only write at DEBUG level
        import logging
        if logging.getLogger("myagent.llm").getEffectiveLevel() > logging.DEBUG:
            return

        try:
            log_dir = Path(getattr(self._logging_config, "dir", "~/.myagent/logs/")).expanduser().resolve()
            prompts_dir = log_dir / ".prompts"
            prompts_dir.mkdir(parents=True, exist_ok=True)

            self._prompt_counter += 1
            ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
            session_part = self._session_id or "nosession"

            # Write request file
            request_file = prompts_dir / f"{ts}-{session_part}-request-{self._prompt_counter:04d}.json"
            request_data = {
                "model": model_name,
                "attempt": attempt + 1,
                "messages": messages,
                "tools": tools,
            }
            request_file.write_text(
                json.dumps(request_data, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception:
            pass  # Prompt logging is best-effort

    def _write_response_log(
        self, model_name: str, text_chunks: list[str],
        tool_calls: list[dict],
        prompt_tokens: int, completion_tokens: int,
        total_tokens: int, latency_ms: float, attempt: int,
    ) -> None:
        """Write LLM response to .prompts/ directory when enabled (gap-2-06).

        Triggered when logging_config.llm_prompts is True and log level is DEBUG.
        Writes the complete response data as a JSON file.
        """
        logging_config = getattr(self, '_logging_config', None)
        if logging_config is None:
            return
        if not getattr(logging_config, "llm_prompts", False):
            return

        # Only write at DEBUG level
        import logging
        if logging.getLogger("myagent.llm").getEffectiveLevel() > logging.DEBUG:
            return

        try:
            log_dir = Path(getattr(self._logging_config, "dir", "~/.myagent/logs/")).expanduser().resolve()
            prompts_dir = log_dir / ".prompts"
            prompts_dir.mkdir(parents=True, exist_ok=True)

            ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
            session_part = self._session_id or "nosession"

            response_file = prompts_dir / f"{ts}-{session_part}-response-{self._prompt_counter:04d}.json"
            response_data = {
                "model": model_name,
                "attempt": attempt + 1,
                "latency_ms": round(latency_ms, 1),
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                },
                "text": "".join(text_chunks) if text_chunks else "",
                "tool_calls": tool_calls if tool_calls else [],
            }
            response_file.write_text(
                json.dumps(response_data, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception:
            pass  # Prompt logging is best-effort

    # Allow setting model manually (for testing)
    def _set_test_model(self, model: str):
        self.model = model
