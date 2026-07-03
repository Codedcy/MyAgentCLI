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

    async def compact(
        self, messages: list[Message], current_usage_pct: float
    ) -> CompactResult:
        if self.config and len(messages) < self.config.minimum_messages:
            return CompactResult(messages=messages, usage_after=current_usage_pct, layers_applied=[])

        layers_applied = []
        changed = False

        # Layer 1: Cleanup
        if self.config and current_usage_pct >= self.config.primary_threshold:
            old_len = len(messages)
            messages = self._layer1_cleanup(messages)
            if len(messages) < old_len:
                layers_applied.append(1)
                changed = True
                current_usage_pct *= 0.95  # rough estimate

        # Layer 2: Summarize tool results
        if self.config and current_usage_pct >= self.config.primary_threshold:
            old_size = sum(len(m.content) for m in messages)
            messages = self._layer2_summarize(messages)
            new_size = sum(len(m.content) for m in messages)
            # Calculate actual savings ratio
            if old_size > 0:
                savings = (old_size - new_size) / old_size
            else:
                savings = 0.0
            # Debounce: skip if savings below minimum_savings threshold (spec §三 防抖保护)
            min_savings = getattr(self.config, 'minimum_savings', 0.10)
            if savings >= min_savings:
                layers_applied.append(2)
                changed = True
                current_usage_pct *= 0.7
            elif savings > 0:
                logger.debug(
                    "Layer 2 compression savings (%.1f%%) below minimum_savings (%.1f%%); skipping.",
                    savings * 100, min_savings * 100,
                )

        # Layer 3: Conversation summary
        if current_usage_pct > self.config.primary_threshold if self.config else 0.75:
            try:
                messages = await self._layer3_summarize(messages)
                layers_applied.append(3)
                changed = True
                current_usage_pct = (
                    self.config.target_after if self.config else 0.30
                )
                self._layer3_failures = 0
            except Exception:
                self._layer3_failures += 1

        # Layer 4: Hard truncation
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
        )

    def _layer1_cleanup(self, messages: list[Message]) -> list[Message]:
        """Remove tool calls that were denied or returned empty results."""
        kept = []
        for m in messages:
            if m.role == "tool" and (not m.content or m.content.strip() == ""):
                continue
            kept.append(m)
        return kept

    def _layer2_summarize(self, messages: list[Message]) -> list[Message]:
        """Truncate large tool results to max chars from ToolsConfig.

        Checks tools_config (ToolsConfig) first, then falls back to config
        attribute for backward compatibility in tests.
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
            if i < len(messages) - protected:
                if len(m.content) > max_chars:
                    m = Message(
                        role=m.role,
                        content=f"[Summarized: {len(m.content)} chars → {max_chars} chars]\n{m.content[:max_chars]}",
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

        if len(messages) < 10:
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
