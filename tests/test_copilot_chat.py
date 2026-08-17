"""Tests for VS Code Copilot Chat ``chatSessions/<id>.jsonl`` journal parsing."""

import json
from pathlib import Path

from ai_replay.parser import detect_format, parse_session

FIXTURE = Path(__file__).parent / "fixtures" / "copilot_chat_session.jsonl"


def test_detect_copilot_chat_format():
    """A VS Code chat journal is detected as the copilot-chat format."""
    assert detect_format(FIXTURE) == "copilot-chat"


def test_detect_copilot_chat_needs_a_session_snapshot():
    """A bare {kind, k, v} patch line is not enough to claim the format."""
    from ai_replay.parser import _detect_format_from_text

    assert _detect_format_from_text(json.dumps({"kind": 1, "k": ["x"], "v": 1})) == "unknown"


def test_detect_copilot_chat_distinct_from_copilot_cli():
    """The CLI event log and the VS Code journal are different formats."""
    from ai_replay.parser import _detect_format_from_text

    cli = json.dumps({"type": "user.message", "data": {"content": "hi"}})
    assert _detect_format_from_text(cli) == "copilot"


def test_journal_patches_are_replayed():
    """Response parts appended by a later kind-2 patch are not lost.

    The snapshot opens with an empty ``requests`` list and the streamed answer
    arrives on a separate line — reading line-by-line would drop it entirely.
    """
    turns = parse_session(FIXTURE)
    assert len(turns) == 2
    assert turns[0]["user_text"] == "add a hello function"
    assert turns[1]["user_text"] == "thanks"
    # The answer body only ever appears in the ["requests", 0, "response"] patch.
    assert any(b["text"] == "Done!" for b in turns[0]["blocks"])


def test_consecutive_text_parts_are_merged():
    """VS Code splits one answer across many parts; they become one block."""
    turns = parse_session(FIXTURE)
    texts = [b["text"] for b in turns[0]["blocks"] if b["kind"] == "text"]
    # "I'll create ", the inline reference, and " for you." merge into one block.
    assert "I'll create `hello.py` for you." in texts


def test_inline_reference_renders_as_filename():
    """An inlineReference part becomes inline code inside the surrounding prose."""
    turns = parse_session(FIXTURE)
    joined = " ".join(b["text"] for b in turns[0]["blocks"] if b["kind"] == "text")
    assert "`hello.py`" in joined


def test_empty_thinking_markers_are_skipped():
    """Reasoning-done markers carry no text and must not become blocks."""
    turns = parse_session(FIXTURE)
    thinking = [b for b in turns[0]["blocks"] if b["kind"] == "thinking"]
    assert len(thinking) == 1
    assert thinking[0]["text"] == "I should write the function."


def test_noise_parts_are_skipped():
    """mcpServersStarting / undoStop parts produce no blocks."""
    turns = parse_session(FIXTURE)
    kinds = [b["kind"] for b in turns[0]["blocks"]]
    assert kinds == ["thinking", "text", "tool_use", "tool_use", "text"]


def test_tool_id_mapping():
    """VS Code toolIds map to Claude-Code-style names."""
    turns = parse_session(FIXTURE)
    tools = [b["tool_call"] for b in turns[0]["blocks"] if b["kind"] == "tool_use"]
    assert [t["name"] for t in tools] == ["Write", "Bash"]


def test_terminal_tool_input_uses_original_command():
    """The user's command wins over VS Code's env-prefixed rewrite."""
    turns = parse_session(FIXTURE)
    bash = next(
        b["tool_call"]
        for b in turns[0]["blocks"]
        if b["kind"] == "tool_use" and b["tool_call"]["name"] == "Bash"
    )
    assert bash["input"]["command"] == "pytest"
    assert "ELECTRON_RUN_AS_NODE" not in json.dumps(bash["input"])


def test_raw_input_is_recovered_for_file_tools():
    """toolSpecificData.rawInput supplies the real arguments."""
    turns = parse_session(FIXTURE)
    write = next(
        b["tool_call"]
        for b in turns[0]["blocks"]
        if b["kind"] == "tool_use" and b["tool_call"]["name"] == "Write"
    )
    assert write["input"]["filePath"] == "/Users/me/Devel/my-project/hello.py"
    assert write["input"]["content"] == "def hello():\n    return 'hi'\n"


def test_tool_results_and_error_flag():
    """resultDetails.output is flattened; isError sets is_error."""
    turns = parse_session(FIXTURE)
    tools = {
        b["tool_call"]["name"]: b["tool_call"]
        for b in turns[0]["blocks"]
        if b["kind"] == "tool_use"
    }
    assert tools["Write"]["result"] == "File created"
    assert tools["Write"]["is_error"] is False
    assert tools["Bash"]["result"] == "1 failed"
    assert tools["Bash"]["is_error"] is True


def test_timestamps_converted_from_epoch_ms():
    """VS Code stores epoch milliseconds; turns carry ISO strings."""
    turns = parse_session(FIXTURE)
    assert turns[0]["timestamp"].startswith("2026-")
    assert turns[0]["timestamp"].endswith("Z")


def test_paced_timing_overrides_timestamps():
    """paced_timing replaces real timestamps with synthetic ones from epoch 0."""
    turns = parse_session(FIXTURE, paced_timing=True)
    assert turns[0]["timestamp"] == "1970-01-01T00:00:00.000Z"


def test_empty_session_yields_no_turns():
    """A snapshot with no requests produces nothing."""
    from ai_replay.parser import _parse_vscode_copilot_format

    lines = [{"kind": 0, "v": {"sessionId": "x", "requests": []}}]
    assert _parse_vscode_copilot_format(lines) == []


def test_patch_to_missing_path_is_ignored():
    """A patch targeting a request that does not exist must not raise."""
    from ai_replay.parser import _parse_vscode_copilot_format

    lines = [
        {"kind": 0, "v": {"sessionId": "x", "requests": []}},
        {"kind": 2, "k": ["requests", 5, "response"], "v": [{"value": "orphan"}]},
    ]
    assert _parse_vscode_copilot_format(lines) == []
