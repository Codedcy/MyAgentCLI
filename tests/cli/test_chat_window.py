from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from prompt_toolkit.keys import Keys
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.utils import get_cwidth
from rich.panel import Panel

from myagent.agent.runtime_status import RuntimeStatusModel
from myagent.cli.chat_window import ChatWindowController
from myagent.cli.status import AgentInspectorPane
from myagent.cli.transcript import TranscriptBuffer
from myagent.config.schema import ChatWindowConfig, StatusPaneConfig


class FakeBuffer:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.reset_calls = 0

    def reset(self) -> None:
        self.reset_calls += 1
        self.text = ""


class FakeApplication:
    instances: list[FakeApplication] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.invalidate_calls = 0
        self.exit_calls = 0
        self._exit_event = asyncio.Event()
        FakeApplication.instances.append(self)

    async def run_async(self) -> None:
        await self._exit_event.wait()

    def invalidate(self) -> None:
        self.invalidate_calls += 1

    def exit(self) -> None:
        self.exit_calls += 1
        self._exit_event.set()


class FakeExitApp:
    def __init__(self) -> None:
        self.exit_calls = 0
        self.invalidate_calls = 0

    def exit(self) -> None:
        self.exit_calls += 1

    def invalidate(self) -> None:
        self.invalidate_calls += 1


class EmptyStatusPane:
    def get_renderable(self, terminal_columns=None):
        return None


class ExitOnceApplication(FakeApplication):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._already_exited = False

    async def run_async(self) -> None:
        self._already_exited = True

    def exit(self) -> None:
        if self._already_exited:
            raise RuntimeError("Application is not running")
        self._already_exited = True
        super().exit()


def make_config(
    *,
    collapse_below_columns: int = 120,
    follow_output: str = "auto",
) -> SimpleNamespace:
    return SimpleNamespace(
        ui=SimpleNamespace(
            chat_window=ChatWindowConfig(follow_output=follow_output),
            status_pane=StatusPaneConfig(
                width=34,
                collapse_below_columns=collapse_below_columns,
                rail_width=8,
            ),
        )
    )


def make_controller(
    *,
    transcript: TranscriptBuffer | None = None,
    model: RuntimeStatusModel | None = None,
    config: SimpleNamespace | None = None,
    status_pane: object | None = None,
) -> ChatWindowController:
    config = config or make_config()
    model = model or RuntimeStatusModel()
    pane = status_pane
    if pane is None:
        pane = AgentInspectorPane(config.ui.status_pane, status_model=model)
    return ChatWindowController(
        config,
        transcript or TranscriptBuffer(follow_output=config.ui.chat_window.follow_output),
        status_pane=pane,
        status_model=model,
    )


def visible_text(buffer: TranscriptBuffer, height: int) -> list[str]:
    return [entry.plain_text for entry in buffer.visible_entries(height)]


