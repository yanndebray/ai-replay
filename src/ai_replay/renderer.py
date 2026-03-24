"""
Render parsed session turns into a self-contained HTML replay file.

Ported from renderer.mjs.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import zlib
from pathlib import Path
from typing import Any

from .secrets import build_redactor, redact_object, redact_secrets
from .themes import get_theme, theme_to_css

# Default template bundled with the package
_DEFAULT_TEMPLATE = Path(__file__).parent / "templates" / "player.html"


# ---------------------------------------------------------------------------
# HTML / script escaping helpers
# ---------------------------------------------------------------------------

def _escape_html(text: str) -> str:
    """Escape text for safe embedding in HTML text nodes and attribute values."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _escape_json_for_script(json_str: str) -> str:
    """Escape a JSON string for safe embedding inside a double-quoted JS string literal in a <script> tag."""
    return (
        json_str
        .replace("\\", "\\\\")      # backslashes first
        .replace('"', '\\"')         # double quotes (JS string delimiter)
        .replace("\n", "\\n")        # newlines
        .replace("\r", "\\r")        # carriage returns
        .replace("</", "<\\/")       # </script> breakout
        .replace("<!--", "<\\!--")   # HTML comment breakout
    )


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------

def _compress_for_embed(json_str: str) -> str:
    """Compress a JSON string to base64-encoded zlib deflate for embedding.

    Uses zlib format (wbits=15) to match Node.js ``deflateSync`` output,
    which the browser decompresses with ``DecompressionStream("deflate")``.
    """
    data = json_str.encode("utf-8")
    compressed = zlib.compress(data)
    return base64.b64encode(compressed).decode("ascii")


# ---------------------------------------------------------------------------
# Turn serialization helpers
# ---------------------------------------------------------------------------

