# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup
uv venv && source .venv/bin/activate
uv pip install -e .

# Run tests
uv run pytest
pytest tests/test_smoke.py  # single test file

# Build package
uv build
```

## Architecture

`ai-replay` is a Python CLI that converts AI agent session transcripts (JSONL files) into interactive HTML replays. It supports Claude Code, Cursor, and Codex CLI session formats.

**Data flow:**
1. `discover.py` — scans `~/.claude/projects/`, `~/.cursor/projects/`, `~/.codex/sessions/` → `SessionInfo` list
2. `parser.py` — reads JSONL → structured turn dicts (with `blocks`, `tool_use`, timestamps); handles 3 format variants via `detect_format()`
3. `secrets.py` — optional regex-based secret redaction applied to turn data
4. `renderer.py` — embeds turns (zlib-compressed + base64) into `templates/player.html` → self-contained HTML
5. `extract.py` — reverse: parses the embedded blob from HTML back to JSONL

**CLI (`__init__.py`)** uses `click-default-group` with `pick` as the default command:
- `pick` — interactive TUI session selector (via `questionary`)
- `generate INPUT [...]` — render sessions to HTML
- `extract HTML_FILE` — extract turns from a previously generated HTML file

**`templates/player.html`** (88 KB) is the entire frontend — a self-contained HTML+JS+CSS player. Modifying the replay UI means editing this file.

**`resolve_session.py`** resolves session IDs (partial path fragments) to full file paths, disambiguating across agents.

## Key conventions

- `Turn` structure: `{index, user_text, blocks: [{type, content}], timestamp, system_events, bookmark}`
- Compressed HTML embeds look like `__TURNS_DATA__ = "..."` (base64 zlib) or `__TURNS_RAW__ = [...]` (plain JSON)
- Theme is injected as CSS variables into the HTML template via `themes.py`
- The package is `ai_replay` but the CLI command is `ai-replay`