def last_non_empty_line(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    assert lines
    return lines[-1]


def invoke_binding(kb, keys, text: str = "") -> FakeBuffer:
    buffer = FakeBuffer(text)
    event = SimpleNamespace(
        current_buffer=buffer,
        app=SimpleNamespace(current_buffer=buffer),
    )
    for binding in kb.bindings:
        if binding.keys == tuple(keys):
            binding.handler(event)
            return buffer
    raise AssertionError(f"No binding found for {keys!r}")


def test_wide_layout_includes_conversation_bottom_input_and_full_inspector() -> None:
    model = RuntimeStatusModel()
    model.update_session(
        session_id="session-wide",
        project_name="MyAgentCLI",
        model="deepseek-v4-pro",
        thinking="Think High",
    )
    model.update_tokens(session_total=1234)
    controller = make_controller(model=model)
    controller.append_output("assistant wide response")

    rendered = controller._render_for_size(
        terminal_columns=140,
        terminal_rows=10,
        input_text="draft text",
    )

    assert "assistant wide response" in rendered
    assert "INPUT>" in rendered
    assert "draft text" in rendered
    assert "Agent Inspector" in rendered
    assert "session-wide" in rendered
    assert last_non_empty_line(rendered).startswith("INPUT>")
    assert "Agent Inspector" not in last_non_empty_line(rendered)


def test_narrow_layout_includes_conversation_bottom_input_and_status_rail() -> None:
    model = RuntimeStatusModel()
    model.update_tokens(context_usage=0.42)
    model.upsert_subagent("agent-1", task_name="review", status="running")
    controller = make_controller(model=model)
    controller.append_output("narrow conversation")

    rendered = controller._render_for_size(
        terminal_columns=80,
        terminal_rows=10,
        input_text="ask",
    )

    assert "narrow conversation" in rendered
    assert "INPUT>" in rendered
    assert "ask" in rendered
    assert "Agent Inspector" not in rendered
    assert "42%" in rendered
    assert "SA 1" in rendered
    assert last_non_empty_line(rendered).startswith("INPUT>")


def test_status_region_never_covers_the_bottom_input_line() -> None:
    model = RuntimeStatusModel()
    model.update_health(retry_info="retry 1/3", last_error="timeout")
    controller = make_controller(model=model)
    controller.append_output("conversation body")

    rendered = controller._render_for_size(
        terminal_columns=140,
        terminal_rows=6,
        input_text="bottom draft",
    )

    last_line = last_non_empty_line(rendered)
    assert "INPUT>" in last_line
    assert "bottom draft" in last_line
    assert "retry 1/3" not in last_line
    assert "timeout" not in last_line


def test_conversation_body_draws_boundaries_and_wraps_wide_text() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output("你好" * 12 + " done")

    rendered = controller._render_body_for_size(
        terminal_columns=34,
        terminal_rows=6,
    )

    lines = rendered.splitlines()
    assert lines[0] == "+" + "-" * 32 + "+"
    assert lines[-1] == "+" + "-" * 32 + "+"
    assert all(line.startswith("|") and line.endswith("|") for line in lines[1:-1])
    assert all(get_cwidth(line) <= 34 for line in lines)
    assert any("done" in line for line in lines)
    assert sum(1 for line in lines if "你好" in line) >= 2


def test_wrapped_single_line_transcript_can_scroll_to_earlier_visual_rows() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output("start " + ("你好" * 30) + " end")

    bottom = controller._render_body_for_size(
        terminal_columns=34,
        terminal_rows=5,
    )
    assert "end" in bottom

    controller._scroll_lines(-10)
    scrolled = controller._render_body_for_size(
        terminal_columns=34,
        terminal_rows=5,
    )

    assert "Agent  | start" in scrolled


def test_render_frame_pads_every_line_to_terminal_width() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output("long output " + ("x" * 80))
    controller._render_for_size(terminal_columns=64, terminal_rows=6)
    controller.append_output("short")

    rendered = controller._render_for_size(
        terminal_columns=64,
        terminal_rows=6,
        input_text="",
    )

    assert all(get_cwidth(line) == 64 for line in rendered.splitlines())


def test_role_markers_and_spacing_distinguish_user_and_agent_turns() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_user_input("hello")
    controller.append_output("hi there")

    lines = controller._conversation_lines(height=6, width=50)

    assert "You    | hello" in lines
    assert "Agent  | hi there" in lines
    assert "" in lines[lines.index("You    | hello") + 1 : lines.index("Agent  | hi there")]


def test_agent_compact_markdownish_response_gets_readable_breaks() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output(
        "我是 MyAgent，可以帮你完成任务。 ---### **文件与代码操作**"
        "- 读写、编辑文件 - 用 glob 模式匹配查找文件### 🌐 **网络能力**"
        "- 网页搜索 - 抓取网页内容"
    )

    lines = controller._conversation_lines(height=14, width=88)

    assert any("Agent  | 我是 MyAgent，可以帮你完成任务。" in line for line in lines)
    assert any("       | 文件与代码操作" in line for line in lines)
    assert any("       | - 读写、编辑文件" in line for line in lines)
    assert any("       | - 用 glob 模式匹配查找文件" in line for line in lines)
    assert any("       | 🌐 网络能力" in line for line in lines)
    assert any("       | - 网页搜索" in line for line in lines)
    assert all("###" not in line and "**" not in line and "---" not in line for line in lines)


def test_agent_inline_heading_without_rule_gets_readable_breaks() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output("Intro### Notes- first - second")

    lines = controller._conversation_lines(height=8, width=72)

    assert any("Agent  | Intro" in line for line in lines)
    assert any("       | Notes" in line for line in lines)
    assert any("       | - first" in line for line in lines)
    assert any("       | - second" in line for line in lines)
    assert all("###" not in line for line in lines)


def test_agent_dash_separated_feature_list_gets_readable_item_lines() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output(
        "### Files\n"
        "- Read - Inspect any file contents - Write - Create or overwrite files "
        "- Search - Find content with regex"
    )

    lines = controller._conversation_lines(height=10, width=72)

    assert any("Agent  | Files" in line for line in lines)
    assert any("       | - Read: Inspect any file contents" in line for line in lines)
    assert any("       | - Write: Create or overwrite files" in line for line in lines)
    assert any("       | - Search: Find content with regex" in line for line in lines)
    assert not any("Read - Inspect any file contents - Write" in line for line in lines)


def test_agent_compact_ordered_list_gets_readable_item_lines() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output(
        "Try these options: 1. **Phone weather app** - fastest "
        "2. **Weather site** - https://example.test search city "
        "3. **Meteorological office** - https://meteo.example.test"
    )

    lines = controller._conversation_lines(height=8, width=96)

    assert any("Agent  | Try these options:" in line for line in lines)
    assert any("       | 1. Phone weather app - fastest" in line for line in lines)
    assert any(
        "       | 2. Weather site - https://example.test search city" in line
        for line in lines
    )
    assert any(
        "       | 3. Meteorological office - https://meteo.example.test" in line
        for line in lines
    )
    assert not any("1. Phone weather app - fastest 2." in line for line in lines)


def test_agent_collapsed_markdown_table_gets_readable_rows() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output(
        "### Use cases | Scenario | I can do | --- | --- | Code | Create features | "
        "Review | Find bugs | Docs | Write README |"
    )

    lines = controller._conversation_lines(height=10, width=80)

    assert any("Agent  | Use cases" in line for line in lines)
    assert any("Scenario" in line and "I can do" in line for line in lines)
    assert any("Code" in line and "Create features" in line for line in lines)
    assert any("Review" in line and "Find bugs" in line for line in lines)
    assert any("Docs" in line and "Write README" in line for line in lines)
    assert not any("---" in line or "| ---" in line for line in lines)


def test_agent_pure_collapsed_markdown_table_gets_readable_rows() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output(
        "| Scenario | I can do | --- | --- | Code | Create features | "
        "Review | Find bugs |"
    )

    lines = controller._conversation_lines(height=8, width=80)

    assert any("Agent  | Scenario" in line and "I can do" in line for line in lines)
    assert any("       | Code" in line and "Create features" in line for line in lines)
    assert any("       | Review" in line and "Find bugs" in line for line in lines)
    assert not any("---" in line or "| ---" in line for line in lines)


def test_agent_compact_fenced_directory_tree_gets_readable_lines() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output(
        "Project overview:\n"
        "```D:\\code\\test\\├── calculator.html <- calculator app"
        "├── .myagent\\ <- agent config"
        "└── memory\\├── MEMORY.md <- memory index"
        "└── dev-team.md <- team config```"
    )

    lines = controller._conversation_lines(height=12, width=120)

    assert any("Agent  | Project overview:" in line for line in lines)
    assert any("       | D:\\code\\test\\" in line for line in lines)
    assert any(
        "       | ├── calculator.html <- calculator app" in line for line in lines
    )
    assert any("       | ├── .myagent\\ <- agent config" in line for line in lines)
    assert any("       | └── memory\\" in line for line in lines)
    assert any("       | ├── MEMORY.md <- memory index" in line for line in lines)
    assert any("       | └── dev-team.md <- team config" in line for line in lines)
    assert not any("```D:\\code\\test\\├──" in line for line in lines)


def test_agent_shell_pipeline_bullet_stays_literal() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output("- Run `cat log.txt | grep error | sort`")

    lines = controller._conversation_lines(height=4, width=90)

    assert any("Agent  | - Run `cat log.txt | grep error | sort`" in line for line in lines)
    assert not any("grep error | sort" in line for line in lines if "cat log" not in line)


def test_agent_compact_markdown_table_with_row_separators_and_code_cells() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output(
        "✅ 开发团队创建完成！ 以下是团队成员： "
        "| # | 角色 | ID | 状态 ||---|------|----|------|| "
        "🎯 | 产品经理 | `pm` | 待命 || 🏗️ | 架构师 | `architect` | 待命 || "
        "📝 | 文档 Reviewer | `doc-reviewer` | 待命 ||"
    )

    lines = controller._conversation_lines(height=12, width=110)

    assert any("开发团队创建完成" in line for line in lines)
    assert any("#" in line and "角色" in line and "ID" in line and "状态" in line for line in lines)
    assert any("产品经理" in line and "pm" in line and "待命" in line for line in lines)
    assert any("架构师" in line and "architect" in line and "待命" in line for line in lines)
    assert any("文档 Reviewer" in line and "doc-reviewer" in line for line in lines)
    assert not any("---" in line or "`" in line for line in lines)


def test_agent_plain_pipe_text_after_heading_stays_literal() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output("### Notes\nRun cat log.txt | grep error | sort")

    lines = controller._conversation_lines(height=6, width=90)

    assert any("Agent  | Notes" in line for line in lines)
    assert any("       | Run cat log.txt | grep error | sort" in line for line in lines)
    assert not any("grep error | sort" in line for line in lines if "Run cat" not in line)


def test_agent_standard_markdown_table_gets_readable_rows() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output(
        "| Scenario | I can do |\n"
        "| --- | --- |\n"
        "| Code | Create features |\n"
        "| Review | Find bugs |"
    )

    lines = controller._conversation_lines(height=8, width=80)

    assert any("Agent  | Scenario" in line and "I can do" in line for line in lines)
    assert any("       | Code" in line and "Create features" in line for line in lines)
    assert any("       | Review" in line and "Find bugs" in line for line in lines)
    assert not any("---" in line for line in lines)


def test_agent_existing_markdown_line_breaks_stay_readable() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output(
        "可以。\n\n### **步骤**\n- 读取文件\n- 运行测试\n\n```python\nprint('hi')\n```"
    )

    lines = controller._conversation_lines(height=12, width=72)

    assert any("Agent  | 可以。" in line for line in lines)
    assert any("       | 步骤" in line for line in lines)
    assert any("       | - 读取文件" in line for line in lines)
    assert any("       | - 运行测试" in line for line in lines)
    assert any("       | ```python" in line for line in lines)
    assert any("       | print('hi')" in line for line in lines)


def test_agent_line_start_markdown_heading_does_not_leave_marker_fragment() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output("### Notes")

    lines = controller._conversation_lines(height=4, width=72)

    assert any("Agent  | Notes" in line for line in lines)
    assert not any(line in {"Agent  | #", "       | #"} for line in lines)


def test_agent_bullet_with_ordinary_hyphen_stays_one_item() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output("- Keep A - B mapping intact")

    lines = controller._conversation_lines(height=4, width=72)

    assert any("Agent  | - Keep A - B mapping intact" in line for line in lines)
    assert not any("- B mapping intact" in line for line in lines if "Keep A" not in line)


def test_agent_unclosed_code_fence_is_preserved_during_streaming() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output(
        'Intro\n```python\n# heading\nprint("a - b")',
        end="",
    )

    lines = controller._conversation_lines(height=8, width=72)

    assert any("Agent  | Intro" in line for line in lines)
    assert any("       | ```python" in line for line in lines)
    assert any("       | # heading" in line for line in lines)
    assert any('       | print("a - b")' in line for line in lines)


def test_streaming_split_ansi_sequences_do_not_leave_visible_fragments() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())

    controller.append_output("before \x1b[2", end="")
    controller.append_output("K clean \x1b[?25", end="")
    controller.append_output("h after", end="\n")

    lines = controller._conversation_lines(height=4, width=90)

    assert any("Agent  | before  clean  after" in line for line in lines)
    assert not any("[2" in line or "K clean" in line or "[?25" in line for line in lines)


