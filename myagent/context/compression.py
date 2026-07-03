"""Compression engine — 4-layer progressive context compression.

Layer 1 — Cleanup (zero-cost): remove denied/empty tool calls
Layer 2 — Summarize (low-cost): replace large tool results with summary
Layer 3 — Conversation summary (one API call): summarize oldest messages
Layer 4 — Hard truncation: drop oldest blocks to stay under hard_limit
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from myagent.context.builder import Message

logger = logging.getLogger("myagent.context.compression")


@dataclass
class CompactResult:
    messages: list[Message]
    usage_after: float
    layers_applied: list[int]
    messages_changed: bool = False
    degradation_notice: str | None = None  # gap-r12-07: Layer 3 degradation notice


class CompressionEngine:
    def __init__(self, config=None, llm=None, tools_config=None):
        self.config = config
        self.llm = llm
        self.tools_config = tools_config
        self._layer3_failures = 0
        self._compact_counter = 0
        self._session_dir = None

    def set_session_dir(self, session_dir: str | Path | None) -> None:
        """Set the session directory for persisting compression summaries.

        Must be called before compaction with layer 3 is triggered,
        otherwise summaries are silently skipped (gap-03).
        """
        from pathlib import Path as _Path
        if session_dir is not None:
            self._session_dir = _Path(session_dir)

    @staticmethod
    def _count_conversation_messages(messages: list[Message]) -> int:
        """Count non-system messages (actual conversation rounds).

        System messages (the system prompt) are metadata injected by the
        engine and should not count toward the minimum_messages debounce
        guard (gap-r14-05). The spec's "最少 10 轮消息" refers to actual
        conversation rounds, not including system prompts.
        """
        return sum(1 for m in messages if m.role != "system")

    async def compact(
        self, messages: list[Message], current_usage_pct: float
    ) -> CompactResult:
        conv_count = self._count_conversation_messages(messages)
        if self.config and conv_count < self.config.minimum_messages:
            return CompactResult(messages=messages, usage_after=current_usage_pct, layers_applied=[])

        # ── Save original state for debounce rollback ──────────
        original_messages = list(messages)
        original_size = sum(len(m.content) for m in messages)
        layers_applied: list[int] = []
        changed = False
        degradation_notice: str | None = None
        min_savings = getattr(self.config, 'minimum_savings', 0.10)

        # Layer 1: Cleanup (zero-cost, always applied when threshold met)
        if self.config and current_usage_pct >= self.config.primary_threshold:
            old_len = len(messages)
            messages = self._layer1_cleanup(messages)
            if len(messages) < old_len:
                layers_applied.append(1)
                changed = True
                current_usage_pct *= 0.95  # rough estimate

        # Layer 2: Summarize tool results (always apply; global debounce
        # at the end will roll back if needed)
        if self.config and current_usage_pct >= self.config.primary_threshold:
            pre_l2_size = sum(len(m.content) for m in messages)
            messages = await self._layer2_summarize(messages)
            post_l2_size = sum(len(m.content) for m in messages)
            if post_l2_size < pre_l2_size:
                layers_applied.append(2)
                changed = True
                current_usage_pct *= 0.7

        # Layer 3: Conversation summary
        if current_usage_pct > (self.config.primary_threshold if self.config else 0.75):
            if self._layer3_failures >= 3:
                # Layer 3 is degraded — set notice once for user notification
                degradation_notice = (
                    "Conversation summarization (Layer 3) has been disabled after "
                    "3 consecutive failures. Context pressure may build faster. "
                    "Consider using /compact or /clear to manually reduce context."
                )
            else:
                try:
                    # Capture pre-Layer-3 character count for actual reduction measurement
                    pre_l3_chars = sum(len(m.content) for m in messages)
                    messages = await self._layer3_summarize(messages)
                    layers_applied.append(3)
                    changed = True
                    # Re-estimate actual usage by measuring character reduction
                    # rather than assuming it always reaches the target (gap-16-02).
                    # Per spec: "达不到30%就接受实际结果"
                    post_l3_chars = sum(len(m.content) for m in messages)
                    if pre_l3_chars > 0:
                        reduction_ratio = post_l3_chars / pre_l3_chars
                        current_usage_pct = current_usage_pct * reduction_ratio
                    else:
                        current_usage_pct = (
                            self.config.target_after if self.config else 0.30
                        )
                    self._layer3_failures = 0
                except Exception:
                    self._layer3_failures += 1
                    if self._layer3_failures >= 3:
                        degradation_notice = (
                            "Conversation summarization (Layer 3) has been disabled after "
                            "3 consecutive failures. Context pressure may build faster. "
                            "Consider using /compact or /clear to manually reduce context."
                        )

        # ── Global debounce: roll back if total savings < minimum_savings ──
        # Per spec §三 防抖保护: "压缩后 token 减少 < 10% → 跳过"
        # This applies to Layers 2-3 (which have real cost: content reduction / API call).
        # Layer 1 is zero-cost cleanup (removing empty messages) and is always kept.
        if changed and original_size > 0:
            post_size = sum(len(m.content) for m in messages)
            total_savings = (original_size - post_size) / original_size
            if total_savings < min_savings:
                # Only roll back if Layers 2 or 3 were applied (Layer 1 alone is always kept).
                has_costly_layers = any(l in layers_applied for l in (2, 3))
                if has_costly_layers:
                    logger.debug(
                        "Overall compression savings (%.1f%%) below minimum_savings (%.1f%%); "
                        "discarding compacted result (layers 2-3).",
                        total_savings * 100, min_savings * 100,
                        extra={"category": "agent"},
                    )
                    messages = original_messages
                    # Re-apply only Layer 1 (zero-cost cleanup)
                    if 1 in layers_applied:
                        messages = self._layer1_cleanup(messages)
                        layers_applied = [1]
                    else:
                        layers_applied = []
                    changed = bool(layers_applied)
                else:
                    # Only Layer 1: keep it (zero-cost, always beneficial)
                    logger.debug(
                        "Layer 1 only compaction — keeping result (%.1f%% savings).",
                        total_savings * 100,
                        extra={"category": "agent"},
                    )
            else:
                logger.debug(
                    "Compression complete: %.1f%% savings across layers %s.",
                    total_savings * 100, layers_applied,
                    extra={"category": "agent"},
                )

        # Layer 4: Hard truncation (safety, always applies regardless of debounce)
        hard_limit = self.config.hard_limit if self.config else 0.90
        if current_usage_pct > hard_limit:
            messages = self._layer4_truncate(messages)
            layers_applied.append(4)
            changed = True

        return CompactResult(
            messages=messages,
            usage_after=current_usage_pct,
            layers_applied=layers_applied,
            messages_changed=changed,
            degradation_notice=degradation_notice,
        )

    def _layer1_cleanup(self, messages: list[Message]) -> list[Message]:
        """Remove tool calls that were denied or returned empty results."""
        kept = []
        for m in messages:
            if m.role == "tool" and (not m.content or m.content.strip() == ""):
                continue
            kept.append(m)
        return kept

    async def _layer2_summarize(self, messages: list[Message]) -> list[Message]:
        """Semantically summarize large tool results using LLM (gap-18-06).

        Per design spec §三 压缩策略: "工具结果 → 语义摘要 + 引用".
        Large tool results (> max_chars) are sent to the LLM for semantic
        summarization, preserving key information while discarding redundancy.
        Falls back to raw truncation if LLM is unavailable.

        The semantic summary is prepended with a header noting the original
        size and the fact it was summarized. The full result is persisted to
        disk via _persist_tool_result() for traceability.
        """
        if self.tools_config and hasattr(self.tools_config, 'tool_result_max_chars'):
            max_chars = self.tools_config.tool_result_max_chars
        elif self.config and hasattr(self.config, 'tool_result_max_chars'):
            max_chars = self.config.tool_result_max_chars
        else:
            max_chars = 5000

        # Protect last 5 rounds
        protected = 5 * 2  # ~5 user+assistant pairs
        result = []

        for i, m in enumerate(messages):
            if (i < len(messages) - protected
                    and m.role == "tool"
                    and len(m.content) > max_chars):
                # Attempt semantic summarization via LLM
                summary = await self._summarize_tool_result(
                    m.content, max_chars, m.name or "unknown"
                )
                if summary is not None:
                    m = Message(
                        role=m.role,
                        content=summary,
                        tool_call_id=m.tool_call_id,
                        name=m.name,
                        timestamp=m.timestamp,
                        tokens_used=m.tokens_used,
                    )
                else:
                    # Fallback: raw truncation
                    m = Message(
                        role=m.role,
                        content=(
                            f"[Summarized: {len(m.content)} chars → {max_chars} chars]\n"
                            f"{m.content[:max_chars]}"
                        ),
                        tool_call_id=m.tool_call_id,
                        name=m.name,
                        timestamp=m.timestamp,
                        tokens_used=m.tokens_used,
                    )
            result.append(m)
        return result

    async def _layer3_summarize(self, messages: list[Message]) -> list[Message]:
        """Summarize oldest messages using real LLM call.

        Takes the oldest portion of messages, sends them to the LLM for
        summarization, and replaces them with a concise summary message.
        Falls back to placeholder summary if LLM is unavailable or fails.
        """
        if not self.llm or self._layer3_failures >= 3:
            return messages

        # Count actual conversation messages (exclude system prompts, gap-r14-05)
        if self._count_conversation_messages(messages) < 10:
            return messages

        # Take oldest 60% of messages, summarize them
        split = max(int(len(messages) * 0.6), 5)
        old = messages[:split]
        recent = messages[split:]

        # Build a compact representation of old messages for the summarizer
        conversation_text = self._messages_to_text(old)

        try:
            summary_text = await self._summarize_with_llm(conversation_text, len(old))
            summary_msg = Message(
                role="system",
                content=(
                    f"[Conversation summary: {len(old)} messages compressed]\n"
                    f"{summary_text}"
                ),
            )
            self._persist_summary(summary_text, len(old))
            return [summary_msg] + recent
        except Exception:
            # Fallback to placeholder
            summary_text = f"[Conversation summary: {len(old)} messages covering {old[0].role} → {old[-1].role} exchanges]"
            summary_msg = Message(role="system", content=summary_text)
            self._persist_summary(summary_text, len(old))
            return [summary_msg] + recent

    def _persist_summary(self, summary_text: str, message_count: int) -> None:
        """Persist layer-3 summary to summaries/compact-NNN.md (gap-15)."""
        self._compact_counter += 1
        if self._session_dir:
            summaries_dir = self._session_dir / "summaries"
            summaries_dir.mkdir(parents=True, exist_ok=True)
            summary_path = summaries_dir / f"compact-{self._compact_counter:03d}.md"
            try:
                summary_path.write_text(
                    f"# Compact #{self._compact_counter}\n\n"
                    f"Messages compressed: {message_count}\n"
                    f"Layers applied: Layer 3 (conversation summary)\n\n"
                    f"{summary_text}\n",
                    encoding="utf-8",
                )
            except Exception:
                pass  # Best-effort persistence

    def _messages_to_text(self, messages: list[Message]) -> str:
        """Convert a list of messages to a compact text representation."""
        lines = []
        for m in messages:
            role = m.role.upper()
            content = m.content[:500] if m.content else "(empty)"
            if len(m.content) > 500:
                content += "..."
            lines.append(f"[{role}] {content}")
        return "\n".join(lines)

    async def _summarize_tool_result(
        self, content: str, max_chars: int, tool_name: str
    ) -> str | None:
        """Produce a semantic summary of a large tool result via LLM (gap-18-06).

        Sends the tool result content to the LLM in Non-think mode to extract
        key information. Returns a summary string with a header indicating
        the original size and the fact of semantic summarization.

        Returns None if LLM is unavailable, in which case the caller should
        fall back to raw character truncation.
        """
        if not self.llm or self._layer3_failures >= 3:
            return None

        # Truncate input to a reasonable size for the summarizer LLM call.
        # We send up to 20K chars to get a good semantic summary.
        input_text = content[:20000]
        if len(content) > 20000:
            input_text += "\n\n[Content truncated for summarization...]"

        summarization_prompt = (
            f"Summarize the following tool result from tool '{tool_name}' "
            f"concisely. Preserve all key facts, data, findings, errors, "
            f"and structural information. Remove redundant or repetitive content. "
            f"The summary will replace the full result in the conversation context. "
            f"Output ONLY the summary, no preamble or meta-commentary.\n\n"
            f"--- TOOL RESULT ({len(content)} chars total) ---\n"
            f"{input_text}\n"
            f"--- END TOOL RESULT ---\n\n"
            f"Concise semantic summary:"
        )

        try:
            messages_for_llm = [{"role": "user", "content": summarization_prompt}]
            collected = []
            async for event in self.llm.complete(
                messages=messages_for_llm,
                tools=None,
                thinking="Non-think",
            ):
                if hasattr(event, "content") and isinstance(event.content, str):
                    collected.append(event.content)

            summary_text = "".join(collected).strip()
            if not summary_text:
                return None

            # Persist the full tool result for traceability
            self._persist_tool_result(tool_name, content)

            # Build the final formatted summary
            original_size = len(content)
            summary_size = len(summary_text)
            return (
                f"[Semantic summary of tool result from '{tool_name}': "
                f"{original_size} chars → {summary_size} chars]\n"
                f"{summary_text}"
            )
        except Exception:
            logger.debug(
                "Layer 2 LLM summarization failed for tool '%s', "
                "falling back to raw truncation",
                tool_name,
                exc_info=True,
                extra={"category": "agent"},
            )
            return None

    def _persist_tool_result(self, tool_name: str, content: str) -> None:
        """Persist the full tool result to the session's tools/ directory."""
        self._compact_counter += 1
        if self._session_dir:
            tools_dir = self._session_dir / "tools"
            tools_dir.mkdir(parents=True, exist_ok=True)
            out_path = (
                tools_dir / f"layer2-compact-{self._compact_counter:03d}.json"
            )
            try:
                import json
                out_path.write_text(
                    json.dumps(
                        {
                            "tool_name": tool_name,
                            "original_chars": len(content),
                            "content": content,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass  # Best-effort persistence

    async def _summarize_with_llm(self, conversation_text: str, message_count: int) -> str:
        """Use the LLM to produce a concise summary of the conversation."""
        if not self.llm:
            return f"Summary of {message_count} messages (LLM unavailable)."

        summary_prompt = (
            f"Summarize the following {message_count} conversation messages "
            f"concisely. Keep all key decisions, facts, findings, and action items. "
            f"Remove redundant exchanges and filler. Output only the summary, "
            f"no preamble or meta-commentary.\n\n"
            f"{conversation_text}"
        )

        messages_for_llm = [{"role": "user", "content": summary_prompt}]

        collected = []
        async for event in self.llm.complete(
            messages=messages_for_llm,
            tools=None,
            thinking="Non-think",
        ):
            if hasattr(event, "content") and isinstance(event.content, str):
                collected.append(event.content)

        result = "".join(collected).strip()
        return result if result else f"Summary of {message_count} messages (empty response)."

    def _layer4_truncate(self, messages: list[Message]) -> list[Message]:
        """Drop oldest message blocks to stay under hard limit."""
        if len(messages) < 5:
            return messages
        # Keep last ~40%
        keep = max(int(len(messages) * 0.4), 2)
        return messages[-keep:]
