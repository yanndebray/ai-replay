# Changelog

## 0.3.0

### New commands
- `extract` subcommand: recover embedded turn/bookmark data from a generated replay HTML

### New flags
- `--exclude-turns N,N,...`: exclude specific turns by index (combines with `--turns`)
- `--redact "text"` / `--redact "text=repl"`: custom string replacement at generation time
- `--no-auto-redact`: disable built-in secret pattern redaction (renamed from `--no-redact`)
- `--description TEXT`: customize meta description for link previews
- `--og-image URL`: customize OG image for link previews
- `--open`: launch replay in default browser after generation

### Player
- Keyboard shortcuts: `Shift+→/L` and `Shift+←/H` to jump between turns
- Keyboard shortcuts: `T` / `Shift+T` to jump between thinking/tool blocks
- Active block indicator (left border highlight)
- OG/Twitter meta tags with default image for link previews

### Other
- Default OG image hosted on GitHub Pages
- Fix `npm test` to avoid Playwright/Node test runner conflict

## 0.2.0

### Player
- Diff view for Edit tool calls: removed lines in red, added lines in green (unified diff style)
- Code block view for Write tool calls instead of raw JSON
- Smarter tool header previews: file path for Edit/Write/Read, pattern for Grep/Glob, command for Bash
- Red indicator dot on failed tool calls (individual and grouped)
- Red result text for error messages
- `<tool_use_error>` XML tags stripped from error results for clean display

### Parser
- Cursor agent transcript support (auto-detected, paced timing)
- Pass `is_error` flag from tool results through to renderer
- Strip `<tool_use_error>` wrapper tags from tool result text
- Strip `<ide_opened_file>` tags from VS Code extension transcripts

### Other
- Raise tool grouping threshold from 3 to 5 consecutive calls for better per-tool visibility
- Add Privacy section to README
- Add e2e tests for diff view, Write view, error indicators, and tag stripping (32 total)

## 0.1.2

- Fix: expanding a block while paused then stepping forward now reveals the next block instead of jumping to the next turn
- Fix: expanded blocks are now collapsed when rewinding
- Fix: `#turn=0` deep link shows splash screen instead of blank page
- Add Playwright e2e integration tests (25 tests covering playback, stepping, expand/collapse, keyboard shortcuts, navbar, progress bar, speed control, chapters)

## 0.1.1

- Fix README screenshot layout for npm

## 0.1.0 — Initial release

### Player
- Interactive playback with block-by-block animation
- Step forward/back through individual blocks within turns
- Session timer (elapsed / total) with per-second ticking during playback
- Speed control (0.5x to 5x), timer scales with playback speed
- Progress bar with turn dots, bookmark dots, and hover tooltip
- Bookmarks / chapters with dropdown navigation
- Toggle visibility of thinking blocks and tool calls
- Keyboard shortcuts: Space/K (play/pause), arrows/H/L (step)
- Splash screen with play button, skip via click or keyboard
- Bottom-anchored scrolling with smooth animations
- Embeddable via iframe with adaptive layout

### Themes
- Built-in themes: `tokyo-night` (default), `monokai`, `solarized-dark`, `github-light`, `dracula`, `bubbles`
- Custom themes via `--theme-file` with JSON color overrides
- `extraCss` support for full layout customization

### Parser
- Handles Claude Code streaming format (incremental content blocks)
- Merges consecutive command messages (slash commands + stdout)
- Strips internal tags (`<system-reminder>`, `<local-command-caveat>`, etc.)
- Filters empty turns and "No response requested." boilerplate
- Time range and turn range filtering

### Output
- Single self-contained HTML file, zero external dependencies
- Deflate + base64 compression (~60-70% size reduction)
- Minified CSS/JS via esbuild
- Automatic secret redaction (API keys, tokens, connection strings, etc.)