def test_agent_plain_prose_with_markdown_characters_stays_inline() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output(
        "Use **kwargs** - it keeps the API flexible. Search for # TODO in the file."
    )

    lines = controller._conversation_lines(height=4, width=100)

    assert any(
        "Agent  | Use **kwargs** - it keeps the API flexible. Search for # TODO in the file."
        in line
        for line in lines
    )
    assert not any(line.startswith("       | - it keeps") for line in lines)
    assert not any(line.strip() == "TODO in the file." for line in lines)


def test_agent_line_start_todo_marker_stays_literal() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output("# TODO in the file")

    lines = controller._conversation_lines(height=4, width=72)

    assert any("Agent  | # TODO in the file" in line for line in lines)
    assert not any(line.strip() == "TODO in the file" for line in lines)


def test_agent_code_fence_keeps_multiple_blank_lines() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.append_output("### Notes\n```text\nalpha\n\n\nbeta\n```")

    lines = controller._conversation_lines(height=8, width=72)
    alpha_index = lines.index("       | alpha")
    beta_index = lines.index("       | beta")

    assert lines[alpha_index + 1 : beta_index] == ["       | ", "       | "]


def test_submissions_waiting_for_agent_are_visible_as_queue() -> None:
    submitted: list[str] = []
    transcript = TranscriptBuffer()
    controller = make_controller(transcript=transcript, status_pane=EmptyStatusPane())
    controller._on_submit = submitted.append
    controller.set_agent_running(True)

    controller._handle_submit("queued question")

    assert submitted == ["queued question"]
    assert transcript.visible_entries(10) == []
    queued_lines = controller._conversation_lines(height=6, width=60)
    assert "Queue  | 1 pending" in queued_lines
    assert any("1. queued question" in line for line in queued_lines)

    controller.mark_submission_started("queued question")

    assert transcript.visible_entries(10)[-1].role == "user"
    assert transcript.visible_entries(10)[-1].plain_text == "queued question"
    assert not any("Queue  |" in line for line in controller._conversation_lines(6, 60))


