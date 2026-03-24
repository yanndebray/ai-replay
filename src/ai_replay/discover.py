"""
Discover recent sessions across all supported agents (Claude Code, Codex, Cursor).
"""

from __future__ import annotations

import json
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


def _read_summary(path: Path, max_bytes: int = 4096) -> str:
    """Read the first user message content from a JSONL file as a summary."""
    try:
        raw = path.read_bytes()[:max_bytes].decode("utf-8", errors="ignore")
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
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


def discover_sessions(limit: int = 20, home: Path | None = None) -> list[SessionInfo]:
    """Discover recent sessions across Claude Code, Codex, and Cursor."""
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

    # Sort by most recent first, cap at limit
    results.sort(key=lambda s: s.mtime, reverse=True)
    results = results[:limit]

    # Populate summaries (deferred so we only read the files we'll show)
    for s in results:
        s.summary = _read_summary(s.path)

    return results
