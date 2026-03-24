"""Smoke tests — verify imports, basic parse, and round-trip render/extract."""
import json
import textwrap

import pytest

from ai_replay.parser import detect_format, parse_session
from ai_replay.renderer import render_html
from ai_replay.extract import extract_turns
from ai_replay.secrets import redact_secrets
from ai_replay.themes import list_themes, get_theme


CLAUDE_FIXTURE = textwrap.dedent("""\
    {"type":"user","message":{"role":"user","content":"Hello, what is 2+2?"},"timestamp":"2025-06-01T10:00:00Z"}
    {"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"2 + 2 = 4"}]},"timestamp":"2025-06-01T10:00:01Z"}
""")


@pytest.fixture
def claude_session(tmp_path):
    f = tmp_path / "session.jsonl"
    f.write_text(CLAUDE_FIXTURE)
    return f


def test_detect_format(claude_session):
    assert detect_format(claude_session) == "claude"


def test_parse_claude(claude_session):
    turns = parse_session(claude_session)
    assert len(turns) == 1
    assert turns[0]["user_text"] == "Hello, what is 2+2?"
    assert turns[0]["blocks"][0]["kind"] == "text"
    assert "4" in turns[0]["blocks"][0]["text"]


def test_render_and_extract(claude_session):
    turns = parse_session(claude_session)
    html = render_html(turns)
    assert html.startswith("<!DOCTYPE html>")

    extracted, bookmarks = extract_turns(html)
    assert len(extracted) == len(turns)
    assert extracted[0]["user_text"] == turns[0]["user_text"]
    assert bookmarks == []


def test_themes():
    themes = list_themes()
    assert "tokyo-night" in themes
    assert "dracula" in themes
    theme = get_theme("tokyo-night")
    assert "bg" in theme


def test_redact_secrets():
    text = "key = sk-ant-api03-supersecretkey1234567890abcdef"
    redacted = redact_secrets(text)
    assert "sk-ant" not in redacted
    assert "[REDACTED]" in redacted
