"""Status bar — Rich renderable display of agent state (gap-2-07, gap-2-08).

Produces a Rich renderable (Panel) that can be embedded in a shared Layout.
Does NOT manage its own Live instance — the REPL owns the single shared Live display.

Design doc reference: §一 CLI Layer (Status Bar), §八 Context 状态栏展示
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.panel import Panel


@dataclass
class SubAgentInfo:
    """Rich sub-agent detail for status bar display (gap-2-08)."""
    agent_id: str
    task_name: str = ""
    status: str = "running"  # running | completed | failed | retrying
    progress_pct: float = 0.0
    result_summary: str = ""
    retry_count: int = 0
    max_retries: int = 0


class StatusBar:
    """Produces Rich renderables for agent status display.

    Designed to be embedded in a shared Rich Layout managed by the REPL.
    Does NOT create its own Live instance (gap-2-07).
    """

    def __init__(self, config=None):
        self.config = config
        self._data = {
            "subagents_active": 0,
            "subagents_details": [],  # list[SubAgentInfo]
            "tokens": 0,
            "thinking": "Think High",
        }

    def update(self, **kwargs) -> None:
        self._data.update(kwargs)

    def get_renderable(self) -> "Panel | None":
        """Return a Rich Panel renderable for the shared layout (gap-2-07)."""
        try:
            from rich.panel import Panel
        except ImportError:
            return None

        # Build the top bar line
        items = []
        if self.config and hasattr(self.config, "status_bar_items"):
            enabled = self.config.status_bar_items
        else:
            enabled = ["subagents", "tokens", "thinking"]

        if "subagents" in enabled:
            items.append(f"🤖 Sub-agents: {self._data['subagents_active']} active")
        if "tokens" in enabled:
            items.append(f"Token: {self._data['tokens']}")
        if "thinking" in enabled:
            items.append(str(self._data["thinking"]))

        lines = ["  ".join(items)]

        # Retry progress display (gap-8-07)
        retry_info = self._data.get("retry_info")
        if retry_info:
            lines.append(f"  🔄 {retry_info}")

        # Sub-agent detail lines (gap-2-08)
        details = self._data.get("subagents_details", [])
        for info in details:
            if isinstance(info, SubAgentInfo):
                lines.append(self._format_subagent_detail(info))
            elif isinstance(info, str):
                # Legacy string format fallback
                lines.append(f"  ├─ {info}")

        return Panel("\n".join(lines), title="MyAgent")

    def _format_subagent_detail(self, info: SubAgentInfo) -> str:
        """Format a sub-agent detail line with rich emoji indicators (gap-2-08).

        Matches design spec example:
          ├─ review-auth      ⏳ 审查中... (82%)
          ├─ review-api       ✅ 完成 (2 个问题)
          └─ review-middleware 🔄 重试中 (1/3)
        """
        display_name = info.task_name or info.agent_id

        if info.status == "running":
            if info.progress_pct > 0:
                return f"  ├─ {display_name:<20} ⏳ running ({info.progress_pct:.0f}%)"
            else:
                return f"  ├─ {display_name:<20} ⏳ running..."
        elif info.status == "completed":
            summary = f" ({info.result_summary})" if info.result_summary else ""
            return f"  ├─ {display_name:<20} ✅ completed{summary}"
        elif info.status == "failed":
            return f"  ├─ {display_name:<20} ❌ failed"
        elif info.status == "retrying":
            retry_str = f" ({info.retry_count}/{info.max_retries})" if info.max_retries > 0 else ""
            return f"  ├─ {display_name:<20} 🔄 retrying{retry_str}"
        else:
            return f"  ├─ {display_name:<20} {info.status}"
