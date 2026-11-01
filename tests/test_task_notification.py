"""Background-task <task-notification> messages become structured system events."""
import json

from ai_replay.parser import parse_session

NEW_FORMAT = (
    "<task-notification>\n"
    "<task-id>b3m64fiyo</task-id>\n"
    "<tool-use-id>toolu_01FpGPvR6rpBHhtznpbL9XM1</tool-use-id>\n"
    "<output-file>/tmp/tasks/b3m64fiyo.output</output-file>\n"
    "<status>completed</status>\n"
    '<summary>Background command "Flash, set clock" completed (exit code 0)</summary>\n'
    "</task-notification>"
)

OLD_FORMAT = (
    "<task-notification>\n<task-id>abc</task-id>\n"
    "<output-file>/tmp/abc.output</output-file>\n"
    "<status>failed</status>\n"
    "<summary>Background command \"build\" failed with exit code 1</summary>\n"
    "</task-notification>\n"
    "Read the output file to retrieve the result: /tmp/abc.output"
)


def _session(tmp_path, notification):
    lines = [
        {"type": "user", "timestamp": "2026-01-01T00:00:00Z",
         "message": {"role": "user", "content": "start the build"}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:01Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Started."}]}},
        {"type": "user", "timestamp": "2026-01-01T00:01:00Z",
         "message": {"role": "user", "content": notification}},
        {"type": "assistant", "timestamp": "2026-01-01T00:01:01Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}},
    ]
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    return parse_session(str(p))


def test_new_format_with_tool_use_id(tmp_path):
    turns = _session(tmp_path, NEW_FORMAT)
    t = turns[1]
    assert t["user_text"] == ""
    assert t["system_events"] == [{
        "kind": "bg-task",
        "status": "completed",
        "text": 'Background command "Flash, set clock" completed (exit code 0)',
    }]


def test_old_format_and_read_output_hint(tmp_path):
    turns = _session(tmp_path, OLD_FORMAT)
    t = turns[1]
    assert t["user_text"] == ""
    assert t["system_events"][0]["status"] == "failed"
    assert "task-notification" not in json.dumps(turns)
