"""
ai-replay: Convert Claude Code, Cursor, and Codex CLI session transcripts
to interactive HTML replays.
"""

from __future__ import annotations

import http.server
import sys
import webbrowser
from pathlib import Path
from typing import Optional

import click
from click_default_group import DefaultGroup

from .parser import parse_session, detect_format
from .renderer import render_html
from .resolve_session import resolve_session_path

__version__ = "0.1.2"

VALID_THEMES = ["tokyo-night", "monokai", "solarized-dark", "github-light", "dracula", "bubbles"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_turn_range(value: str) -> tuple[int, int]:
    """Parse a turn range string like '1-10' into (start, end)."""
    parts = value.split("-")
    if len(parts) != 2:
        raise click.BadParameter(f"invalid turn range {value!r} (expected N-M)")
    try:
        start, end = int(parts[0]), int(parts[1])
    except ValueError:
        raise click.BadParameter(f"invalid turn range {value!r} (expected integers)")
    return start, end


def _parse_exclude_turns(value: str) -> list[int]:
    """Parse an exclusion list like '3,7,12' into [3, 7, 12]."""
    result = []
    for part in value.split(","):
        part = part.strip()
        try:
            result.append(int(part))
        except ValueError:
            raise click.BadParameter(f"invalid turn number {part!r} in --exclude")
    return result


def _resolve_inputs(inputs: tuple[str, ...]) -> list[Path]:
    """Resolve a list of file paths or session IDs to Path objects."""
    MAX_INPUTS = 20
    if len(inputs) > MAX_INPUTS:
        raise click.UsageError(f"Too many input files (max {MAX_INPUTS})")

    resolved: list[Path] = []
    for arg in inputs:
        p = Path(arg)
        if p.exists():
            resolved.append(p.resolve())
        elif not arg.endswith(".jsonl"):
            # Treat as session ID
            path = resolve_session_path(arg)
            resolved.append(path)
        else:
            raise click.BadParameter(f"File not found: {arg}", param_hint="INPUT")
    return resolved


def _derive_title(input_files: list[Path]) -> str:
    """Derive a page title from the first input file path."""
    first = input_files[0]
    dir_name = first.parent.name
    parts = dir_name.lstrip("-").split("-")
    project_name = "-".join(parts[-2:]) if len(parts) > 1 else parts[0]
    if project_name and project_name not in (".", "/", ""):
        return f"Replay — {project_name}"
    return f"Replay — {first.stem}"


def _build_replay(
    input_files: list[Path],
    *,
    title: Optional[str],
    theme_name: str,
    redact: bool,
    compress: bool,
    turns_filter: Optional[str],
    exclude_filter: Optional[str],
) -> tuple[str, int]:
    """Parse inputs and render to HTML. Returns (html, turn_count)."""
    from .themes import get_theme
    from .parser import filter_turns, apply_paced_timing

    theme = get_theme(theme_name)

    # Detect format from first file
    fmt = detect_format(input_files[0])

    all_turns = []
    for f in input_files:
        file_turns = parse_session(f)
        if len(input_files) > 1:
            f_fmt = detect_format(f)
            if f_fmt == "cursor":
                fmt = "cursor"
        all_turns.extend(file_turns)

    # When merging multiple sessions, sort by timestamp if available
    if len(input_files) > 1:
        all_have_timestamps = all_turns and all(t.get("timestamp") for t in all_turns)
        if all_have_timestamps:
            all_turns.sort(key=lambda t: t["timestamp"])
        for i, t in enumerate(all_turns, 1):
            t["index"] = i
        click.echo(
            f"Merged {len(input_files)} sessions ({len(all_turns)} turns total)",
            err=True,
        )

    # Parse turn filters
    turn_range = _parse_turn_range(turns_filter) if turns_filter else None
    exclude_turns = _parse_exclude_turns(exclude_filter) if exclude_filter else None

    turns = filter_turns(
        all_turns,
        turn_range=turn_range,
        exclude_turns=exclude_turns,
    )

    # Re-index after filtering
    index_map: dict[int, int] = {}
    for i, t in enumerate(turns, 1):
        index_map[t["index"]] = i
        t["index"] = i

    if not turns:
        click.echo("Warning: no turns found after filtering.", err=True)

    # Determine timing strategy
    has_timestamps = any(t.get("timestamp") for t in turns)
    if not has_timestamps:
        apply_paced_timing(turns)
    has_real_timestamps = has_timestamps

    # Collect bookmarks embedded in turns
    bookmarks: list[dict] = []
    for t in turns:
        if t.get("bookmark"):
            bookmarks.append({"turn": t["index"], "label": t.pop("bookmark")})
    bookmarks.sort(key=lambda b: b["turn"])

    assistant_label = (
        "Codex" if fmt == "codex"
        else "Assistant" if fmt == "cursor"
        else "Claude"
    )

    if not title:
        title = _derive_title(input_files)

    html = render_html(
        turns,
        title=title,
        theme=theme,
        assistant_label=assistant_label,
        bookmarks=bookmarks,
        has_real_timestamps=has_real_timestamps,
        compress=compress,
        redact=redact,
    )

    return html, len(turns)


# ---------------------------------------------------------------------------
# CLI definition
# ---------------------------------------------------------------------------

@click.group(cls=DefaultGroup, default="generate", default_if_no_args=False)
@click.version_option(__version__, "-v", "--version")
def main() -> None:
    """Convert Claude Code, Cursor, and Codex CLI session transcripts to
    interactive HTML replays.

    \b
    Examples:
      agent-replay session.jsonl -o replay.html
      agent-replay <session-id> -o replay.html
      agent-replay extract replay.html
    """


@main.command(name="generate")
@click.argument("input", nargs=-1, required=True)
@click.option(
    "-o", "--output",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Output HTML file. Defaults to <input_basename>.html, or stdout if multiple inputs.",
)
@click.option("--title", default=None, help="Custom page title.")
@click.option(
    "--theme",
    type=click.Choice(VALID_THEMES),
    default="tokyo-night",
    show_default=True,
    help="Color theme.",
)
@click.option(
    "--no-redact",
    is_flag=True,
    default=False,
    help="Disable automatic secret redaction.",
)
@click.option(
    "--open", "open_browser",
    is_flag=True,
    default=False,
    help="Open in browser after generating (requires --output or --serve).",
)
@click.option(
    "--no-compress",
    is_flag=True,
    default=False,
    help="Embed raw JSON instead of compressed data.",
)
@click.option(
    "--serve",
    is_flag=True,
    default=False,
    help="Start an HTTP server instead of writing a file.",
)
@click.option(
    "--port",
    type=int,
    default=4000,
    show_default=True,
    help="Port for --serve.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Host to bind for --serve.",
)
@click.option(
    "--turns",
    default=None,
    metavar="TEXT",
    help="Include only this turn range, e.g. '1-10'.",
)
@click.option(
    "--exclude",
    default=None,
    metavar="TEXT",
    help="Exclude specific turns, e.g. '3,7'.",
)
def generate(
    input: tuple[str, ...],
    output: Optional[str],
    title: Optional[str],
    theme: str,
    no_redact: bool,
    open_browser: bool,
    no_compress: bool,
    serve: bool,
    port: int,
    host: str,
    turns: Optional[str],
    exclude: Optional[str],
) -> None:
    """Generate an HTML replay from one or more JSONL session files or session IDs.

    INPUT can be a .jsonl file path or a session ID. If it does not end in
    .jsonl and is not an existing file, it is treated as a session ID and
    searched in ~/.claude/projects/, ~/.cursor/projects/, and ~/.codex/sessions/.

    Multiple inputs are concatenated into a single replay (up to 20). Sessions
    with timestamps are sorted chronologically; otherwise command-line order is
    used.
    """
    try:
        input_files = _resolve_inputs(input)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc))

    try:
        html, turn_count = _build_replay(
            input_files,
            title=title,
            theme_name=theme,
            redact=not no_redact,
            compress=not no_compress,
            turns_filter=turns,
            exclude_filter=exclude,
        )
    except Exception as exc:
        raise click.ClickException(str(exc))

    if serve:
        _serve_html(html, host=host, port=port, open_browser=open_browser)
        return

    if output:
        out_path = Path(output)
        out_path.write_text(html, encoding="utf-8")
        click.echo(f"Wrote {out_path} ({turn_count} turns)", err=True)
        if open_browser:
            webbrowser.open(out_path.resolve().as_uri())
    else:
        if open_browser:
            click.echo(
                "Warning: --open requires -o/--output (cannot open stdout output)",
                err=True,
            )
        sys.stdout.write(html)