def test_goal_submission_during_agent_run_does_not_enter_visible_queue() -> None:
    submitted: list[str] = []
    controller = make_controller(status_pane=EmptyStatusPane())
    controller._on_submit = submitted.append
    controller.set_agent_running(True)

    controller._handle_submit("/goal ship it")

    assert submitted == ["/goal ship it"]
    assert not any("Queue  |" in line for line in controller._conversation_lines(6, 60))


def test_long_queued_submission_preserves_queue_header_in_small_viewport() -> None:
    controller = make_controller(status_pane=EmptyStatusPane())
    controller.set_agent_running(True)
    controller._on_submit = lambda text: None

    controller._handle_submit("queued " * 30)

    queued_lines = controller._conversation_lines(height=3, width=32)

    assert queued_lines[0] == "Queue  | 1 pending"
    assert any("1. queued" in line for line in queued_lines)


def test_append_methods_update_transcript_and_refresh_once_per_append() -> None:
    transcript = TranscriptBuffer()
    controller = make_controller(transcript=transcript)
    controller.refresh = Mock()

    operations = [
        (lambda: controller.append_user_input("hello"), "user", "hello"),
        (lambda: controller.append_output("assistant text"), "assistant", "assistant text"),
        (
            lambda: controller.append_output(Panel("panel body", title="Panel Title")),
            "assistant",
            "panel body",
        ),
        (lambda: controller.append_system("system text"), "system", "system text"),
        (lambda: controller.append_error("error text"), "error", "error text"),
    ]

    for operation, role, expected_text in operations:
        controller.refresh.reset_mock()
        operation()
        assert controller.refresh.call_count == 1
        entry = transcript.visible_entries(100)[-1]
        assert entry.role == role
        assert expected_text in entry.plain_text

    panel_entry = transcript.visible_entries(100)[2]
    assert "Panel Title" in panel_entry.plain_text
    assert "<rich.panel.Panel object" not in panel_entry.plain_text


