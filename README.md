# ai-replay

Convert Claude Code, Cursor, and Codex CLI session transcripts to interactive HTML replays.

> Python port of [claude-replay](https://github.com/es617/claude-replay) by es617 (original JavaScript version).

## Installation

```bash
uv tool install ai-replay
```

## Usage

```bash
# Generate HTML replay from a session file
ai-replay session.jsonl -o replay.html

# Generate from a session ID (auto-discovered)
ai-replay <session-id> -o replay.html

# Extract turns from a generated replay
ai-replay extract replay.html

# Serve replay on local HTTP server
ai-replay session.jsonl --serve --port 4000
```

## Options

- `-o, --output FILE`: Output HTML file (default: stdout)
- `--title TEXT`: Custom page title
- `--theme NAME`: Color theme (default: tokyo-night)
- `--no-redact`: Disable automatic secret redaction
- `--open`: Open in browser after generating
- `--no-compress`: Embed raw JSON instead of compressed
- `--serve`: Serve on a local HTTP server
- `--port INTEGER`: Port for --serve (default: 4000)
- `--turns TEXT`: Turn range filter (e.g. "1-10")
- `--exclude TEXT`: Exclude specific turns (e.g. "3,7")