def _serve_html(html: str, *, host: str = "127.0.0.1", port: int, open_browser: bool) -> None:
    """Serve an HTML string on a local HTTP server."""
    html_bytes = html.encode("utf-8")

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_bytes)))
            self.end_headers()
            self.wfile.write(html_bytes)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # Silence request logs

    url = f"http://{host}:{port}"
    click.echo(f"Serving replay at {url}", err=True)
    if open_browser:
        webbrowser.open(url)

    with http.server.HTTPServer((host, port), _Handler) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            click.echo("\nServer stopped.", err=True)


@main.command(name="extract")
@click.argument("html_file", type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option(
    "-o", "--output",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Output JSONL file. Defaults to stdout.",
)
@click.option(
    "--format", "fmt",
    type=click.Choice(["jsonl", "json"]),
    default="jsonl",
    show_default=True,
    help="Output format.",
)
def extract(html_file: str, output: Optional[str], fmt: str) -> None:
    """Extract embedded turn data from a generated HTML replay file.

    Outputs JSONL (or JSON with --format json) to stdout or a file.
    """
    from .extract import extract_turns

    html_path = Path(html_file)
    html = html_path.read_text(encoding="utf-8")

    try:
        turns_list, bookmarks_list = extract_turns(html)
        data = {"turns": turns_list, "bookmarks": bookmarks_list}
    except Exception as exc:
        raise click.ClickException(str(exc))

    if fmt == "json":
        import json
        out_text = json.dumps(data, indent=2)
    else:
        import json
        bm_map: dict[int, str] = {bm["turn"]: bm["label"] for bm in data.get("bookmarks", [])}
        lines = []
        for t in data.get("turns", []):
            label = bm_map.get(t.get("index"))
            if label:
                t = {**t, "bookmark": label}
            lines.append(json.dumps(t))
        out_text = "\n".join(lines)

    turn_count = len(data.get("turns", []))
    bm_count = len(data.get("bookmarks", []))

    if output:
        Path(output).write_text(out_text + "\n", encoding="utf-8")
        click.echo(f"Wrote {output} ({turn_count} turns, {bm_count} bookmarks)", err=True)
    else:
        sys.stdout.write(out_text + "\n")