def test_status_only_refresh_does_not_append_to_transcript() -> None:
    model = RuntimeStatusModel()
    transcript = TranscriptBuffer()
    controller = make_controller(transcript=transcript, model=model)

    model.update_tokens(session_total=999)
    controller.refresh()

    assert transcript.visible_entries(10) == []


def test_page_and_wheel_actions_move_transcript_viewport_and_refresh() -> None:
    transcript = TranscriptBuffer()
    controller = make_controller(transcript=transcript)
    for index in range(8):
        transcript.append_assistant(f"line-{index}")
    controller._render_for_size(terminal_columns=100, terminal_rows=6)
    controller.refresh = Mock()

    controller._page(-1)
    assert visible_text(transcript, 3) == ["line-2", "line-3", "line-4"]
    assert controller.refresh.call_count == 1

    controller._page(1)
    assert visible_text(transcript, 3) == ["line-5", "line-6", "line-7"]

    controller._scroll_lines(-3)
    assert visible_text(transcript, 3) == ["line-2", "line-3", "line-4"]

    controller._scroll_lines(3)
    assert visible_text(transcript, 3) == ["line-5", "line-6", "line-7"]
    assert transcript.unread_count == 0


def test_mouse_wheel_on_body_scrolls_visual_transcript() -> None:
    transcript = TranscriptBuffer()
    controller = make_controller(transcript=transcript, status_pane=EmptyStatusPane())
    controller._build_layout()
    for index in range(8):
        controller.append_output(f"line-{index}")
    controller._render_body_for_size(terminal_columns=100, terminal_rows=5)

    event = SimpleNamespace(
        event_type=MouseEventType.SCROLL_UP,
        position=SimpleNamespace(x=0, y=0),
    )
    result = controller._body_control.mouse_handler(event)
    scrolled = controller._render_body_for_size(terminal_columns=100, terminal_rows=5)

    assert result is None
    assert "line-2" in scrolled
    assert "line-7" not in scrolled


def test_scroll_moves_within_single_multiline_transcript_entry() -> None:
    transcript = TranscriptBuffer()
    controller = make_controller(transcript=transcript)
    controller.append_output("\n".join(f"line-{index}" for index in range(10)))

    bottom = controller._render_body_for_size(terminal_columns=100, terminal_rows=5)
    assert "line-7" in bottom
    assert "line-8" in bottom
    assert "line-9" in bottom

    controller._scroll_lines(-3)
    scrolled = controller._render_body_for_size(terminal_columns=100, terminal_rows=5)

    assert "line-4" in scrolled
    assert "line-5" in scrolled
    assert "line-6" in scrolled
    assert "line-9" not in scrolled


def test_new_output_follows_bottom_only_while_viewport_is_at_bottom() -> None:
    transcript = TranscriptBuffer(follow_output="auto")
    controller = make_controller(transcript=transcript)
    for index in range(5):
        transcript.append_assistant(f"line-{index}")
    controller._render_for_size(terminal_columns=100, terminal_rows=4)

    controller.append_output("line-5")
    assert visible_text(transcript, 3) == ["line-3", "line-4", "line-5"]
    assert transcript.unread_count == 0

    controller._scroll_lines(-2)
    before_output = visible_text(transcript, 3)
    controller.append_output("line-6")

    assert visible_text(transcript, 3) == before_output
    assert transcript.unread_count == 1

    controller._scroll_lines(100)
    assert visible_text(transcript, 3) == ["line-4", "line-5", "line-6"]
    assert transcript.unread_count == 0


