"""Status bar — Rich Live display of agent state."""

from __future__ import annotations


class StatusBar:
    """Rich Live layout showing sub-agents, token usage, thinking mode."""

    def __init__(self, config=None):
        self.config = config
        self._live = None
        self._data = {
            "subagents_active": 0,
            "subagents_details": [],
            "tokens": 0,
            "thinking": "Think High",
        }

    async def start(self) -> None:
        try:
            from rich.live import Live
            from rich.layout import Layout
            from rich.panel import Panel

            layout = Layout()
            layout.split(
                Layout(name="top", size=3),
                Layout(name="main"),
            )

            self._live = Live(layout, refresh_per_second=4, vertical_overflow="visible")
            self._live.start()
        except ImportError:
            pass

    def update(self, **kwargs) -> None:
        self._data.update(kwargs)

    def stop(self) -> None:
        if self._live:
            self._live.stop()

    def _render(self):
        from rich.panel import Panel
        lines = [
            f"🤖 Sub-agents: {self._data['subagents_active']} active | "
            f"Token: {self._data['tokens']} | "
            f"{self._data['thinking']}"
        ]
        for detail in self._data.get("subagents_details", []):
            lines.append(f"  ├─ {detail}")
        return Panel("\n".join(lines), title="MyAgent")
