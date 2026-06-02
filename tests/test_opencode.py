"""Tests for OpenCode (`opencode export`) transcript parsing."""

import json
from pathlib import Path

import pytest

from ai_replay.parser import detect_format, parse_session

FIXTURE = Path(__file__).parent / "fixtures" / "opencode_session.json"


def test_detect_opencode_format():
    """An `opencode export` JSON object is detected as the opencode format."""
    assert detect_format(FIXTURE) == "opencode"


def test_detect_opencode_does_not_collide_with_replay():
    """A replay JSONL stream is not misdetected as opencode."""
    # Replay format is JSONL with user_text/blocks per line.
    text = json.dumps({"index": 1, "user_text": "hi", "blocks": []})
    from ai_replay.parser import _detect_format_from_text

    assert _detect_format_from_text(text) == "replay"


def test_opencode_turns_grouped_by_user_message():
    """Turns are split on user messages; assistant parts attach to the open turn."""
    turns = parse_session(FIXTURE)
    assert len(turns) == 2
    assert turns[0]["user_text"] == "add a hello function"
    assert turns[1]["user_text"] == "thanks"
    # Indices are 1-based and contiguous.
    assert [t["index"] for t in turns] == [1, 2]


def test_opencode_block_kinds():
    """reasoning -> thinking, text -> text, tool -> tool_use; step-* ignored."""
    turns = parse_session(FIXTURE)
    kinds = [b["kind"] for b in turns[0]["blocks"]]
    # thinking, text, then three tool_use blocks (write, edit, bash).
    assert kinds == ["thinking", "text", "tool_use", "tool_use", "tool_use"]
    assert turns[0]["blocks"][0]["text"] == "I should write the function."


def test_opencode_tool_name_mapping_and_input_normalization():
    """Lowercase tool names map to TitleCase; input keys are normalized."""
    turns = parse_session(FIXTURE)
    tools = [
        b["tool_call"] for b in turns[0]["blocks"] if b["kind"] == "tool_use"
    ]
    write, edit, bash = tools

    assert write["name"] == "Write"
    assert write["input"] == {
        "file_path": "/Users/me/Devel/my-project/hello.py",
        "content": "def hello():\n    return 'hi'\n",
    }

    assert edit["name"] == "Edit"
    assert edit["input"]["file_path"] == "/Users/me/Devel/my-project/hello.py"
    assert edit["input"]["old_string"] == "return 'hi'"
    assert edit["input"]["new_string"] == "return 'hello'"

    assert bash["name"] == "Bash"
    # workdir is folded into the command.
    assert bash["input"] == {"command": "cd /Users/me/Devel/my-project && pytest"}


def test_opencode_tool_results_and_errors():
    """Tool output becomes result; error status / nonzero exit set is_error."""
    turns = parse_session(FIXTURE)
    tools = {
        b["tool_call"]["name"]: b["tool_call"]
        for b in turns[0]["blocks"]
        if b["kind"] == "tool_use"
    }
    assert tools["Write"]["result"] == "File written"
    assert tools["Write"]["is_error"] is False
    # bash had status "error" and exit 1.
    assert tools["Bash"]["is_error"] is True
    assert tools["Bash"]["result"] == "1 failed"


def test_opencode_timestamps_are_iso():
    """Epoch-ms times are converted to ISO-8601 strings."""
    turns = parse_session(FIXTURE)
    assert turns[0]["timestamp"] == "2026-06-02T19:45:29.475Z"
    # Tool result timestamps come from state.time.end.
    write = next(
        b["tool_call"]
        for b in turns[0]["blocks"]
        if b["kind"] == "tool_use" and b["tool_call"]["name"] == "Write"
    )
    assert write["result_timestamp"] == "2026-06-02T19:45:34.200Z"


def test_opencode_paced_timing_overrides_timestamps():
    """paced_timing replaces real timestamps with synthetic ones from epoch 0."""
    turns = parse_session(FIXTURE, paced_timing=True)
    assert turns[0]["timestamp"] == "1970-01-01T00:00:00.000Z"


def test_opencode_empty_messages():
    """An export with an empty messages list yields no turns but still detects."""
    from ai_replay.parser import _parse_opencode_format, _is_opencode_export

    export = {"info": {"id": "ses_x"}, "messages": []}
    assert _is_opencode_export(export) is True
    assert _parse_opencode_format(export) == []