def test_unread_marker_does_not_evict_scrolled_transcript_lines() -> None:
    transcript = TranscriptBuffer(follow_output="auto")
    controller = make_controller(transcript=transcript)
    for index in range(8):
        transcript.append_assistant(f"line-{index}")
    controller._render_for_size(terminal_columns=100, terminal_rows=4)
    controller._scroll_lines(-3)

    before_output = controller._conversation_lines(height=3, width=100)
    controller.append_output("line-8")
    after_output = controller._conversation_lines(height=3, width=100)

    assert before_output == [
        "Agent  | line-2",
        "Agent  | line-3",
        "Agent  | line-4",
    ]
    assert "Agent  | line-2" in after_output
    assert "Agent  | line-3" in after_output
    assert any(line.startswith("Agent  | line-4") for line in after_output)
    assert any("[1 new messages]" in line for line in after_output)


def test_unread_marker_tracks_visual_scroll_inside_wrapped_single_line() -> None:
    transcript = TranscriptBuffer(follow_output="auto")
    controller = make_controller(transcript=transcript, status_pane=EmptyStatusPane())
    controller.append_output("start " + ("你好" * 30) + " end")
    controller._render_body_for_size(terminal_columns=34, terminal_rows=5)
    controller._scroll_lines(-10)

    assert transcript.unread_count == 0

    controller.append_output("new output")
    after_output = controller._conversation_lines(height=3, width=32)

    assert transcript.unread_count == 0
    assert any("[1 new messages]" in line for line in after_output)


def test_toggle_latest_tool_detail_expands_and_collapses_tool_output() -> None:
    transcript = TranscriptBuffer()
    controller = make_controller(transcript=transcript, status_pane=EmptyStatusPane())
    controller.append_tool(
        "Tool read completed (F3 for details)",
        detail_text="first detail line\nsecond detail line",
    )

    collapsed = controller._render_for_size(terminal_columns=90, terminal_rows=8)
    assert "Tool read completed" in collapsed
    assert "first detail line" not in collapsed

    assert controller.toggle_latest_tool_detail() is True

    expanded = controller._render_for_size(terminal_columns=90, terminal_rows=8)
    assert "first detail line" in expanded
    assert "second detail line" in expanded

    assert controller.toggle_latest_tool_detail() is True

    collapsed_again = controller._render_for_size(terminal_columns=90, terminal_rows=8)
    assert "first detail line" not in collapsed_again


def test_active_thinking_state_renders_above_input_without_transcript_entry() -> None:
    model = RuntimeStatusModel()
    model.update_session(thinking="Think High")
    model.update_thinking(active=True, elapsed_seconds=12.34)
    transcript = TranscriptBuffer()
    controller = make_controller(
        transcript=transcript,
        model=model,
        status_pane=EmptyStatusPane(),
    )

    rendered = controller._render_for_size(terminal_columns=80, terminal_rows=7)

    assert transcript.entries() == []
    assert "State" in rendered
    assert "Thinking 12.3s" in rendered
    assert rendered.index("State") < rendered.index("INPUT>")


def test_tool_output_uses_visual_unread_accounting_when_scrolled_up() -> None:
    transcript = TranscriptBuffer(follow_output="auto")
    controller = make_controller(transcript=transcript, status_pane=EmptyStatusPane())
    controller.append_output("start " + ("你好" * 30) + " end")
    controller._render_body_for_size(terminal_columns=34, terminal_rows=5)
    controller._scroll_lines(-10)

    controller.append_tool("Tool: read ok")
    after_output = controller._conversation_lines(height=3, width=32)

    assert transcript.unread_count == 0
    assert any("[1 new messages]" in line for line in after_output)


def test_unread_marker_overlays_long_last_line_without_adding_row() -> None:
    transcript = TranscriptBuffer(follow_output="auto")
    controller = make_controller(transcript=transcript)
    for index in range(4):
        transcript.append_assistant(f"line-{index}")
    transcript.append_assistant("x" * 80)
    for index in range(5, 8):
        transcript.append_assistant(f"line-{index}")
    controller._render_for_size(terminal_columns=100, terminal_rows=4)
    controller._scroll_lines(-3)

    before_output = controller._conversation_lines(height=3, width=32)
    controller.append_output("line-7")
    after_output = controller._conversation_lines(height=3, width=32)

    assert len(before_output) == len(after_output) == 3
    assert before_output[:2] == after_output[:2]
    assert after_output[-1].endswith("[1 new messages]")
    assert len(after_output[-1]) <= 32