def _to_dict(obj: Any) -> Any:
    """Recursively convert dataclasses to plain dicts."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_to_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    return obj


def _transform_strings(obj: Any, fn: Any) -> Any:
    """Recursively apply a text transform to all string values in an object/array."""
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_transform_strings(v, fn) for v in obj]
    if isinstance(obj, dict):
        return {k: _transform_strings(v, fn) for k, v in obj.items()}
    return obj


def _turns_to_json_data(
    turns: list[Any],
    *,
    redact: bool = True,
    redact_rules: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Prepare turns data for serialization, applying optional redaction."""
    custom_redact = build_redactor(redact_rules)

    def scrub_text(text: str) -> str:
        return custom_redact(redact_secrets(text) if redact else text)

    def scrub_obj(obj: Any) -> Any:
        scrubbed = redact_object(obj) if redact else obj
        return _transform_strings(scrubbed, custom_redact)

    result = []
    for turn in turns:
        # Support both dataclass instances and plain dicts
        if dataclasses.is_dataclass(turn) and not isinstance(turn, type):
            turn = dataclasses.asdict(turn)

        blocks = []
        for b in turn.get("blocks", []):
            block: dict[str, Any] = {
                "kind": b.get("kind"),
                "text": scrub_text(b.get("text") or ""),
            }
            if b.get("timestamp"):
                block["timestamp"] = b["timestamp"]
            if b.get("tool_call"):
                tc = b["tool_call"]
                tool_call: dict[str, Any] = {
                    "name": tc.get("name"),
                    "input": scrub_obj(tc.get("input")),
                    "result": scrub_text(tc.get("result") or ""),
                }
                if tc.get("is_error"):
                    tool_call["is_error"] = True
                if tc.get("resultTimestamp"):
                    tool_call["resultTimestamp"] = tc["resultTimestamp"]
                block["tool_call"] = tool_call
            blocks.append(block)

        entry: dict[str, Any] = {
            "index": turn.get("index"),
            "user_text": scrub_text(turn.get("user_text") or ""),
            "blocks": blocks,
            "timestamp": turn.get("timestamp"),
        }
        if turn.get("system_events"):
            entry["system_events"] = turn["system_events"]

        result.append(entry)

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_html(
    turns: list[Any],
    *,
    title: str = "Claude Code Replay",
    theme: dict[str, Any] | str | None = None,
    speed: float = 1.0,
    show_thinking: bool = True,
    show_tool_calls: bool = True,
    user_label: str = "User",
    assistant_label: str = "Claude",
    description: str = "Interactive AI session replay",
    og_image: str = "https://es617.github.io/claude-replay/og.png",
    bookmarks: list[dict[str, Any]] | None = None,
    has_real_timestamps: bool = False,
    compress: bool = True,
    redact: bool = True,
    redact_rules: list[dict[str, str]] | None = None,
    template_path: Path | str | None = None,
    options: dict[str, Any] | None = None,
) -> str:
    """Render a list of turns into a self-contained HTML string.

    Parameters
    ----------
    turns:
        Parsed turn dicts or dataclass instances.
    title:
        Page title shown in the browser tab and controls bar.
    theme:
        Theme dict with CSS variable definitions, or a built-in theme name
        string. Defaults to "tokyo-night".
    speed:
        Initial playback speed multiplier (clamped to 0.1–10).
    show_thinking:
        Whether thinking blocks are visible by default.
    show_tool_calls:
        Whether tool-call blocks are visible by default.
    user_label:
        Display label for the user role.
    assistant_label:
        Display label for the assistant role.
    description:
        Meta description for link previews.
    og_image:
        Open Graph image URL.
    bookmarks:
        List of ``{"turn": int, "label": str}`` bookmark dicts.
    has_real_timestamps:
        Whether the turns carry real wall-clock timestamps.
    compress:
        If True, compress embedded turn data with raw deflate + base64.
    redact:
        If True, apply secret redaction to turn text content.
    redact_rules:
        Optional list of ``{"search": str, "replacement": str}`` dicts for
        caller-specified plain-string redaction rules.
    template_path:
        Path to a custom player.html template. Defaults to the bundled one.
    options:
        Ignored; reserved for forward compatibility.

    Returns
    -------
    str
        Complete, self-contained HTML document.
    """
    # Resolve theme
    if theme is None:
        theme_dict = get_theme("tokyo-night")
    elif isinstance(theme, str):
        theme_dict = get_theme(theme)
    else:
        theme_dict = theme

    # Clamp speed
    try:
        speed_val = float(speed)
        if not (speed_val == speed_val):  # NaN check
            speed_val = 1.0
    except (TypeError, ValueError):
        speed_val = 1.0
    speed_val = max(0.1, min(speed_val, 10.0))
    speed_str = str(speed_val) if speed_val != int(speed_val) else str(int(speed_val))

    # Load template
    tpl_path = Path(template_path) if template_path else _DEFAULT_TEMPLATE
    html = tpl_path.read_text(encoding="utf-8")

    # --- Replace non-data placeholders first ---
    # (before injecting TURNS/BOOKMARKS JSON which may contain the same strings)

    html = html.replace("/*THEME_CSS*/", theme_to_css(theme_dict))
    html = html.replace("/*THEME_BG*/", _escape_html(theme_dict.get("bg", "#1a1b26")))

    # /*INITIAL_SPEED*/1  (JS default in code)  and  /*INITIAL_SPEED*/  (HTML attrs)
    html = html.replace("/*INITIAL_SPEED*/1", speed_str, 1)
    html = html.replace("/*INITIAL_SPEED*/", speed_str)

    html = html.replace("/*CHECKED_THINKING*/", "checked" if show_thinking else "")
    html = html.replace("/*CHECKED_TOOLS*/", "checked" if show_tool_calls else "")
    html = html.replace("/*PAGE_TITLE*/", _escape_html(title))
    html = html.replace("/*PAGE_DESCRIPTION*/", _escape_html(description))
    html = html.replace("/*OG_IMAGE*/", _escape_html(og_image))
    html = html.replace("/*USER_LABEL*/", _escape_html(user_label), 1)
    html = html.replace("/*ASSISTANT_LABEL*/", _escape_html(assistant_label), 1)
    html = html.replace(
        "/*HAS_REAL_TIMESTAMPS*/false",
        "true" if has_real_timestamps else "false",
        1,
    )

    # --- Embed data blobs last ---
    # BOOKMARKS before TURNS because TURNS data may contain placeholder strings.
    bookmarks_list = bookmarks or []

    def embed(json_str: str) -> str:
        if compress:
            return _compress_for_embed(json_str)
        return _escape_json_for_script(json_str)

    turns_data = _turns_to_json_data(turns, redact=redact, redact_rules=redact_rules)

    html = html.replace("/*BOOKMARKS_DATA*/", embed(json.dumps(bookmarks_list)), 1)
    html = html.replace("/*TURNS_DATA*/", embed(json.dumps(turns_data)), 1)

    return html
