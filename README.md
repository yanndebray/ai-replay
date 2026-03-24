# ai-replay

Convert Claude Code, Cursor, and Codex CLI session transcripts to interactive HTML replays.

> Python port of [claude-replay](https://github.com/es617/claude-replay) by es617 (original JavaScript version).

## Installation

```bash
uv tool install ai-replay
```

## Usage

```bash
# Interactive session picker (default — runs when no arguments given)
ai-replay

# Generate HTML replay from a session file
ai-replay session.jsonl -o replay.html

# Generate from a session ID (auto-discovered)
ai-replay <session-id> -o replay.html

# Extract turns from a generated replay
ai-replay extract replay.html

# Serve replay on local HTTP server
ai-replay session.jsonl --serve --port 4000
```

### Interactive picker options

```bash
ai-replay pick                  # explicit invocation
ai-replay pick --limit 30       # show more sessions (default: 20)
ai-replay pick --agent codex    # filter to one agent (partial match)
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

---

## Development

### Prerequisites

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (recommended) **or** pip

### Install the dev version from this branch

**With uv (recommended):**

```bash
# Clone the repo (or your fork)
git clone https://github.com/jeanclawd/ai-replay.git
cd ai-replay

# Check out the feature branch
git checkout feat/interactive-tui-picker

# Install in editable mode with all dependencies
uv pip install -e .

# Verify
ai-replay --version
```

**With pip:**

```bash
git clone https://github.com/jeanclawd/ai-replay.git
cd ai-replay
git checkout feat/interactive-tui-picker

pip install -e .

# Verify
ai-replay --version
```

### Run the tests

```bash
# With uv
uv run pytest

# With pip / standard Python
pip install pytest
pytest
```

Expected output:

```
collected 6 items

tests/test_discover.py::test_discover_claude_and_codex PASSED
tests/test_discover.py::test_discover_agent_field PASSED
tests/test_discover.py::test_discover_limit PASSED
tests/test_discover.py::test_discover_summary_extraction PASSED
tests/test_discover.py::test_discover_no_sessions PASSED
tests/test_discover.py::test_discover_skips_agent_files PASSED

6 passed in 0.17s
```

### Try the interactive picker

```bash
# Launch the TUI picker (requires Claude Code, Codex, or Cursor sessions on disk)
ai-replay

# Or explicitly:
ai-replay pick --limit 10
```

You should see an arrow-key menu like:

```
Loading sessions...
? Select a session to replay:
❯ Claude Code   2026-03-24 18:42    142 KB  Fix auth bug in middleware
  Codex         2026-03-23 11:10     98 KB  Add streaming support
  Cursor        2026-03-22 09:55    210 KB  Initial project scaffold
```

Select a session and it will generate the HTML replay and open it in your browser.

### Project structure

```
src/ai_replay/
├── __init__.py        # CLI entrypoint (click commands)
├── discover.py        # Session discovery across all agents  ← new in this branch
├── parser.py          # JSONL/JSON session parser
├── renderer.py        # HTML renderer
├── resolve_session.py # Resolve session ID → file path
├── secrets.py         # Secret redaction
└── templates/         # HTML templates
tests/
└── test_discover.py   # Discovery tests  ← new in this branch
```