def test_unread_marker_overlay_respects_full_width_terminal_cells() -> None:
    transcript = TranscriptBuffer(follow_output="auto")
    controller = make_controller(transcript=transcript)
    for index in range(4):
        transcript.append_assistant(f"line-{index}")
    transcript.append_assistant("界" * 20)
    for index in range(5, 8):
        transcript.append_assistant(f"line-{index}")
    controller._render_for_size(terminal_columns=100, terminal_rows=4)
    controller._scroll_lines(-3)

    controller.append_output("line-8")
    after_output = controller._conversation_lines(height=3, width=32)

    assert len(after_output) == 3
    assert after_output[-1].endswith("[1 new messages]")
    assert get_cwidth(after_output[-1]) <= 32


@pytest.mark.asyncio
async def test_run_starts_full_screen_application_and_request_stop_exits_it(
    monkeypatch,
) -> None:
    import myagent.cli.chat_window as chat_window

    FakeApplication.instances = []
    monkeypatch.setattr(chat_window, "Application", FakeApplication)
    controller = make_controller()

    run_task = asyncio.create_task(controller.run(lambda text: None))
    while not FakeApplication.instances:
        await asyncio.sleep(0)

    app = FakeApplication.instances[0]
    assert controller.is_running is True
    assert app.kwargs["full_screen"] is True
    assert app.kwargs["mouse_support"] is True
    assert "layout" in app.kwargs
    assert "key_bindings" in app.kwargs

    controller.request_stop()
    await run_task

    assert app.exit_calls == 1
    assert controller.is_running is False


@pytest.mark.asyncio
async def test_run_can_opt_out_of_mouse_support_for_native_selection(
    monkeypatch,
) -> None:
    import myagent.cli.chat_window as chat_window

    FakeApplication.instances = []
    monkeypatch.setattr(chat_window, "Application", FakeApplication)
    config = make_config()
    config.ui.chat_window.mouse_support = False
    controller = make_controller(config=config)

    run_task = asyncio.create_task(controller.run(lambda text: None))
    while not FakeApplication.instances:
        await asyncio.sleep(0)

    app = FakeApplication.instances[0]
    assert app.kwargs["mouse_support"] is False

    controller.request_stop()
    await run_task


@pytest.mark.asyncio
async def test_stopping_with_pending_ask_returns_none_promptly() -> None:
    controller = make_controller()

    ask_task = asyncio.create_task(controller.ask("Stop now?", timeout=60))
    await asyncio.sleep(0)

    controller.request_stop()

    assert await asyncio.wait_for(ask_task, timeout=0.2) is None


@pytest.mark.asyncio
async def test_request_stop_is_safe_after_app_exit_and_when_repeated(monkeypatch) -> None:
    import myagent.cli.chat_window as chat_window

    FakeApplication.instances = []
    monkeypatch.setattr(chat_window, "Application", ExitOnceApplication)
    controller = make_controller()

    await controller.run(lambda text: None)

    assert controller.is_running is False
    controller.request_stop()
    controller.request_stop()

    app = FakeApplication.instances[0]
    assert app.exit_calls == 0


def test_set_agent_running_changes_ctrl_c_behavior_through_input_controller() -> None:
    controller = make_controller()
    interrupts: list[str] = []
    controller._on_interrupt = lambda: interrupts.append("interrupt")

    controller.set_agent_running(True)
    running_buffer = invoke_binding(
        controller._key_bindings,
        (Keys.ControlC,),
        "draft",
    )

    assert interrupts == ["interrupt"]
    assert running_buffer.reset_calls == 0

    controller.set_agent_running(False)
    idle_buffer = invoke_binding(
        controller._key_bindings,
        (Keys.ControlC,),
        "draft",
    )

    assert interrupts == ["interrupt"]
    assert idle_buffer.reset_calls == 1


def test_idle_empty_ctrl_c_requires_confirmation_before_exiting() -> None:
    transcript = TranscriptBuffer()
    controller = make_controller(transcript=transcript)
    app = FakeExitApp()
    controller._app = app
    controller._is_running = True

    first_buffer = invoke_binding(controller._key_bindings, (Keys.ControlC,), "")

    assert first_buffer.reset_calls == 0
    assert app.exit_calls == 0
    assert controller.is_running is True
    assert app.invalidate_calls == 1
    assert transcript.visible_entries(10)[-1].role == "system"
    assert (
        transcript.visible_entries(10)[-1].plain_text
        == "Press Ctrl+C again or Ctrl+D to exit."
    )

    invoke_binding(controller._key_bindings, (Keys.ControlC,), "")

    assert app.exit_calls == 1
    assert controller.is_running is False


def test_ctrl_d_exits_idle_empty_chat_window_without_confirmation() -> None:
    controller = make_controller()
    app = FakeExitApp()
    controller._app = app
    controller._is_running = True

    invoke_binding(controller._key_bindings, (Keys.ControlD,), "")

    assert app.exit_calls == 1
    assert controller.is_running is False


