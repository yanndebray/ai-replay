# Changelog

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
