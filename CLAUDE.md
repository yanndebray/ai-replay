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

`ai-replay` is a Python CLI that converts AI agent session transcripts (JSONL files) into interactive HTML replays. It supports Claude Code, Cursor, Codex CLI, OpenCode, Pi, GitHub Copilot CLI, and VS Code Copilot Chat session formats.

**Data flow:**
1. `discover.py` — scans `~/.claude/projects/`, `~/.cursor/projects/`, `~/.codex/sessions/`, `~/.pi/agent/sessions/`, `~/.copilot/session-state/`, VS Code's `workspaceStorage/*/chatSessions/` → `SessionInfo` list
2. `parser.py` — reads JSONL → structured turn dicts (with `blocks`, `tool_use`, timestamps); handles format variants via `detect_format()`
3. `secrets.py` — optional regex-based secret redaction applied to turn data
4. `renderer.py` — embeds turns (zlib-compressed + base64) into `templates/player.html` → self-contained HTML
5. `extract.py` — reverse: parses the embedded blob from HTML back to JSONL

**OpenCode:** stores sessions in a SQLite DB, not on-disk JSONL. ai-replay does *not* read the DB directly — instead the user exports a session with `opencode export <sessionID> > session.json` (a single JSON object `{info, messages:[{info:{role}, parts:[...]}]}`), which is auto-detected as the `"opencode"` format. OpenCode's lowercase tool names (`bash`, `read`, `write`, `edit`, …) are mapped to Claude-Code-style TitleCase names so the player renders the same previews/diffs.

**GitHub Copilot CLI:** writes an event log to `~/.copilot/session-state/<uuid>/events.jsonl` (overridable via `COPILOT_CLI_DIR`). Every line is a `{type, data, id, timestamp, parentId}` envelope with a *dotted* event name, which is what `detect_format()` keys on (`"copilot"`) — the dotted names never collide with Pi's bare `session`/`message` or Claude's bare `user`/`assistant`. A `user.message` starts a turn (use `data.content`, not `data.transformedContent`, which has system reminders injected); `assistant.message` carries `reasoningText` (thinking), `content` (text) and `toolRequests[]`; `tool.execution_complete` attaches output by `toolCallId`. Long responses are split over several `assistant.message` events via `chunkIndex`/`chunkCount`, but each carries complete text, so they just append. Failed calls have no `result` — only `error: {message, code}`. Copilot's lowercase built-ins (`bash`, `view`, `create`, `edit`, …) map to TitleCase; unmapped snake_case names become TitleCase (`ask_user` → `AskUser`). Session dirs are UUIDs, so the picker's project name comes from `session.start`'s `data.context.cwd`.

**VS Code Copilot Chat:** stored at `.../Code/User/workspaceStorage/<workspace>/chatSessions/<id>.jsonl`, detected as `"copilot-chat"`. This is the only supported format that is *not* an append-only transcript — it is a **journal**: `kind 0` is a full snapshot, `kind 1` sets the value at key-path `k`, `kind 2` appends to the list at `k`. A streamed answer arrives on a later line as a `["requests", N, "response"]` append, so reading line-by-line silently loses content; `_vscode_apply_journal()` must replay it to a final state first. Each `requests[]` entry is one turn: `message.text` is the prompt, `response[]` holds parts — a part with **no** `kind` is markdown text, plus `thinking`, `inlineReference` and `toolInvocationSerialized`. Consecutive text parts are merged (VS Code splits one answer across many). Empty `thinking` parts are completion markers (`vscode_reasoning_done`) and carry no text — skip them. Tool arguments come from `toolSpecificData` (`terminal` → `commandLine.original`, *not* `toolEdited`, which is env-prefixed; `input` → `rawInput`; `subagent`; `todoList`), results from `resultDetails.output` (a list of `{value}` chunks) with `isError`. Note VS Code also writes `GitHub.copilot-chat/transcripts/<id>.jsonl` in the *CLI* event format, but it is often incomplete for a live session. Discovery scans `Code`, `Code - Insiders` and `VSCodium` under the platform config root (overridable via `VSCODE_USER_DIR`); the project name comes from the workspace's `workspace.json`, since the storage dir is an opaque hash, and prompt-less journals (VS Code writes one the moment a panel opens) are skipped. `_vscode_storage_dirs()` only trusts `XDG_CONFIG_HOME`/`APPDATA` when `home` is the real home, so tests passing a temp `home` cannot escape into real storage.

**Pi** ([pi.dev](https://pi.dev)): stores sessions as on-disk JSONL at `~/.pi/agent/sessions/--<cwd>--/<timestamp>_<uuid>.jsonl` (overridable via `PI_CODING_AGENT_DIR`). Entries are one JSON object per line linked by `id`/`parentId`; the parser walks them in file order. Detected as `"pi"` via the `{"type":"session",...}` header or a `{"type":"message","message":{"role":...}}` entry (Pi nests `role` under `message`, unlike Claude). A `message` with `role:"user"` starts a turn; `assistant` content blocks are `text`/`thinking`/`toolCall`; `toolResult` messages attach output by `toolCallId`. Pi's lowercase built-ins (`bash`, `read`, `write`, `edit`, `grep`, `find`, `ls`) are mapped to Claude-Code-style TitleCase names (`find`/`ls` → `Glob`).

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
