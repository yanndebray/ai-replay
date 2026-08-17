# Changelog

## 0.5.0

- Add [GitHub Copilot CLI](https://github.com/github/copilot-cli) session support: the event log at `~/.copilot/session-state/<uuid>/events.jsonl` (overridable via `COPILOT_CLI_DIR`) is auto-discovered and auto-detected as the `copilot` format — no export step needed
- Map Copilot's lowercase tool names (`bash`, `view`, `create`, `edit`, `grep`, …) to Claude-Code-style TitleCase so tool calls render with the same previews and diffs; unmapped snake_case names become TitleCase (`ask_user` → `AskUser`)
- Render Copilot `reasoningText` as thinking blocks, and surface the error message for rejected or failed tool calls
- Add VS Code Copilot Chat support: pass a `chatSessions/<id>.jsonl` file and it is auto-detected as the `copilot-chat` format. Unlike the other formats this is a journal (snapshot + patches) rather than an append-only transcript, so it is replayed to its final state before turns are extracted — reading it line-by-line would drop every streamed answer
- Map VS Code tool ids (`run_in_terminal`, `copilot_readFile`, `copilot_createFile`, …) to Claude-Code-style names, using the command as typed rather than VS Code's environment-prefixed rewrite
- Add Pi ([pi.dev](https://pi.dev)) session support: on-disk JSONL sessions under `~/.pi/agent/sessions/` (overridable via `PI_CODING_AGENT_DIR`) are auto-discovered and auto-detected as the `pi` format — no export step needed
- Map Pi's lowercase built-in tool names (`bash`, `read`, `write`, `edit`, `grep`, `find`, `ls`) to Claude-Code-style TitleCase so tool calls render with the same previews and diffs
- Render Pi thinking blocks as thinking blocks

## 0.4.2

- Make the CI release step idempotent so re-runs don't fail when the GitHub release already exists

## 0.3.0

- Add OpenCode session support: export a session with `opencode export <sessionID> > session.json` and pass it to `ai-replay` (auto-detected as the `opencode` format)
- Map OpenCode's lowercase tool names (`bash`, `read`, `write`, `edit`, …) to Claude-Code-style TitleCase so tool calls render with the same previews and diffs
- Render OpenCode reasoning blocks as thinking blocks

## 0.2.2

- Fix: Codex sessions showing `(no summary)` in the session picker

## 0.2.1

- Fix: sync `__version__` in `__init__.py` with `pyproject.toml` so `ai-replay --version` reports the correct version

## 0.2.0

- Add interactive TUI session picker as the default command (`ai-replay` with no arguments)
- Auto-create `<agent>-<sessionID>/index.html` in the current directory after picking a session
- Support filtering sessions by agent name with `--agent`

## 0.1.2

- Add smoke tests
- Relax `uv_build` version constraint
- Fix CI warnings: bump actions to Node.js 24

## 0.1.1

- Fix Docker build
- Minor CI improvements

## 0.1.0

- Initial Python port of `ai-replay`
- Support for Claude Code, Cursor, and Codex CLI session formats
- Themes: tokyo-night, monokai, solarized-dark, github-light, dracula, bubbles
- `generate`, `extract` commands
- Secret redaction, turn filtering, multi-session merge, `--serve` mode
