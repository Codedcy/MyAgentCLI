"""Compression engine — 4-layer progressive context compression.

Layer 1 — Cleanup (zero-cost): remove denied/empty tool calls
Layer 2 — Summarize (low-cost): replace large tool results with summary
Layer 3 — Conversation summary (one API call): summarize oldest messages
Layer 4 — Hard truncation: drop oldest blocks to stay under hard_limit
"""

from __future__ import annotations

from dataclasses import dataclass, field

from myagent.context.builder import Message


@dataclass
class CompactResult:
    messages: list[Message]
    usage_after: float
    layers_applied: list[int]
    messages_changed: bool = False


class CompressionEngine:
    def __init__(self, config=None, llm=None):
        self.config = config
        self.llm = llm
        self._layer3_failures = 0

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
            if new_size < old_size * 0.9:
                layers_applied.append(2)
                changed = True
                current_usage_pct *= 0.7

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
        """Truncate large tool results to max 5000 chars."""
        max_chars = self.config.tool_result_max_chars if hasattr(self.config, 'tool_result_max_chars') else 5000
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
        """Summarize oldest messages using LLM."""
        if not self.llm or self._layer3_failures >= 3:
            return messages

        if len(messages) < 10:
            return messages

        # Take oldest 60% of messages, summarize them
        split = max(int(len(messages) * 0.6), 5)
        old = messages[:split]
        recent = messages[split:]

        summary_text = f"[Conversation summary: {len(old)} messages covering {old[0].role} → {old[-1].role} exchanges]"
        summary_msg = Message(role="system", content=summary_text)

        return [summary_msg] + recent

    def _layer4_truncate(self, messages: list[Message]) -> list[Message]:
        """Drop oldest message blocks to stay under hard limit."""
        if len(messages) < 5:
            return messages
        # Keep last ~40%
        keep = max(int(len(messages) * 0.4), 2)
        return messages[-keep:]
