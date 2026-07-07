"""Goal tracker — overlay on the ReAct loop for goal-oriented execution."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from myagent.llm.provider import LLMProvider

logger = logging.getLogger("myagent.agent.goal")


@dataclass
class GoalCheckResult:
    achieved: bool
    reasoning: str = ""
    remaining_work: str | None = None


class GoalTracker:
    """Tracks whether a session goal has been achieved.

    Goal mode is an overlay — not a separate execution mode.
    When the model emits `done` and a goal is set, GoalTracker
    interjects to check if the goal is achieved.

    If an LLMProvider is available, evaluation is LLM-driven.
    Without an LLM, the tracker returns a conservative
    "not achieved" to prevent false completion claims.
    """

    def __init__(self, llm: LLMProvider | None = None):
        self._goal: str | None = None
        self._version = 0
        self._llm: LLMProvider | None = llm

    def set_goal(self, goal: str) -> None:
        self._goal = goal
        self._version += 1

    def clear_goal(self) -> None:
        self._goal = None
        self._version += 1

    def get_goal(self) -> str | None:
        return self._goal

    def get_goal_snapshot(self) -> tuple[str | None, int]:
        return self._goal, self._version

    def is_current_goal(self, goal: str | None, version: int) -> bool:
        return self._goal == goal and self._version == version

    async def check_goal(
        self, session, recent_history: list, goal: str | None = None
    ) -> GoalCheckResult:
        """Judge if the goal is achieved based on conversation history.

        With LLM: sends an evaluation prompt and parses JSON response.
        Without LLM: returns conservative "not achieved" (safer than
        falsely claiming completion).

        Args:
            session: The current session object.
            recent_history: List of recent conversation messages.

        Returns:
            GoalCheckResult with achieved status, reasoning, and
            optional remaining work description.
        """
        goal_text = self._goal if goal is None else goal
        if not goal_text:
            return GoalCheckResult(achieved=False, reasoning="No goal set")

        if self._llm is None:
            return GoalCheckResult(
                achieved=False,
                reasoning="No LLM available for goal evaluation — conservative fallback.",
            )

        try:
            return await self._llm_check(session, recent_history, goal_text)
        except Exception:
            logger.exception(
                "Goal evaluation via LLM failed",
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "goal_evaluation_llm",
                },
            )
            return GoalCheckResult(
                achieved=False,
                reasoning="Goal evaluation failed — conservative fallback.",
            )

    # ── internal methods ──────────────────────────────────────────

    async def _llm_check(
        self,
        session,
        recent_history: list,
        goal: str,
    ) -> GoalCheckResult:
        """Build evaluation prompt, call LLM, and parse the JSON response.

        Summarises the last 20 messages from recent_history, sends an
        evaluation prompt in Non-think mode, and extracts the JSON result.
        """
        # Build a summary of recent history (last 20 messages max)
        history_summary = self._build_history_summary(recent_history)

        # Build evaluation prompt
        system_prompt = (
            "You are a goal-evaluation assistant. Your task is to determine "
            "whether the user's stated goal has been achieved based on the "
            "conversation history. Respond ONLY with a JSON object with these fields:\n"
            '  - "achieved": boolean (true if the goal is fully achieved)\n'
            '  - "reasoning": string (brief explanation of your assessment)\n'
            '  - "remaining_work": string or null (what still needs to be done, if anything)\n'
            "Do not include any other text, markdown, or explanation outside the JSON."
        )

        user_message = (
            f"Goal: {goal}\n\n"
            f"Conversation history summary:\n{history_summary}\n\n"
            "Based on the conversation above, has the goal been achieved? "
            "Respond with JSON only."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        # Stream LLM response in non-think mode and collect text
        collected_text = ""
        async for event in self._llm.complete(
            messages=messages,
            tools=None,
            thinking="Non-think",
        ):
            # Duck-typing: accept any event with a 'content' attribute
            if hasattr(event, "content") and isinstance(event.content, str):
                collected_text += event.content

        logger.info(
            "Goal evaluation completed",
            extra={
                "category": "agent",
                "goal": goal[:100],
                "collected_chars": len(collected_text),
            },
        )

        # Parse JSON from the collected text
        return self._parse_goal_response(collected_text)

    def _build_history_summary(self, recent_history: list) -> str:
        """Build a concise summary of recent conversation history.

        Takes the last 20 messages and extracts role + content preview.
        """
        if not recent_history:
            return "(no conversation history)"

        # Take last 20 messages
        last_messages = recent_history[-20:]

        lines = []
        for msg in last_messages:
            if hasattr(msg, "role") and hasattr(msg, "content"):
                role = msg.role
                content = msg.content or ""
            elif isinstance(msg, dict):
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
            else:
                continue

            # Truncate long content for the summary
            preview = content[:300] if content else "(empty)"
            if len(content) > 300:
                preview += "..."

            lines.append(f"[{role}] {preview}")

        return "\n".join(lines)

    def _parse_goal_response(self, text: str) -> GoalCheckResult:
        """Parse the JSON response from the LLM into a GoalCheckResult.

        Tolerates surrounding markdown fences and common JSON wrapping.
        """
        if not text.strip():
            return GoalCheckResult(
                achieved=False,
                reasoning="Empty response from LLM — conservative fallback.",
            )

        # Strip markdown code fences if present
        clean = text.strip()
        if clean.startswith("```"):
            # Remove opening fence (```json or ```)
            newline_idx = clean.find("\n")
            if newline_idx != -1:
                clean = clean[newline_idx + 1:]
            # Remove closing fence
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()

        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse goal evaluation JSON",
                exc_info=True,
                extra={
                    "category": "error",
                    "component": "agent",
                    "context": "parse goal evaluation JSON",
                    "raw_text": text[:500],
                },
            )
            return GoalCheckResult(
                achieved=False,
                reasoning="Failed to parse LLM response — conservative fallback.",
            )

        return GoalCheckResult(
            achieved=bool(data.get("achieved", False)),
            reasoning=data.get("reasoning", ""),
            remaining_work=data.get("remaining_work"),
        )
