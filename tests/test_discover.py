"""Tests for the session discovery module."""

import json
import time
from pathlib import Path

import pytest

from ai_replay.discover import discover_sessions


def _write_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj) + "\n", encoding="utf-8")


def _write_jsonl_lines(path: Path, objs: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(o) for o in objs) + "\n", encoding="utf-8")


def test_discover_claude_and_codex(tmp_path):
    """Both Claude Code and Codex sessions are discovered and sorted by mtime."""
    # Claude Code session
    claude_file = tmp_path / ".claude" / "projects" / "my-proj" / "abc123.jsonl"
    _write_jsonl(claude_file, {"type": "user", "message": {"content": "Hello world"}})

    # Small sleep to ensure distinct mtimes
    time.sleep(0.05)

    # Codex session (newer)
    codex_file = (
        tmp_path / ".codex" / "sessions" / "2026" / "03" / "24"
        / "rollout-2026-03-24T10-00-00-xyz.jsonl"
    )
    _write_jsonl(codex_file, {"type": "user", "content": "Test codex session"})

    results = discover_sessions(home=tmp_path)

    assert len(results) == 2

    # Most recent first → Codex should be first
    assert results[0].agent == "Codex"
    assert results[1].agent == "Claude Code"


def test_discover_agent_field(tmp_path):
    """Agent field is correctly set for each source."""
    claude_file = tmp_path / ".claude" / "projects" / "proj-a" / "session1.jsonl"
    _write_jsonl(claude_file, {"type": "user", "message": {"content": "Claude session"}})

    cursor_file = (
        tmp_path / ".cursor" / "projects" / "proj-b"
        / "agent-transcripts" / "sess-xyz" / "transcript.jsonl"
    )
    _write_jsonl(cursor_file, {"type": "user", "content": "Cursor session"})

    results = discover_sessions(home=tmp_path)
    agents = {r.agent for r in results}
    assert "Claude Code" in agents
    assert "Cursor" in agents


def test_discover_limit(tmp_path):
    """Limit caps the number of results returned."""
    for i in range(5):
        f = tmp_path / ".claude" / "projects" / f"proj-{i}" / f"session-{i}.jsonl"
        _write_jsonl(f, {"type": "user", "message": {"content": f"Session {i}"}})

    results = discover_sessions(limit=3, home=tmp_path)
    assert len(results) == 3


def test_discover_summary_extraction(tmp_path):
    """Summary is extracted from the first user message content."""
    f = tmp_path / ".claude" / "projects" / "my-proj" / "sess.jsonl"
    _write_jsonl(f, {"type": "user", "message": {"content": "Fix the authentication bug"}})

    results = discover_sessions(home=tmp_path)
    assert len(results) == 1
    assert "Fix the authentication bug" in results[0].summary


def test_discover_codex_summary_extraction(tmp_path):
    """Summary is extracted from Codex event_msg / user_message lines."""
    f = (
        tmp_path / ".codex" / "sessions" / "2026" / "03" / "25"
        / "rollout-2026-03-25T12-00-00-abc.jsonl"
    )
    _write_jsonl_lines(f, [
        {"type": "session_meta", "session_id": "abc"},
        {"type": "event_msg", "payload": {"type": "task_started"}, "timestamp": "2026-03-25T12:00:00Z"},
        {
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": "## My request for Codex:\nFix the authentication bug",
            },
            "timestamp": "2026-03-25T12:00:01Z",
        },
    ])

    results = discover_sessions(home=tmp_path)
    assert len(results) == 1
    assert "Fix the authentication bug" in results[0].summary
    assert "My request for Codex" not in results[0].summary


def test_discover_no_sessions(tmp_path):
    """Returns empty list when no agent directories exist."""
    results = discover_sessions(home=tmp_path)
    assert results == []


def test_discover_skips_agent_files(tmp_path):
    """Files named agent-*.jsonl in Claude Code projects are skipped."""
    agent_file = tmp_path / ".claude" / "projects" / "proj" / "agent-abc.jsonl"
    _write_jsonl(agent_file, {"type": "user", "message": {"content": "Should be skipped"}})

    real_file = tmp_path / ".claude" / "projects" / "proj" / "real-session.jsonl"
    _write_jsonl(real_file, {"type": "user", "message": {"content": "Real session"}})

    results = discover_sessions(home=tmp_path)
    assert len(results) == 1
    assert results[0].path.name == "real-session.jsonl"
