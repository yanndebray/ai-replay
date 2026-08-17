"""
Discover recent sessions across all supported agents (Claude Code, Codex, Cursor, Pi).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SessionInfo:
    path: Path
    agent: str
    project: str
    mtime: float
    size_bytes: int
    summary: str = ""


def _read_summary(path: Path, max_lines: int = 30) -> str:
    """Read the first user message content from a JSONL file as a summary."""
    try:
        with path.open(encoding="utf-8", errors="ignore") as fh:
            lines = [fh.readline() for _ in range(max_lines)]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Codex format: {"type": "event_msg", "payload": {"type": "user_message", "message": "..."}}
            if obj.get("type") == "event_msg":
                payload = obj.get("payload") or {}
                if payload.get("type") == "user_message":
                    msg = payload.get("message", "").strip()
                    # Strip "## My request for Codex:" prefix if present
                    for marker in ("## My request for Codex:", "My request for Codex:"):
                        idx = msg.find(marker)
                        if idx != -1:
                            msg = msg[idx + len(marker):].strip()
                            break
                    if msg:
                        return msg[:60]
                continue

            # GitHub Copilot CLI: {"type": "user.message", "data": {"content": "..."}}
            if obj.get("type") == "user.message":
                data = obj.get("data") or {}
                text = (data.get("content") or "").strip()
                if text:
                    return text[:60]
                continue

            # Claude Code / generic format
            msg = obj.get("message", {})
            content = msg.get("content") if isinstance(msg, dict) else None
            if not content:
                content = obj.get("content") or obj.get("text") or obj.get("summary")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text:
                            return text[:60]
            if isinstance(content, str) and content.strip():
                return content.strip()[:60]
    except Exception:
        pass
    return "(no summary)"


def _project_display(dir_name: str) -> str:
    parts = dir_name.lstrip("-").split("-")
    return "-".join(parts[-2:]) if len(parts) > 1 else parts[0]


def _copilot_project(path: Path, max_lines: int = 5) -> str:
    """Derive a project name for a Copilot session from its ``session.start`` event.

    Copilot names its session directories by UUID, so the working directory has
    to come from the transcript itself rather than the path.
    """
    try:
        with path.open(encoding="utf-8", errors="ignore") as fh:
            for _ in range(max_lines):
                line = fh.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "session.start":
                    continue
                context = (obj.get("data") or {}).get("context") or {}
                cwd = context.get("cwd")
                if cwd:
                    return Path(cwd).name or cwd
                break
    except Exception:
        pass
    return "(unknown)"


def discover_sessions(limit: int = 20, home: Path | None = None) -> list[SessionInfo]:
    """Discover recent sessions across Claude Code, Cursor, Codex, Pi, and Copilot."""
    home_dir = home or Path.home()
    results: list[SessionInfo] = []

    # ------------------------------------------------------------------
    # Claude Code: ~/.claude/projects/<project>/<id>.jsonl
    # ------------------------------------------------------------------
    claude_base = home_dir / ".claude" / "projects"
    if claude_base.is_dir():
        for proj_path in claude_base.iterdir():
            if not proj_path.is_dir():
                continue
            for f in proj_path.glob("*.jsonl"):
                if f.name.startswith("agent-"):
                    continue
                stat = f.stat()
                results.append(SessionInfo(
                    path=f,
                    agent="Claude Code",
                    project=_project_display(proj_path.name),
                    mtime=stat.st_mtime,
                    size_bytes=stat.st_size,
                ))

    # ------------------------------------------------------------------
    # Cursor: ~/.cursor/projects/<project>/agent-transcripts/<id>/transcript.jsonl
    # ------------------------------------------------------------------
    cursor_base = home_dir / ".cursor" / "projects"
    if cursor_base.is_dir():
        for proj_path in cursor_base.iterdir():
            if not proj_path.is_dir():
                continue
            transcripts_dir = proj_path / "agent-transcripts"
            if not transcripts_dir.is_dir():
                continue
            for session_dir in transcripts_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                for candidate in ["transcript.jsonl", session_dir.name + ".jsonl"]:
                    f = session_dir / candidate
                    if f.exists():
                        stat = f.stat()
                        results.append(SessionInfo(
                            path=f,
                            agent="Cursor",
                            project=_project_display(proj_path.name),
                            mtime=stat.st_mtime,
                            size_bytes=stat.st_size,
                        ))
                        break

    # ------------------------------------------------------------------
    # Codex CLI: ~/.codex/sessions/<YYYY>/<MM>/<DD>/<rollout>.jsonl
    # ------------------------------------------------------------------
    codex_base = home_dir / ".codex" / "sessions"
    if codex_base.is_dir():
        for year_path in codex_base.iterdir():
            if not year_path.is_dir():
                continue
            for month_path in year_path.iterdir():
                if not month_path.is_dir():
                    continue
                for day_path in month_path.iterdir():
                    if not day_path.is_dir():
                        continue
                    for f in day_path.glob("*.jsonl"):
                        stat = f.stat()
                        results.append(SessionInfo(
                            path=f,
                            agent="Codex",
                            project=f"{year_path.name}-{month_path.name}-{day_path.name}",
                            mtime=stat.st_mtime,
                            size_bytes=stat.st_size,
                        ))

    # ------------------------------------------------------------------
    # Pi: ~/.pi/agent/sessions/--<cwd>--/<timestamp>_<uuid>.jsonl
    # (overridable via the PI_CODING_AGENT_DIR environment variable)
    # ------------------------------------------------------------------
    pi_dir = os.environ.get("PI_CODING_AGENT_DIR")
    pi_base = (Path(pi_dir) if pi_dir else home_dir / ".pi" / "agent") / "sessions"
    if pi_base.is_dir():
        for proj_path in pi_base.iterdir():
            if not proj_path.is_dir():
                continue
            for f in proj_path.glob("*.jsonl"):
                stat = f.stat()
                results.append(SessionInfo(
                    path=f,
                    agent="Pi",
                    project=_project_display(proj_path.name.strip("-")),
                    mtime=stat.st_mtime,
                    size_bytes=stat.st_size,
                ))

    # ------------------------------------------------------------------
    # GitHub Copilot CLI: ~/.copilot/session-state/<uuid>/events.jsonl
    # (overridable via the COPILOT_CLI_DIR environment variable)
    # ------------------------------------------------------------------
    copilot_dir = os.environ.get("COPILOT_CLI_DIR")
    copilot_base = (
        Path(copilot_dir) if copilot_dir else home_dir / ".copilot"
    ) / "session-state"
    if copilot_base.is_dir():
        for session_path in copilot_base.iterdir():
            if not session_path.is_dir():
                continue
            events = session_path / "events.jsonl"
            if not events.is_file():
                continue
            stat = events.stat()
            if stat.st_size == 0:
                continue
            results.append(SessionInfo(
                path=events,
                agent="Copilot",
                project=_copilot_project(events),
                mtime=stat.st_mtime,
                size_bytes=stat.st_size,
            ))

    # Sort by most recent first, cap at limit
    results.sort(key=lambda s: s.mtime, reverse=True)
    results = results[:limit]

    # Populate summaries (deferred so we only read the files we'll show)
    for s in results:
        s.summary = _read_summary(s.path)

    return results
