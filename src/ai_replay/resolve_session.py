"""
Resolve a session ID to a full file path by scanning known session directories.

Ports src/resolve-session.mjs from the Node.js implementation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SessionMatch:
    path: Path
    project: str
    group: str


def resolve_session_id(
    session_id: str,
    home: Path | None = None,
) -> list[SessionMatch]:
    """Find session files matching the given ID.

    Parameters
    ----------
    session_id:
        Session ID (with or without .jsonl extension).
    home:
        Override the home directory (useful for testing).

    Returns
    -------
    list[SessionMatch]
        All files that match the session ID across all known tool locations.
    """
    home_dir = home or Path.home()
    target = session_id if session_id.endswith(".jsonl") else session_id + ".jsonl"
    matches: list[SessionMatch] = []

    # ------------------------------------------------------------------
    # Claude Code: ~/.claude/projects/<project>/<id>.jsonl
    # ------------------------------------------------------------------
    claude_base = home_dir / ".claude" / "projects"
    if claude_base.is_dir():
        for proj_path in claude_base.iterdir():
            if not proj_path.is_dir():
                continue
            file_path = proj_path / target
            if file_path.exists():
                parts = proj_path.name.lstrip("-").split("-")
                display_name = "-".join(parts[-2:]) if len(parts) > 1 else parts[0]
                matches.append(
                    SessionMatch(path=file_path, project=display_name, group="Claude Code")
                )

    # ------------------------------------------------------------------
    # Cursor: ~/.cursor/projects/<project>/agent-transcripts/<id>/transcript.jsonl
    #     or: ~/.cursor/projects/<project>/agent-transcripts/<id>/<id>.jsonl
    # ------------------------------------------------------------------
    cursor_base = home_dir / ".cursor" / "projects"
    if cursor_base.is_dir():
        for proj_path in cursor_base.iterdir():
            if not proj_path.is_dir():
                continue
            transcripts_dir = proj_path / "agent-transcripts"
            # Try transcript.jsonl first, then <id>.jsonl
            file_path = transcripts_dir / session_id / "transcript.jsonl"
            if not file_path.exists():
                file_path = transcripts_dir / session_id / (session_id + ".jsonl")
                if not file_path.exists():
                    continue
            parts = proj_path.name.lstrip("-").split("-")
            display_name = "-".join(parts[-2:]) if len(parts) > 1 else parts[0]
            matches.append(
                SessionMatch(path=file_path, project=display_name, group="Cursor")
            )

    # ------------------------------------------------------------------
    # Codex CLI: ~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-<timestamp>-<uuid>.jsonl
    # Match by exact filename, or by UUID portion after the timestamp prefix.
    # ------------------------------------------------------------------
    _ROLLOUT_RE = re.compile(
        r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(.+)$"
    )
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
                    for f in day_path.iterdir():
                        if f.suffix != ".jsonl":
                            continue
                        if f.name == target:
                            matches.append(
                                SessionMatch(
                                    path=f,
                                    project=f"{year_path.name}-{month_path.name}-{day_path.name}",
                                    group="Codex CLI",
                                )
                            )
                            continue
                        # Check UUID portion of rollout filenames
                        stem = f.stem
                        m = _ROLLOUT_RE.match(stem)
                        if m and session_id in m.group(1):
                            matches.append(
                                SessionMatch(
                                    path=f,
                                    project=f"{year_path.name}-{month_path.name}-{day_path.name}",
                                    group="Codex CLI",
                                )
                            )

    return matches


def resolve_session_path(session_id: str, home: Path | None = None) -> Path:
    """Resolve a session ID to a single unambiguous file path.

    Parameters
    ----------
    session_id:
        A .jsonl file path, or a session UUID / partial ID to search for.
    home:
        Override the home directory (useful for testing).

    Returns
    -------
    Path
        The resolved file path.

    Raises
    ------
    FileNotFoundError
        If the session ID cannot be found in any known location.
    ValueError
        If the session ID matches more than one session.
    """
    candidate = Path(session_id)
    if candidate.exists():
        return candidate.resolve()

    matches = resolve_session_id(session_id, home=home)

    if len(matches) == 0:
        raise FileNotFoundError(
            f"No session found matching {session_id!r}. "
            "Searched ~/.claude/projects/, ~/.cursor/projects/, and ~/.codex/sessions/"
        )

    if len(matches) > 1:
        lines = [f"Multiple sessions match {session_id!r}:"]
        for i, m in enumerate(matches, 1):
            lines.append(f"  {i}) {m.group} / {m.project} — {m.path}")
        raise ValueError("\n".join(lines))

    match = matches[0]
    import sys
    print(f"Found: {match.group} / {match.project} → {match.path}", file=sys.stderr)
    return match.path
