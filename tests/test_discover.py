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


def test_discover_pi(tmp_path):
    """Pi sessions under ~/.pi/agent/sessions are discovered with agent 'Pi'."""
    pi_file = (
        tmp_path / ".pi" / "agent" / "sessions" / "--Users-me-Devel-my-project--"
        / "2026-06-29T07-06-41-426Z_019f1233.jsonl"
    )
    _write_jsonl_lines(pi_file, [
        {"type": "session", "version": 3, "id": "019f1233", "cwd": "/Users/me/Devel/my-project"},
        {"type": "message", "id": "m1", "message": {
            "role": "user", "content": [{"type": "text", "text": "Fix the parser bug"}]}},
    ])

    results = discover_sessions(home=tmp_path)
    assert len(results) == 1
    assert results[0].agent == "Pi"
    assert results[0].project == "my-project"
    assert "Fix the parser bug" in results[0].summary


def test_discover_copilot(tmp_path):
    """Copilot sessions under ~/.copilot/session-state get agent 'Copilot'."""
    events = (
        tmp_path / ".copilot" / "session-state"
        / "411f3f8b-0799-4e3d-9162-0100b4b182f1" / "events.jsonl"
    )
    _write_jsonl_lines(events, [
        {"type": "session.start", "id": "e1", "data": {
            "sessionId": "411f3f8b", "producer": "copilot-agent",
            "context": {"cwd": "/Users/me/Devel/my-project", "branch": "main"}}},
        {"type": "user.message", "id": "e2",
         "data": {"content": "Fix the parser bug"}},
    ])

    results = discover_sessions(home=tmp_path)
    assert len(results) == 1
    assert results[0].agent == "Copilot"
    # The session directory is a UUID, so the project comes from session.start.
    assert results[0].project == "my-project"
    assert "Fix the parser bug" in results[0].summary


def test_discover_copilot_skips_empty_sessions(tmp_path):
    """A session directory with an empty events.jsonl is not listed."""
    events = (
        tmp_path / ".copilot" / "session-state" / "empty-session" / "events.jsonl"
    )
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text("", encoding="utf-8")

    assert discover_sessions(home=tmp_path) == []


def _write_vscode_chat(tmp_path, workspace_hash, session_id, folder, lines):
    """Create a VS Code workspaceStorage chat session under *tmp_path*."""
    ws = tmp_path / "vscode" / "workspaceStorage" / workspace_hash
    (ws / "chatSessions").mkdir(parents=True, exist_ok=True)
    if folder is not None:
        (ws / "workspace.json").write_text(
            json.dumps({"folder": folder}), encoding="utf-8"
        )
    path = ws / "chatSessions" / f"{session_id}.jsonl"
    _write_jsonl_lines(path, lines)
    return path


def test_discover_vscode_copilot_chat(tmp_path, monkeypatch):
    """VS Code chat journals are discovered with agent 'Copilot Chat'."""
    monkeypatch.setenv("VSCODE_USER_DIR", str(tmp_path / "vscode"))
    _write_vscode_chat(
        tmp_path, "abc123hash", "269919a7-c82d-45ba-b595-5d26d3cd995b",
        "file:///Users/me/Devel/my-project",
        [
            {"kind": 0, "v": {"sessionId": "269919a7", "requests": []}},
            {"kind": 2, "k": ["requests"], "v": [
                {"requestId": "r1", "message": {"text": "hellp"}, "response": []}]},
        ],
    )

    results = discover_sessions(home=tmp_path)
    assert len(results) == 1
    assert results[0].agent == "Copilot Chat"
    # workspaceStorage dirs are opaque hashes; the name comes from workspace.json.
    assert results[0].project == "my-project"
    assert results[0].summary == "hellp"


def test_discover_vscode_chat_prompt_in_snapshot(tmp_path, monkeypatch):
    """A prompt living in the opening kind-0 snapshot is found too."""
    monkeypatch.setenv("VSCODE_USER_DIR", str(tmp_path / "vscode"))
    _write_vscode_chat(
        tmp_path, "hash2", "sess-2", "file:///Users/me/Devel/other",
        [{"kind": 0, "v": {"sessionId": "s2", "requests": [
            {"requestId": "r1", "message": {"text": "in the snapshot"}}]}}],
    )

    results = discover_sessions(home=tmp_path)
    assert len(results) == 1
    assert results[0].summary == "in the snapshot"


def test_discover_vscode_chat_skips_empty_sessions(tmp_path, monkeypatch):
    """VS Code writes a journal as soon as a panel opens; those are skipped."""
    monkeypatch.setenv("VSCODE_USER_DIR", str(tmp_path / "vscode"))
    _write_vscode_chat(
        tmp_path, "hash3", "empty-session", "file:///Users/me/Devel/proj",
        [{"kind": 0, "v": {"sessionId": "s3", "requests": []}}],
    )

    assert discover_sessions(home=tmp_path) == []


def test_discover_vscode_chat_without_workspace_json(tmp_path, monkeypatch):
    """A workspace with no workspace.json still lists, with an unknown project."""
    monkeypatch.setenv("VSCODE_USER_DIR", str(tmp_path / "vscode"))
    _write_vscode_chat(
        tmp_path, "hash4", "sess-4", None,
        [{"kind": 0, "v": {"sessionId": "s4", "requests": [
            {"requestId": "r1", "message": {"text": "no metadata"}}]}}],
    )

    results = discover_sessions(home=tmp_path)
    assert len(results) == 1
    assert results[0].project == "(unknown)"


def test_discover_does_not_escape_overridden_home(tmp_path, monkeypatch):
    """With no explicit override, the scan stays inside the given home directory.

    Guards against XDG_CONFIG_HOME / APPDATA pointing the scan at the
    developer's real VS Code storage during a test run.
    """
    # A populated VS Code layout sitting where XDG_CONFIG_HOME / APPDATA point.
    chat_dir = tmp_path / "config" / "Code" / "User" / "workspaceStorage" / "h6" / "chatSessions"
    chat_dir.mkdir(parents=True)
    _write_jsonl_lines(
        chat_dir / "leaked.jsonl",
        [{"kind": 0, "v": {"sessionId": "s6", "requests": [
            {"requestId": "r1", "message": {"text": "should not appear"}}]}}],
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "config"))
    monkeypatch.delenv("VSCODE_USER_DIR", raising=False)

    home = tmp_path / "elsewhere"
    home.mkdir()
    assert discover_sessions(home=home) == []


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
