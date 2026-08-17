"""
Discover recent sessions across all supported agents (Claude Code, Codex,
Cursor, Pi, GitHub Copilot CLI, VS Code Copilot Chat).
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote


@dataclass
class SessionInfo:
    path: Path
    agent: str
    project: str
    mtime: float
    size_bytes: int
    summary: str = ""


def _vscode_first_prompt(obj: dict) -> str:
    """Return the first user prompt from a VS Code chat journal line, if present.

    ``requests`` arrives either inside the opening ``kind 0`` snapshot or as a
    later ``kind 2`` append at the ``["requests"]`` key-path.
    """
    if "kind" not in obj:
        return ""
    requests: object = None
    if obj.get("kind") == 0 and isinstance(obj.get("v"), dict):
        requests = obj["v"].get("requests")
    elif obj.get("k") == ["requests"] and isinstance(obj.get("v"), list):
        requests = obj["v"]
    if not isinstance(requests, list):
        return ""
    for request in requests:
        if not isinstance(request, dict):
            continue
        message = request.get("message")
        if isinstance(message, dict):
            text = (message.get("text") or "").strip()
            if text:
                return text
    return ""


def _vscode_has_prompt(path: Path, max_lines: int = 40) -> bool:
    """Return True if a VS Code chat journal contains at least one user prompt.

    VS Code creates a journal file as soon as a chat panel is opened, so empty
    sessions are common and would otherwise crowd the picker.
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
                if _vscode_first_prompt(obj):
                    return True
    except Exception:
        pass
    return False


def _vscode_workspace_project(workspace_dir: Path) -> str:
    """Derive a project name for a VS Code workspace storage directory.

    The directory itself is an opaque hash; the real path lives in a sibling
    ``workspace.json`` as either a ``folder`` or ``workspace`` URI.
    """
    meta = workspace_dir / "workspace.json"
    try:
        data = json.loads(meta.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return "(unknown)"
    uri = data.get("folder") or data.get("workspace")
    if not isinstance(uri, str) or not uri:
        return "(unknown)"
    # "file:///Users/me/Devel/my-project" -> "my-project"
    path = uri.split("://", 1)[-1]
    name = Path(unquote(path)).name
    if name.endswith(".code-workspace"):
        name = name[: -len(".code-workspace")]
    return name or "(unknown)"


# VS Code and its close forks, which all share the workspaceStorage layout.
# Other Electron apps (e.g. "IBM Bob") use the same layout but are not VS Code,
# so the list is explicit rather than a wildcard.
_VSCODE_APP_DIRS = ("Code", "Code - Insiders", "VSCodium")


def _vscode_storage_dirs(home_dir: Path) -> list[Path]:
    """Return candidate ``workspaceStorage`` directories for this platform."""
    override = os.environ.get("VSCODE_USER_DIR")
    if override:
        return [Path(override) / "workspaceStorage"]

    # Environment-provided config roots are only trusted for the real home
    # directory; when *home_dir* is overridden (tests) stay inside it so the
    # scan cannot escape into the developer's actual VS Code storage.
    is_real_home = home_dir == Path.home()

    if sys.platform == "darwin":
        roots = [home_dir / "Library" / "Application Support"]
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA") if is_real_home else None
        roots = [Path(appdata)] if appdata else [home_dir / "AppData" / "Roaming"]
    else:
        config_home = os.environ.get("XDG_CONFIG_HOME") if is_real_home else None
        roots = [Path(config_home) if config_home else home_dir / ".config"]

    return [
        root / app / "User" / "workspaceStorage"
        for root in roots
        for app in _VSCODE_APP_DIRS
    ]


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

            # VS Code Copilot Chat journal: the requests live either in the
            # opening ``kind 0`` snapshot or in a later ``kind 2`` append.
            text = _vscode_first_prompt(obj)
            if text:
                return text[:60]

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
    """Discover recent sessions across Claude Code, Cursor, Codex, Pi, and Copilot.

    Covers both GitHub Copilot CLI sessions and VS Code Copilot Chat sessions.
    """
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

    # ------------------------------------------------------------------
    # VS Code Copilot Chat:
    #   <config>/<app>/User/workspaceStorage/<hash>/chatSessions/<id>.jsonl
    # (overridable via the VSCODE_USER_DIR environment variable)
    # ------------------------------------------------------------------
    for storage_base in _vscode_storage_dirs(home_dir):
        if not storage_base.is_dir():
            continue
        for workspace_path in storage_base.iterdir():
            chat_dir = workspace_path / "chatSessions"
            if not chat_dir.is_dir():
                continue
            project = _vscode_workspace_project(workspace_path)
            for f in chat_dir.glob("*.jsonl"):
                stat = f.stat()
                if stat.st_size == 0 or not _vscode_has_prompt(f):
                    continue
                results.append(SessionInfo(
                    path=f,
                    agent="Copilot Chat",
                    project=project,
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
