"""Tests for GitHub Copilot CLI ``events.jsonl`` session parsing."""

import json
from pathlib import Path

from ai_replay.parser import detect_format, parse_session

FIXTURE = Path(__file__).parent / "fixtures" / "copilot_session.jsonl"


def test_detect_copilot_format():
    """A Copilot events file is detected as the copilot format."""
    assert detect_format(FIXTURE) == "copilot"


def test_detect_copilot_from_any_event():
    """A lone dotted event line is enough to detect the format."""
    from ai_replay.parser import _detect_format_from_text

    text = json.dumps(
        {"type": "user.message", "data": {"content": "hi"}, "id": "x"}
    )
    assert _detect_format_from_text(text) == "copilot"


def test_detect_copilot_does_not_collide_with_claude_or_pi():
    """Dotted event names are distinct from Claude's and Pi's type values."""
    from ai_replay.parser import _detect_format_from_text

    # Claude uses a bare "user"; Pi uses a bare "session" / "message".
    assert _detect_format_from_text(json.dumps({"type": "user"})) == "claude"
    assert (
        _detect_format_from_text(
            json.dumps({"type": "session", "version": 3, "cwd": "/tmp"})
        )
        == "pi"
    )


def test_copilot_turns_grouped_by_user_message():
    """Turns split on user.message; assistant events attach to the open turn."""
    turns = parse_session(FIXTURE)
    assert len(turns) == 2
    assert turns[0]["user_text"] == "add a hello function"
    assert turns[1]["user_text"] == "thanks"
    assert [t["index"] for t in turns] == [1, 2]


def test_copilot_uses_raw_content_not_transformed():
    """The injected system reminders in transformedContent are not shown."""
    turns = parse_session(FIXTURE)
    assert "<system_reminder>" not in turns[0]["user_text"]
    assert "current_datetime" not in turns[0]["user_text"]


def test_copilot_block_kinds():
    """reasoningText -> thinking, content -> text, toolRequests -> tool_use."""
    turns = parse_session(FIXTURE)
    kinds = [b["kind"] for b in turns[0]["blocks"]]
    assert kinds == ["thinking", "text", "tool_use", "text", "tool_use", "text"]
    assert turns[0]["blocks"][0]["text"] == "I should write the function."
    assert turns[0]["blocks"][-1]["text"] == "Done! The hello function is added."


def test_copilot_skips_system_prompt():
    """The system.message harness prompt never becomes a block."""
    turns = parse_session(FIXTURE)
    all_text = " ".join(b["text"] for t in turns for b in t["blocks"])
    assert "terminal assistant built by GitHub" not in all_text


def test_copilot_tool_name_mapping_and_input_normalization():
    """Lowercase tool names map to TitleCase; input keys are normalized."""
    turns = parse_session(FIXTURE)
    tools = [b["tool_call"] for b in turns[0]["blocks"] if b["kind"] == "tool_use"]
    write, bash = tools

    assert write["name"] == "Write"
    assert write["input"] == {
        "file_path": "/Users/me/Devel/my-project/hello.py",
        "content": "def hello():\n    return 'hi'\n",
    }

    # bash already uses Claude's key names, so it passes through untouched.
    assert bash["name"] == "Bash"
    assert bash["input"] == {"command": "pytest", "description": "Run the test suite"}


def test_copilot_snake_case_tool_names_become_titlecase():
    """An unmapped tool like ask_user renders as AskUser, not Ask_user."""
    turns = parse_session(FIXTURE)
    tool = next(
        b["tool_call"] for b in turns[1]["blocks"] if b["kind"] == "tool_use"
    )
    assert tool["name"] == "AskUser"


def test_copilot_execution_start_does_not_duplicate_tool_calls():
    """A call declared by assistant.message is not re-added by execution_start."""
    turns = parse_session(FIXTURE)
    ids = [
        b["tool_call"]["tool_use_id"]
        for t in turns
        for b in t["blocks"]
        if b["kind"] == "tool_use"
    ]
    assert ids == ["call_1", "call_2", "call_3"]
    assert len(ids) == len(set(ids))


def test_copilot_prefers_detailed_result_content():
    """The richer detailedContent wins over the short content."""
    turns = parse_session(FIXTURE)
    write = next(
        b["tool_call"]
        for b in turns[0]["blocks"]
        if b["kind"] == "tool_use" and b["tool_call"]["name"] == "Write"
    )
    assert write["result"] == "File written to /Users/me/Devel/my-project/hello.py"
    assert write["is_error"] is False


def test_copilot_failed_tool_falls_back_to_error_message():
    """A failed call has no result object, so the error message is surfaced."""
    turns = parse_session(FIXTURE)
    bash = next(
        b["tool_call"]
        for b in turns[0]["blocks"]
        if b["kind"] == "tool_use" and b["tool_call"]["name"] == "Bash"
    )
    assert bash["result"] == "The user rejected this tool call. (rejected)"
    assert bash["is_error"] is True


def test_copilot_timestamps_are_iso():
    """Envelope ISO timestamps are used for turns, blocks, and results."""
    turns = parse_session(FIXTURE)
    assert turns[0]["timestamp"] == "2026-08-11T08:14:27.109Z"
    write = next(
        b["tool_call"]
        for b in turns[0]["blocks"]
        if b["kind"] == "tool_use" and b["tool_call"]["name"] == "Write"
    )
    assert write["result_timestamp"] == "2026-08-11T08:14:29.200Z"


def test_copilot_paced_timing_overrides_timestamps():
    """paced_timing replaces real timestamps with synthetic ones from epoch 0."""
    turns = parse_session(FIXTURE, paced_timing=True)
    assert turns[0]["timestamp"] == "1970-01-01T00:00:00.000Z"


def test_copilot_empty_session():
    """A session with only bookkeeping events yields no turns."""
    from ai_replay.parser import _parse_copilot_format

    events = [
        {"type": "session.start", "data": {"sessionId": "x"}, "id": "a"},
        {"type": "session.model_change", "data": {"newModel": "gpt-5"}, "id": "b"},
        {"type": "session.shutdown", "data": {"reason": "exit"}, "id": "c"},
    ]
    assert _parse_copilot_format(events) == []


def test_copilot_orphan_execution_start_still_renders():
    """A trimmed transcript with no assistant.message still shows its tool call."""
    from ai_replay.parser import _parse_copilot_format

    events = [
        {"type": "user.message", "data": {"content": "run it"}, "id": "a",
         "timestamp": "2026-08-11T08:00:00.000Z"},
        {"type": "tool.execution_start",
         "data": {"toolCallId": "c1", "toolName": "bash",
                  "arguments": {"command": "ls"}},
         "id": "b", "timestamp": "2026-08-11T08:00:01.000Z"},
        {"type": "tool.execution_complete",
         "data": {"toolCallId": "c1", "success": True,
                  "result": {"content": "file.txt"}},
         "id": "c", "timestamp": "2026-08-11T08:00:02.000Z"},
    ]
    turns = _parse_copilot_format(events)
    assert len(turns) == 1
    tool = turns[0]["blocks"][0]["tool_call"]
    assert tool["name"] == "Bash"
    assert tool["input"] == {"command": "ls"}
    assert tool["result"] == "file.txt"
