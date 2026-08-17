"""Tests for Pi (https://pi.dev) on-disk JSONL session parsing."""

import json
from pathlib import Path

import pytest

from ai_replay.parser import detect_format, parse_session

FIXTURE = Path(__file__).parent / "fixtures" / "pi_session.jsonl"


def test_detect_pi_format():
    """A Pi session file is detected as the pi format."""
    assert detect_format(FIXTURE) == "pi"


def test_detect_pi_from_session_header():
    """A lone Pi ``session`` header line is enough to detect the format."""
    from ai_replay.parser import _detect_format_from_text

    text = json.dumps(
        {"type": "session", "version": 3, "id": "x", "cwd": "/tmp/proj"}
    )
    assert _detect_format_from_text(text) == "pi"


def test_detect_pi_does_not_collide_with_claude():
    """Pi's ``type:"message"`` (role nested) is not misread as Claude/Cursor."""
    from ai_replay.parser import _detect_format_from_text

    text = json.dumps(
        {"type": "message", "id": "m", "message": {"role": "user",
         "content": [{"type": "text", "text": "hi"}]}}
    )
    assert _detect_format_from_text(text) == "pi"


def test_pi_turns_grouped_by_user_message():
    """Turns split on user messages; assistant/toolResult attach to the open turn."""
    turns = parse_session(FIXTURE)
    assert len(turns) == 2
    assert turns[0]["user_text"] == "add a hello function"
    assert turns[1]["user_text"] == "thanks"
    assert [t["index"] for t in turns] == [1, 2]


def test_pi_block_kinds():
    """thinking -> thinking, text -> text, toolCall -> tool_use; metadata ignored."""
    turns = parse_session(FIXTURE)
    kinds = [b["kind"] for b in turns[0]["blocks"]]
    # thinking, write, edit, bash, then the final text block.
    assert kinds == ["thinking", "tool_use", "tool_use", "tool_use", "text"]
    assert turns[0]["blocks"][0]["text"] == "I should write the function."
    assert turns[0]["blocks"][-1]["text"] == "Done! The hello function is added."


def test_pi_tool_name_mapping_and_input_normalization():
    """Lowercase tool names map to TitleCase; input keys are normalized."""
    turns = parse_session(FIXTURE)
    tools = [b["tool_call"] for b in turns[0]["blocks"] if b["kind"] == "tool_use"]
    write, edit, bash = tools

    assert write["name"] == "Write"
    assert write["input"] == {
        "file_path": "/Users/me/Devel/my-project/hello.py",
        "content": "def hello():\n    return 'hi'\n",
    }

    assert edit["name"] == "Edit"
    assert edit["input"] == {
        "file_path": "/Users/me/Devel/my-project/hello.py",
        "old_string": "return 'hi'",
        "new_string": "return 'hello'",
    }

    assert bash["name"] == "Bash"
    assert bash["input"] == {"command": "pytest"}


def test_pi_tool_results_and_errors():
    """toolResult content becomes the result; isError sets is_error."""
    turns = parse_session(FIXTURE)
    tools = {
        b["tool_call"]["name"]: b["tool_call"]
        for b in turns[0]["blocks"]
        if b["kind"] == "tool_use"
    }
    assert tools["Write"]["result"] == "File written"
    assert tools["Write"]["is_error"] is False
    assert tools["Bash"]["result"] == "1 failed"
    assert tools["Bash"]["is_error"] is True


def test_pi_timestamps_are_iso():
    """Entry-level ISO timestamps are used for turns, blocks, and results."""
    turns = parse_session(FIXTURE)
    assert turns[0]["timestamp"] == "2026-06-29T07:25:40.774Z"
    write = next(
        b["tool_call"]
        for b in turns[0]["blocks"]
        if b["kind"] == "tool_use" and b["tool_call"]["name"] == "Write"
    )
    assert write["result_timestamp"] == "2026-06-29T07:25:45.200Z"


def test_pi_paced_timing_overrides_timestamps():
    """paced_timing replaces real timestamps with synthetic ones from epoch 0."""
    turns = parse_session(FIXTURE, paced_timing=True)
    assert turns[0]["timestamp"] == "1970-01-01T00:00:00.000Z"


def test_pi_empty_session():
    """A session with only header/metadata entries yields no turns."""
    from ai_replay.parser import _parse_pi_format

    entries = [
        {"type": "session", "version": 3, "id": "x", "cwd": "/tmp"},
        {"type": "model_change", "id": "a", "modelId": "claude-opus-4-8"},
    ]
    assert _parse_pi_format(entries) == []
