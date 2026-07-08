"""Tests for last prompt capture formatting and serialization."""

import json

from myagent.agent.prompt_capture import LastPromptCapture


def test_last_prompt_capture_text_includes_metadata_messages_and_tools():
    capture = LastPromptCapture.capture(
        model="deepseek-v4-pro",
        thinking="Think High",
        messages=[
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Build the thing"},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "parameters": {"type": "object"},
                },
            }
        ],
        estimated_tokens=123,
        captured_at="2026-07-08T12:34:56+00:00",
    )

    text = capture.to_text()

    assert "Last LLM prompt" in text
    assert "Model: deepseek-v4-pro" in text
    assert "Thinking: Think High" in text
    assert "Messages: 2" in text
    assert "Tools: 1" in text
    assert "Estimated tokens: 123" in text
    assert "[1] system" in text
    assert "System prompt" in text
    assert "[2] user" in text
    assert "Build the thing" in text
    assert "Tools:" in text
    assert "[1] read" in text
    assert '"parameters"' in text


def test_last_prompt_capture_json_uses_stable_keys_and_preserves_content():
    capture = LastPromptCapture.capture(
        model="model",
        thinking="Non-think",
        messages=[{"role": "user", "content": "hello"}],
        tools=[{"function": {"name": "write", "metadata": {"value": object()}}}],
        estimated_tokens=None,
        captured_at="2026-07-08T12:34:56+00:00",
    )

    data = json.loads(capture.to_json())

    assert list(data.keys()) == [
        "captured_at",
        "model",
        "thinking",
        "estimated_tokens",
        "message_count",
        "tool_count",
        "messages",
        "tools",
    ]
    assert data["messages"] == [{"role": "user", "content": "hello"}]
    assert data["tools"][0]["function"]["name"] == "write"
    assert isinstance(data["tools"][0]["function"]["metadata"]["value"], str)


def test_last_prompt_capture_deep_copies_input_lists():
    messages = [{"role": "user", "content": "before"}]
    tools = [{"function": {"name": "read"}}]

    capture = LastPromptCapture.capture(
        model="model",
        thinking="Think High",
        messages=messages,
        tools=tools,
        captured_at="2026-07-08T12:34:56+00:00",
    )
    messages[0]["content"] = "after"
    tools[0]["function"]["name"] = "write"

    assert capture.messages[0]["content"] == "before"
    assert capture.tools[0]["function"]["name"] == "read"