def test_non_empty_ctrl_c_clears_text_and_cancels_exit_confirmation() -> None:
    transcript = TranscriptBuffer()
    controller = make_controller(transcript=transcript)
    app = FakeExitApp()
    controller._app = app
    controller._is_running = True

    invoke_binding(controller._key_bindings, (Keys.ControlC,), "")
    draft_buffer = invoke_binding(controller._key_bindings, (Keys.ControlC,), "draft")
    invoke_binding(controller._key_bindings, (Keys.ControlC,), "")

    assert draft_buffer.reset_calls == 1
    assert draft_buffer.text == ""
    assert app.exit_calls == 0
    assert controller.is_running is True
    assert [
        entry.plain_text
        for entry in transcript.visible_entries(10)
        if entry.role == "system"
    ] == [
        "Press Ctrl+C again or Ctrl+D to exit.",
        "Press Ctrl+C again or Ctrl+D to exit.",
    ]


def test_live_text_area_height_updates_for_multiline_drafts() -> None:
    controller = make_controller(
        config=make_config_with_chat_lines(input_min_lines=1, input_max_lines=3)
    )
    controller._build_layout()
    assert controller._input_field is not None

    controller._input_field.buffer.text = "one"
    controller._body_text()
    assert controller._input_field.window.height == 1

    controller._input_field.buffer.text = "one\ntwo\nthree\nfour"
    controller._body_text()
    assert controller._input_field.window.height == 3


def make_config_with_chat_lines(
    *,
    input_min_lines: int,
    input_max_lines: int,
) -> SimpleNamespace:
    config = make_config()
    config.ui.chat_window.input_min_lines = input_min_lines
    config.ui.chat_window.input_max_lines = input_max_lines
    return config


@pytest.mark.asyncio
async def test_ask_collects_one_response_through_bottom_input() -> None:
    transcript = TranscriptBuffer()
    controller = make_controller(transcript=transcript)

    ask_task = asyncio.create_task(controller.ask("Need a value?", timeout=1))
    await asyncio.sleep(0)
    assert transcript.visible_entries(10)[-1].plain_text == "Need a value?"

    controller._handle_submit("chosen value")

    assert await ask_task == "chosen value"
    assert transcript.visible_entries(10)[-1].role == "user"
    assert transcript.visible_entries(10)[-1].plain_text == "chosen value"


@pytest.mark.asyncio
async def test_transient_ask_renders_above_input_without_transcript_entry() -> None:
    transcript = TranscriptBuffer()
    controller = make_controller(transcript=transcript, status_pane=EmptyStatusPane())

    ask_task = asyncio.create_task(
        controller.ask(
            "Permission required for tool: bash\n[A] allow once  [D] deny",
            timeout=1,
            transient=True,
        )
    )
    await asyncio.sleep(0)

    rendered = controller._render_for_size(terminal_columns=80, terminal_rows=8)

    assert transcript.entries() == []
    assert "Permission required for tool: bash" in rendered
    assert rendered.index("Permission required") < rendered.index("INPUT>")

    controller._handle_submit("a")

    assert await ask_task == "a"
    assert transcript.entries() == []
    assert "Permission required" not in controller._render_for_size(
        terminal_columns=80,
        terminal_rows=8,
    )


@pytest.mark.asyncio
async def test_run_logs_and_reraises_startup_exception(monkeypatch, caplog) -> None:
    import myagent.cli.chat_window as chat_window

    class BrokenApplication:
        def __init__(self, **kwargs) -> None:
            raise RuntimeError("terminal unavailable")

    monkeypatch.setattr(chat_window, "Application", BrokenApplication)
    controller = make_controller()
    caplog.set_level(logging.ERROR, logger="myagent.cli.chat_window")

    with pytest.raises(RuntimeError, match="terminal unavailable"):
        await controller.run(lambda text: None)

    record = next(
        record
        for record in caplog.records
        if getattr(record, "context", "") == "cli_chat_window_start"
    )
    assert record.category == "error"
    assert record.component == "agent"
    assert record.exception_type == "RuntimeError"
    assert "terminal unavailable" in record.traceback


def test_render_exception_is_logged_and_falls_back_to_transcript_only(caplog) -> None:
    class BrokenStatusPane:
        def get_renderable(self, terminal_columns=None):
            raise RuntimeError("status render failed")

        def preferred_width(self, terminal_columns=None) -> int:
            return 34

    controller = make_controller(status_pane=BrokenStatusPane())
    controller.append_output("still visible")
    caplog.set_level(logging.ERROR, logger="myagent.cli.chat_window")

    rendered = controller._render_for_size(terminal_columns=140, terminal_rows=8)

    assert "still visible" in rendered
    assert "INPUT>" in rendered
    record = next(
        record
        for record in caplog.records
        if getattr(record, "context", "") == "cli_chat_window_render"
    )
    assert record.category == "error"
    assert record.component == "agent"
    assert record.exception_type == "RuntimeError"
    assert "status render failed" in record.traceback
