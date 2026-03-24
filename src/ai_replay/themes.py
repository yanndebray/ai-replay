"""
Built-in color themes and CSS generation for the HTML replay player.

Ported from themes.mjs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Ordered list of CSS variable names that are serialized into the :root block.
THEME_VARS = [
    "bg", "bg-surface", "bg-hover",
    "text", "text-dim", "text-bright",
    "accent", "accent-dim",
    "green", "blue", "orange", "red", "cyan",
    "border", "tool-bg", "thinking-bg",
]

THEMES: dict[str, dict[str, str]] = {
    "tokyo-night": {
        "bg": "#1a1b26",
        "bg-surface": "#24253a",
        "bg-hover": "#2f3147",
        "text": "#c0caf5",
        "text-dim": "#565f89",
        "text-bright": "#e0e6ff",
        "accent": "#bb9af7",
        "accent-dim": "#7957a8",
        "green": "#9ece6a",
        "blue": "#7aa2f7",
        "orange": "#ff9e64",
        "red": "#f7768e",
        "cyan": "#7dcfff",
        "border": "#3b3d57",
        "tool-bg": "#1e1f33",
        "thinking-bg": "#1c1d2e",
    },
    "monokai": {
        "bg": "#272822",
        "bg-surface": "#2d2e27",
        "bg-hover": "#3e3d32",
        "text": "#f8f8f2",
        "text-dim": "#75715e",
        "text-bright": "#ffffff",
        "accent": "#ae81ff",
        "accent-dim": "#7c5cbf",
        "green": "#a6e22e",
        "blue": "#66d9ef",
        "orange": "#fd971f",
        "red": "#f92672",
        "cyan": "#66d9ef",
        "border": "#49483e",
        "tool-bg": "#1e1f1c",
        "thinking-bg": "#1c1d1a",
    },
    "solarized-dark": {
        "bg": "#002b36",
        "bg-surface": "#073642",
        "bg-hover": "#0a4050",
        "text": "#839496",
        "text-dim": "#586e75",
        "text-bright": "#fdf6e3",
        "accent": "#6c71c4",
        "accent-dim": "#4e5299",
        "green": "#859900",
        "blue": "#268bd2",
        "orange": "#cb4b16",
        "red": "#dc322f",
        "cyan": "#2aa198",
        "border": "#094959",
        "tool-bg": "#012934",
        "thinking-bg": "#012730",
    },
    "github-light": {
        "bg": "#ffffff",
        "bg-surface": "#f6f8fa",
        "bg-hover": "#eaeef2",
        "text": "#1f2328",
        "text-dim": "#656d76",
        "text-bright": "#000000",
        "accent": "#8250df",
        "accent-dim": "#6639ba",
        "green": "#1a7f37",
        "blue": "#0969da",
        "orange": "#bc4c00",
        "red": "#cf222e",
        "cyan": "#0598bc",
        "border": "#d0d7de",
        "tool-bg": "#f6f8fa",
        "thinking-bg": "#f0f3f6",
    },
    "dracula": {
        "bg": "#282a36",
        "bg-surface": "#2d2f3d",
        "bg-hover": "#383a4a",
        "text": "#f8f8f2",
        "text-dim": "#6272a4",
        "text-bright": "#ffffff",
        "accent": "#bd93f9",
        "accent-dim": "#9571d1",
        "green": "#50fa7b",
        "blue": "#8be9fd",
        "orange": "#ffb86c",
        "red": "#ff5555",
        "cyan": "#8be9fd",
        "border": "#44475a",
        "tool-bg": "#21222c",
        "thinking-bg": "#1e1f29",
    },
    "bubbles": {
        "bg": "#f0f2f5",
        "bg-surface": "#ffffff",
        "bg-hover": "#e4e6eb",
        "text": "#1c1e21",
        "text-dim": "#65676b",
        "text-bright": "#000000",
        "accent": "#0084ff",
        "accent-dim": "#0066cc",
        "green": "#31a24c",
        "blue": "#0084ff",
        "orange": "#f5a623",
        "red": "#e4405f",
        "cyan": "#0097a7",
        "border": "#dddfe2",
        "tool-bg": "#e4e6eb",
        "thinking-bg": "#e8daef",
        "extraCss": (
            "\n"
            "      .turn { margin-bottom: 16px; }\n"
            "      .user-msg {\n"
            "        display: flex; align-items: flex-end; justify-content: flex-end; gap: 8px; margin-bottom: 12px;\n"
            "      }\n"
            "      .user-msg::after {\n"
            '        content: "\\1F464"; font-size: 24px; flex-shrink: 0; line-height: 1;\n'
            "      }\n"
            "      .user-prompt { display: none; }\n"
            "      .user-text {\n"
            "        background: #0084ff; color: #fff; border-radius: 18px 18px 4px 18px;\n"
            "        padding: 10px 16px; max-width: 75%; display: inline-block; font-weight: normal;\n"
            "      }\n"
            "      .turn-header-ts { color: #fff8; }\n"
            "      .turn > :not(.user-msg):not(.block-wrapper) { padding-left: 40px; }\n"
            "      .block-wrapper { padding-left: 40px; position: relative; }\n"
            "      .block-wrapper::before {\n"
            '        content: "\\1F916"; position: absolute; left: 4px; top: 4px; font-size: 20px; line-height: 1;\n'
            "      }\n"
            "      .block-wrapper + .block-wrapper::before { content: none; }\n"
            "      .assistant-text {\n"
            "        background: #fff; border-radius: 18px 18px 18px 4px;\n"
            "        padding: 10px 16px; max-width: 85%; display: inline-block; color: #1c1e21;\n"
            "        border: 1px solid #dddfe2;\n"
            "      }\n"
            "      .thinking-block {\n"
            "        background: #f3ebfa; border-radius: 18px 18px 18px 4px;\n"
            "        padding: 10px 16px; max-width: 85%; border: 1px solid #d6c8e4;\n"
            "      }\n"
            "      .thinking-header { color: #6b3fa0; }\n"
            "      .thinking-body { color: #3d2066; }\n"
            "      .tool-block, .tool-group {\n"
            "        background: #fff; border-radius: 12px;\n"
            "        padding: 8px 12px; max-width: 85%; border: 1px solid #dddfe2;\n"
            "      }\n"
            "      .tool-header { color: #1c1e21; }\n"
            "      .tool-name { color: #0066cc; }\n"
            "      .bookmark-divider { color: #1c1e21; border-color: #dddfe2; }\n"
            "    "
        ),
    },
}


def get_theme(name: str) -> dict[str, Any]:
    """Return a theme dict for the given built-in theme name.

    Raises ValueError if name is not a known built-in theme.
    """
    if name not in THEMES:
        available = ", ".join(sorted(THEMES.keys()))
        raise ValueError(f"Unknown theme {name!r}. Available: {available}")
    return THEMES[name]


def load_theme_file(path: Path | str) -> dict[str, Any]:
    """Load a custom theme from a JSON file.

    Missing keys are filled from tokyo-night defaults.
    """
    with open(path, "r", encoding="utf-8") as f:
        custom = json.load(f)
    if not isinstance(custom, dict):
        raise ValueError("Theme file must be a JSON object")
    return {**THEMES["tokyo-night"], **custom}


def theme_to_css(theme: dict[str, Any]) -> str:
    """Convert a theme dict to a CSS :root block."""
    lines = []
    for var in THEME_VARS:
        if var in theme:
            lines.append(f"  --{var}: {theme[var]};")
    css = ":root {\n" + "\n".join(lines) + "\n}"
    if "extraCss" in theme:
        css += "\n" + theme["extraCss"]
    return css


def get_theme_css(theme_name: str) -> str:
    """Generate the CSS :root block for the given theme name."""
    return theme_to_css(get_theme(theme_name))


def list_themes() -> list[str]:
    """Return available theme names sorted alphabetically."""
    return sorted(THEMES.keys())
