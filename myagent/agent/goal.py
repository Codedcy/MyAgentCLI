"""Goal tracker — overlay on the ReAct loop for goal-oriented execution."""

from __future__ import annotations

from dataclasses import dataclass


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
    """

    def __init__(self):
        self._goal: str | None = None

    def set_goal(self, goal: str) -> None:
        self._goal = goal

    def clear_goal(self) -> None:
        self._goal = None

    def get_goal(self) -> str | None:
        return self._goal

    async def check_goal(
        self, session, recent_history: list
    ) -> GoalCheckResult:
        """Judge if the goal is achieved based on conversation history.

        In production, this uses an LLM call to evaluate goal completion.
        For now, returns a placeholder result assuming achievement.
        """
        if not self._goal:
            return GoalCheckResult(achieved=False, reasoning="No goal set")

        # Placeholder: assume goal is achieved when model says done
        return GoalCheckResult(
            achieved=True,
            reasoning=f"Goal '{self._goal}' appears to be completed based on conversation context.",
        )
