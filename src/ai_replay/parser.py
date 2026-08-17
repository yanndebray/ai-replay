"""
Parse Claude Code, Cursor, Codex CLI, OpenCode, Pi, GitHub Copilot CLI, and
VS Code Copilot Chat JSONL transcripts into structured turns.

Ported from parser.mjs (JavaScript) to Python 3.10+.

Public API
----------
detect_format(file_path)        -> str
parse_session(file_path)        -> list[dict]
filter_turns(turns, ...)        -> list[dict]
apply_paced_timing(turns)       -> None  (mutates in-place)

Each turn dict has the shape::

    {
        "index":        int,
        "user_text":    str,
        "blocks":       list[block_dict],
        "timestamp":    str,                 # ISO-8601
        "system_events": list[str],          # optional
        "bookmark":     str | None,          # optional
    }

Each block_dict::

    {
        "kind":      "text" | "thinking" | "tool_use",
        "text":      str,
        "tool_call": tool_call_dict | None,
        "timestamp": str | None,
    }

Each tool_call_dict::

    {
        "tool_use_id":       str,
        "name":              str,
        "input":             dict,
        "result":            str | None,
        "result_timestamp":  str | None,
        "is_error":          bool,
    }
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Text cleaning helpers
# ---------------------------------------------------------------------------


def _clean_system_tags(text: str) -> str:
    """Strip / transform known system tags from user message text."""
    # Replace <task-notification> blocks with a compact marker
    text = re.sub(
        r"<task-notification>\s*<task-id>[^<]*</task-id>"
        r"\s*<output-file>[^<]*</output-file>"
        r"\s*<status>([^<]*)</status>"
        r"\s*<summary>([^<]*)</summary>"
        r"\s*</task-notification>",
        lambda m: f"[bg-task: {m.group(2)}]",
        text,
    )
    # Remove trailing "Read the output file..." lines that follow notifications
    text = re.sub(r"\n*Read the output file to retrieve the result:[^\n]*", "", text)
    # Unwrap Cursor's <user_query> tags
    text = re.sub(
        r"<user_query>([\s\S]*?)</user_query>\s*",
        lambda m: m.group(1).strip(),
        text,
    )
    # Remove <system-reminder> blocks
    text = re.sub(r"<system-reminder>[\s\S]*?</system-reminder>\s*", "", text)
    # Remove IDE context tags (VS Code extension)
    text = re.sub(r"<ide_opened_file>[\s\S]*?</ide_opened_file>\s*", "", text)
    # Remove internal caveat boilerplate
    text = re.sub(r"<local-command-caveat>[\s\S]*?</local-command-caveat>\s*", "", text)
    # Extract slash command name, keep as visible text
    text = re.sub(
        r"<command-name>([\s\S]*?)</command-name>\s*",
        lambda m: m.group(1).strip() + "\n",
        text,
    )
    # Remove command-message (redundant with command-name)
    text = re.sub(r"<command-message>[\s\S]*?</command-message>\s*", "", text)
    # Remove empty command-args
    text = re.sub(r"<command-args>\s*</command-args>\s*", "", text)
    # Keep non-empty command args
    text = re.sub(
        r"<command-args>([\s\S]*?)</command-args>\s*",
        lambda m: (m.group(1).strip() + "\n") if m.group(1).strip() else "",
        text,
    )
    # Remove local command stdout
    text = re.sub(r"<local-command-stdout>[\s\S]*?</local-command-stdout>\s*", "", text)
    return text.strip()


def _extract_text(content: str | list) -> str:
    """Extract plain text from user message content (string or block array)."""
    if isinstance(content, str):
        return _clean_system_tags(content)
    parts = [b.get("text", "") for b in content if b.get("type") == "text"]
    return _clean_system_tags("\n".join(parts))


def _is_tool_result_only(content: str | list) -> bool:
    """Return True if *content* consists only of tool_result blocks."""
    if isinstance(content, str):
        return False
    return bool(content) and all(b.get("type") == "tool_result" for b in content)


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def _is_opencode_export(obj: Any) -> bool:
    """Return True if *obj* is an OpenCode ``opencode export`` JSON object.

    The export is a single JSON object of the shape
    ``{"info": {...}, "messages": [{"info": {"role": ...}, "parts": [...]}, ...]}``.
    """
    if not isinstance(obj, dict):
        return False
    if not isinstance(obj.get("info"), dict):
        return False
    messages = obj.get("messages")
    if not isinstance(messages, list):
        return False
    for msg in messages:
        if isinstance(msg, dict) and isinstance(msg.get("info"), dict):
            if msg["info"].get("role") in ("user", "assistant"):
                return True
    # Empty messages list with a session info block still counts as OpenCode.
    return messages == [] and "id" in obj["info"]


def _detect_format_from_text(text: str) -> str:
    """
    Detect transcript format by peeking at the first parseable JSON line.

    Returns one of: ``"claude"``, ``"cursor"``, ``"codex"``, ``"opencode"``,
    ``"pi"``, ``"copilot"``, ``"copilot-chat"``, ``"replay"``, or ``"unknown"``.
    """
    # OpenCode export is a single (often pretty-printed) JSON object, so it must
    # be detected from the whole text before the line-by-line JSONL scan.
    try:
        whole = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        whole = None
    if _is_opencode_export(whole):
        return "opencode"

    for line in text.split("\n"):
        trimmed = line.strip()
        if not trimmed:
            continue
        try:
            obj = json.loads(trimmed)
        except json.JSONDecodeError:
            continue
        if obj.get("user_text") is not None and obj.get("blocks") is not None:
            return "replay"
        if obj.get("type") == "session_meta":
            return "codex"
        # GitHub Copilot CLI: dotted event names in a ``{type, data, ...}``
        # envelope. ``session.start`` carries ``producer: "copilot-agent"``.
        # The dotted names never collide with Pi's ``session`` / ``message`` or
        # Claude's ``user`` / ``assistant``.
        if obj.get("type") in _COPILOT_EVENT_TYPES and isinstance(
            obj.get("data"), dict
        ):
            return "copilot"
        # VS Code Copilot Chat: a journal of {kind, k, v} entries. ``kind 0`` is
        # the opening snapshot and carries the session's ``requests`` array.
        if obj.get("kind") == 0 and isinstance(obj.get("v"), dict):
            snapshot = obj["v"]
            if "requests" in snapshot and (
                "sessionId" in snapshot or "responderUsername" in snapshot
            ):
                return "copilot-chat"
        # Pi: session header ``{"type":"session","version":..,"cwd":..}`` or a
        # ``{"type":"message","message":{"role":...}}`` entry. Pi nests ``role``
        # under ``message`` (Claude puts ``type`` at top level), so these never
        # collide with the Claude/Cursor checks below.
        if obj.get("type") == "session" and ("cwd" in obj or "version" in obj):
            return "pi"
        if obj.get("type") == "message" and isinstance(obj.get("message"), dict):
            if obj["message"].get("role") in ("user", "assistant", "toolResult"):
                return "pi"
        if obj.get("type") in ("user", "assistant"):
            return "claude"
        if obj.get("role") in ("user", "assistant"):
            return "cursor"
    return "unknown"


def detect_format(file_path: Path | str) -> str:
    """Detect the session format of a JSONL file.

    Returns one of: ``"claude"``, ``"cursor"``, ``"codex"``, ``"opencode"``,
    ``"pi"``, ``"copilot"``, ``"copilot-chat"``, ``"replay"``, or ``"unknown"``.
    """
    text = Path(file_path).read_text(encoding="utf-8")
    return _detect_format_from_text(text)


# ---------------------------------------------------------------------------
# Helper: make canonical dict shapes
# ---------------------------------------------------------------------------


def _make_tool_call(
    tool_use_id: str,
    name: str,
    inp: dict[str, Any],
    result: str | None = None,
    result_timestamp: str | None = None,
    is_error: bool = False,
) -> dict[str, Any]:
    return {
        "tool_use_id": tool_use_id,
        "name": name,
        "input": inp,
        "result": result,
        "result_timestamp": result_timestamp,
        "is_error": is_error,
    }


def _make_block(
    kind: str,
    text: str = "",
    tool_call: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    return {"kind": kind, "text": text, "tool_call": tool_call, "timestamp": timestamp}


def _make_turn(
    index: int,
    user_text: str,
    blocks: list[dict[str, Any]],
    timestamp: str,
    system_events: list[str] | None = None,
    bookmark: str | None = None,
) -> dict[str, Any]:
    turn: dict[str, Any] = {
        "index": index,
        "user_text": user_text,
        "blocks": blocks,
        "timestamp": timestamp,
        "system_events": system_events or [],
    }
    if bookmark is not None:
        turn["bookmark"] = bookmark
    return turn


# ---------------------------------------------------------------------------
# Shared Claude Code / Cursor parsing helpers
# ---------------------------------------------------------------------------


def _collect_assistant_blocks(
    entries: list[dict], start: int
) -> tuple[list[dict[str, Any]], int]:
    """
    Collect consecutive assistant content blocks starting at *start*.

    Returns ``(blocks, next_index)``.
    """
    blocks: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    i = start

    while i < len(entries):
        entry = entries[i]
        role = (entry.get("message") or {}).get("role") or entry.get("type")
        if role != "assistant":
            break

        entry_ts: str | None = entry.get("timestamp")
        content = (entry.get("message") or {}).get("content") or []

        if isinstance(content, list):
            for block in content:
                btype = block.get("type")

                if btype == "text":
                    text = (block.get("text") or "").strip()
                    if not text or text == "No response requested.":
                        continue
                    key = f"text:{text}"
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    blocks.append(_make_block("text", text=text, timestamp=entry_ts))

                elif btype == "thinking":
                    text = (block.get("thinking") or "").strip()
                    if not text:
                        continue
                    key = f"thinking:{text}"
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    blocks.append(_make_block("thinking", text=text, timestamp=entry_ts))

                elif btype == "tool_use":
                    tool_id = block.get("id") or ""
                    key = f"tool_use:{tool_id}"
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    tc = _make_tool_call(
                        tool_use_id=tool_id,
                        name=block.get("name") or "",
                        inp=block.get("input") or {},
                    )
                    blocks.append(_make_block("tool_use", tool_call=tc, timestamp=entry_ts))

        i += 1

    return blocks, i


def _attach_tool_results(
    blocks: list[dict[str, Any]], entries: list[dict], result_start: int
) -> int:
    """
    Scan forward from *result_start* for ``tool_result`` user messages,
    match them to ``tool_use`` blocks by ``tool_use_id``, and attach results.

    Returns the index after consumed entries.
    """
    # Build map of pending tool calls by id
    pending: dict[str, dict[str, Any]] = {}
    for b in blocks:
        if b["kind"] == "tool_use" and b.get("tool_call"):
            tc = b["tool_call"]
            pending[tc["tool_use_id"]] = tc

    if not pending:
        return result_start

    i = result_start
    while i < len(entries) and pending:
        entry = entries[i]
        role = (entry.get("message") or {}).get("role") or entry.get("type")

        if role == "assistant":
            break

        if role == "user":
            content = (entry.get("message") or {}).get("content") or ""
            if isinstance(content, list):
                has_tool_result = False
                for block in content:
                    if block.get("type") == "tool_result":
                        has_tool_result = True
                        tid = block.get("tool_use_id") or ""
                        if tid in pending:
                            result_content = block.get("content")
                            if isinstance(result_content, list):
                                result_text = "\n".join(
                                    p.get("text", "")
                                    for p in result_content
                                    if p.get("type") == "text"
                                )
                            elif isinstance(result_content, str):
                                result_text = result_content
                            elif result_content is None:
                                result_text = ""
                            else:
                                result_text = str(result_content)

                            # Strip <tool_use_error> wrapper if present
                            result_text = re.sub(
                                r"^<tool_use_error>([\s\S]*)</tool_use_error>$",
                                r"\1",
                                result_text,
                            )
                            tc = pending[tid]
                            tc["result"] = result_text
                            tc["result_timestamp"] = entry.get("timestamp")
                            tc["is_error"] = bool(block.get("is_error"))
                            del pending[tid]
                if not has_tool_result:
                    break
            else:
                break
        i += 1

    return i


# ---------------------------------------------------------------------------
# Claude Code format parser
# ---------------------------------------------------------------------------


def _parse_claude_entries(entries: list[dict]) -> list[dict[str, Any]]:
    """Parse pre-decoded Claude Code / Cursor entries into turn dicts."""
    turns: list[dict[str, Any]] = []
    i = 0
    turn_index = 0

    while i < len(entries):
        entry = entries[i]
        role = (entry.get("message") or {}).get("role") or entry.get("type")

        if role == "user":
            content = (entry.get("message") or {}).get("content") or ""
            if _is_tool_result_only(content):
                i += 1
                continue

            user_text = _extract_text(content)
            timestamp = entry.get("timestamp") or ""
            i += 1

            # Absorb consecutive non-tool-result user messages into the same turn
            while i < len(entries):
                nxt = entries[i]
                next_role = (nxt.get("message") or {}).get("role") or nxt.get("type")
                if next_role != "user":
                    break
                next_content = (nxt.get("message") or {}).get("content") or ""
                if _is_tool_result_only(next_content):
                    break
                next_text = _extract_text(next_content)
                if next_text:
                    user_text = (user_text + "\n" + next_text) if user_text else next_text
                i += 1

            # Extract system events (bg-task notifications) from user text
            system_events: list[str] = []

            def _pull_event(m: re.Match) -> str:  # noqa: E306
                system_events.append(m.group(1))
                return ""

            user_text = re.sub(r"\[bg-task:\s*(.+)\]", _pull_event, user_text)
            user_text = user_text.strip()

            assistant_blocks, next_i = _collect_assistant_blocks(entries, i)
            i = next_i
            i = _attach_tool_results(assistant_blocks, entries, i)

            turn_index += 1
            turns.append(
                _make_turn(turn_index, user_text, assistant_blocks, timestamp, system_events)
            )

        elif role == "assistant":
            assistant_blocks, next_i = _collect_assistant_blocks(entries, i)
            i = next_i
            i = _attach_tool_results(assistant_blocks, entries, i)

            if turns:
                turns[-1]["blocks"].extend(assistant_blocks)
            else:
                # First entry is assistant — create an anonymous turn
                turn_index += 1
                turns.append(
                    _make_turn(
                        turn_index, "", assistant_blocks, entry.get("timestamp") or ""
                    )
                )
        else:
            i += 1

    return turns


def _parse_claude_format(lines: list[dict]) -> list[dict[str, Any]]:
    return _parse_claude_entries(lines)


def _parse_cursor_format(lines: list[dict]) -> list[dict[str, Any]]:
    """
    Parse Cursor format entries into turn dicts.

    Cursor uses ``role`` (not ``type``) and has no timestamps.
    All assistant blocks except the last per turn are treated as thinking.
    """
    # Normalise to Claude-Code shape
    normalised: list[dict] = []
    for obj in lines:
        role = (obj.get("message") or {}).get("role") or obj.get("role")
        if role in ("user", "assistant"):
            normalised.append(
                {
                    "type": role,
                    "message": {
                        "role": role,
                        "content": (obj.get("message") or {}).get("content") or "",
                    },
                    "timestamp": obj.get("timestamp"),
                }
            )

    turns = _parse_claude_entries(normalised)

    # Cursor: all assistant text blocks except the last per turn become thinking
    for turn in turns:
        for j in range(len(turn["blocks"]) - 1):
            if turn["blocks"][j]["kind"] == "text":
                turn["blocks"][j]["kind"] = "thinking"

    return turns


# ---------------------------------------------------------------------------
# Codex format parser
# ---------------------------------------------------------------------------


def _parse_codex_patch(patch_str: str) -> dict[str, Any]:
    """
    Parse a Codex ``apply_patch`` string into Edit/Write-compatible input.

    ``*** Add File``  → ``Write`` (new file)
    ``*** Update File`` → ``Edit`` (modify existing file)
    """
    patch_lines = patch_str.split("\n")
    while patch_lines and patch_lines[-1] == "":
        patch_lines.pop()

    file_path = ""
    is_new = False
    old_lines: list[str] = []
    new_lines: list[str] = []

    for line in patch_lines:
        if line.startswith("*** Begin Patch") or line.startswith("*** End Patch"):
            continue
        if line.startswith("*** Add File:"):
            file_path = line[len("*** Add File:"):].strip()
            is_new = True
            continue
        if line.startswith("*** Update File:"):
            file_path = line[len("*** Update File:"):].strip()
            is_new = False
            continue
        if line.startswith("@@"):
            continue  # context marker
        if line.startswith("+"):
            new_lines.append(line[1:])
        elif line.startswith("-"):
            old_lines.append(line[1:])
        else:
            # Context line (unchanged) — appears in both
            old_lines.append(line)
            new_lines.append(line)

    if is_new:
        return {"file_path": file_path, "content": "\n".join(new_lines), "isNew": True}
    return {
        "file_path": file_path,
        "old_string": "\n".join(old_lines),
        "new_string": "\n".join(new_lines),
        "isNew": False,
    }


def _extract_codex_user_text(text: str) -> str:
    """Extract the actual user request from a Codex user message."""
    marker = "## My request for Codex:"
    idx = text.find(marker)
    if idx != -1:
        return text[idx + len(marker):].strip()
    marker2 = "## My request for Codex"
    idx2 = text.find(marker2)
    if idx2 != -1:
        after = text[idx2 + len(marker2):]
        return re.sub(r"^:?\s*", "", after).strip()
    return text.strip()


def _parse_codex_format(events: list[dict]) -> list[dict[str, Any]]:
    """Parse Codex CLI JSONL events into turn dicts."""
    turns: list[dict[str, Any]] = []
    turn_index = 0
    current_user_text = ""
    current_timestamp = ""
    current_blocks: list[dict[str, Any]] = []
    pending_calls: dict[str, dict[str, Any]] = {}
    in_turn = False

    for evt in events:
        evt_type = evt.get("type")
        payload: dict[str, Any] = evt.get("payload") or {}
        ts: str | None = evt.get("timestamp")

        if evt_type == "event_msg" and payload.get("type") == "task_started":
            in_turn = True
            current_user_text = ""
            current_timestamp = ts or ""
            current_blocks = []
            pending_calls = {}
            continue

        if evt_type == "event_msg" and payload.get("type") == "task_complete":
            if in_turn:
                turn_index += 1
                turns.append(
                    _make_turn(
                        turn_index,
                        current_user_text,
                        current_blocks,
                        current_timestamp,
                    )
                )
            in_turn = False
            continue

        if not in_turn:
            continue

        if evt_type == "event_msg" and payload.get("type") == "user_message":
            msg = payload.get("message") or ""
            current_user_text = _extract_codex_user_text(msg)
            if ts:
                current_timestamp = ts
            continue

        if evt_type == "response_item":
            ptype = payload.get("type")
            role = payload.get("role") or ""
            phase = payload.get("phase") or ""

            # User message as response_item — fallback if event_msg didn't fire
            if ptype == "message" and role == "user":
                content = payload.get("content") or []
                if isinstance(content, list):
                    text_parts = [
                        b.get("text", "")
                        for b in content
                        if b.get("type") == "input_text"
                    ]
                    extracted = _extract_codex_user_text("\n".join(text_parts))
                    if extracted and not current_user_text:
                        current_user_text = extracted
                continue

            # Skip developer/system messages
            if ptype == "message" and role == "developer":
                continue

            # Encrypted reasoning — skip
            if ptype == "reasoning":
                continue

            # Assistant text: commentary → thinking, final_answer → text
            if ptype == "message" and role == "assistant":
                content = payload.get("content") or []
                text_parts = [
                    b.get("text", "")
                    for b in (content if isinstance(content, list) else [])
                    if b.get("type") == "output_text"
                ]
                block_text = "\n".join(text_parts).strip()
                if not block_text:
                    continue
                kind = "thinking" if phase == "commentary" else "text"
                current_blocks.append(_make_block(kind, text=block_text, timestamp=ts))
                continue

            # exec_command tool call → Bash
            if ptype == "function_call":
                call_id = payload.get("call_id") or ""
                name = payload.get("name") or "unknown"
                try:
                    inp: dict[str, Any] = json.loads(payload.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    inp = {"raw": payload.get("arguments")}

                # Normalize exec_command → Bash
                if name == "exec_command" and inp.get("cmd"):
                    cmd = inp["cmd"]
                    if inp.get("workdir"):
                        cmd = f"cd {inp['workdir']} && {cmd}"
                    inp = {"command": cmd}

                tc = _make_tool_call(
                    tool_use_id=call_id,
                    name="Bash" if name == "exec_command" else name,
                    inp=inp,
                )
                current_blocks.append(_make_block("tool_use", tool_call=tc, timestamp=ts))
                pending_calls[call_id] = tc
                continue

            # exec_command result
            if ptype == "function_call_output":
                call_id = payload.get("call_id") or ""
                output = payload.get("output") or ""
                cleaned = re.sub(r"^Chunk ID:.*\n?", "", output, flags=re.MULTILINE)
                cleaned = re.sub(r"^Wall time:.*\n?", "", cleaned, flags=re.MULTILINE)
                cleaned = re.sub(
                    r"^Process exited with code \d+\n?", "", cleaned, flags=re.MULTILINE
                )
                cleaned = re.sub(
                    r"^Original token count:.*\n?", "", cleaned, flags=re.MULTILINE
                )
                cleaned = re.sub(r"^Output:\n?", "", cleaned, flags=re.MULTILINE)
                cleaned = cleaned.strip()
                if call_id in pending_calls:
                    tc = pending_calls[call_id]
                    tc["result"] = cleaned
                    tc["result_timestamp"] = ts
                    tc["is_error"] = (
                        "Process exited with code" in output
                        and "code 0" not in output
                    )
                    del pending_calls[call_id]
                continue

            # apply_patch / other custom tool calls
            if ptype == "custom_tool_call":
                call_id = payload.get("call_id") or ""
                name = payload.get("name") or "unknown"
                if name == "apply_patch":
                    parsed = _parse_codex_patch(payload.get("input") or "")
                    mapped_name = "Write" if parsed.get("isNew") else "Edit"
                    inp = parsed
                else:
                    mapped_name = name
                    inp = {"raw": payload.get("input") or ""}
                tc = _make_tool_call(
                    tool_use_id=call_id,
                    name=mapped_name,
                    inp=inp,
                )
                current_blocks.append(_make_block("tool_use", tool_call=tc, timestamp=ts))
                pending_calls[call_id] = tc
                continue

            # custom tool call result
            if ptype == "custom_tool_call_output":
                call_id = payload.get("call_id") or ""
                raw_output = payload.get("output")
                if isinstance(raw_output, str):
                    output_str = raw_output
                elif isinstance(raw_output, dict) and raw_output.get("output"):
                    output_str = raw_output["output"]
                else:
                    output_str = ""
                if call_id in pending_calls:
                    tc = pending_calls[call_id]
                    tc["result"] = output_str.strip()
                    tc["result_timestamp"] = ts
                    tc["is_error"] = (
                        isinstance(raw_output, dict)
                        and (raw_output.get("metadata") or {}).get("exit_code") not in (None, 0)
                    )
                    del pending_calls[call_id]
                continue

    # Handle session that ends without task_complete
    if in_turn and (current_user_text or current_blocks):
        turn_index += 1
        turns.append(
            _make_turn(
                turn_index, current_user_text, current_blocks, current_timestamp
            )
        )

    # Drop empty turns and re-index
    filtered = [
        t
        for t in turns
        if t["user_text"]
        or any(
            b["kind"] == "tool_use"
            or (b["kind"] == "text" and b.get("text"))
            or (b["kind"] == "thinking" and b.get("text"))
            for b in t["blocks"]
        )
    ]
    for j, t in enumerate(filtered):
        t["index"] = j + 1
    return filtered


# ---------------------------------------------------------------------------
# OpenCode format parser
# ---------------------------------------------------------------------------


# OpenCode uses lowercase tool names; the player template renders rich previews
# and diffs only for Claude-Code-style TitleCase names, so we map them here.
_OPENCODE_TOOL_MAP = {
    "bash": "Bash",
    "read": "Read",
    "write": "Write",
    "edit": "Edit",
    "patch": "Edit",
    "glob": "Glob",
    "grep": "Grep",
    "ls": "Glob",
    "list": "Glob",
    "webfetch": "WebFetch",
    "websearch": "WebSearch",
    "codesearch": "Grep",
    "task": "Task",
    "todo": "TodoWrite",
    "todowrite": "TodoWrite",
    "question": "Question",
    "skill": "Skill",
}


def _ms_to_iso(ms: int | float | None) -> str | None:
    """Convert epoch milliseconds to an ISO-8601 UTC string (or ``None``)."""
    if ms is None:
        return None
    try:
        dt = datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _normalize_opencode_tool_input(name: str, inp: dict[str, Any]) -> dict[str, Any]:
    """Map OpenCode tool input keys to the Claude-Code-style keys the player expects."""
    if not isinstance(inp, dict):
        return {"raw": inp}
    if name == "Bash" and inp.get("command"):
        command = inp["command"]
        if inp.get("workdir"):
            command = f"cd {inp['workdir']} && {command}"
        return {"command": command}
    if name == "Write" and inp.get("filePath"):
        return {"file_path": inp["filePath"], "content": inp.get("content") or ""}
    if name == "Read" and inp.get("filePath"):
        return {"file_path": inp["filePath"]}
    if name == "Edit" and inp.get("filePath"):
        normalized = dict(inp)
        normalized["file_path"] = inp["filePath"]
        return normalized
    return inp


def _parse_opencode_format(export: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse an ``opencode export`` JSON object into turn dicts.

    The export shape is::

        {"info": {...}, "messages": [{"info": {"role": ...}, "parts": [...]}, ...]}

    A ``user`` message starts a new turn; subsequent ``assistant`` messages
    contribute ``text`` / ``thinking`` / ``tool_use`` blocks to that turn.
    ``step-start`` / ``step-finish`` parts are ignored.
    """
    messages = export.get("messages") or []

    turns: list[dict[str, Any]] = []
    turn_index = 0
    current_user_text = ""
    current_timestamp = ""
    current_blocks: list[dict[str, Any]] = []
    in_turn = False

    def _finalize() -> None:
        nonlocal turn_index, current_user_text, current_timestamp, current_blocks, in_turn
        if not in_turn:
            return
        if current_user_text or current_blocks:
            turn_index += 1
            turns.append(
                _make_turn(
                    turn_index, current_user_text, current_blocks, current_timestamp
                )
            )
        current_user_text = ""
        current_timestamp = ""
        current_blocks = []
        in_turn = False

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        info = msg.get("info") or {}
        role = info.get("role")
        msg_ts = _ms_to_iso((info.get("time") or {}).get("created"))
        parts = msg.get("parts") or []

        if role == "user":
            # A new user message closes the previous turn and opens a new one.
            _finalize()
            in_turn = True
            current_timestamp = msg_ts or ""
            text_parts = [
                (p.get("text") or "").strip()
                for p in parts
                if isinstance(p, dict)
                and p.get("type") == "text"
                and (p.get("text") or "").strip()
            ]
            current_user_text = "\n".join(text_parts)
            continue

        if role == "assistant":
            if not in_turn:
                # Assistant message with no preceding user message: open a turn.
                in_turn = True
                current_timestamp = msg_ts or ""
            for p in parts:
                if not isinstance(p, dict):
                    continue
                ptype = p.get("type")

                if ptype == "text":
                    text = (p.get("text") or "").strip()
                    if text:
                        current_blocks.append(_make_block("text", text=text, timestamp=msg_ts))

                elif ptype == "reasoning":
                    text = (p.get("text") or "").strip()
                    if text:
                        current_blocks.append(
                            _make_block("thinking", text=text, timestamp=msg_ts)
                        )

                elif ptype == "tool":
                    raw_name = p.get("tool") or "unknown"
                    name = _OPENCODE_TOOL_MAP.get(raw_name, raw_name[:1].upper() + raw_name[1:])
                    state = p.get("state") or {}
                    inp = _normalize_opencode_tool_input(name, state.get("input") or {})
                    output = state.get("output")
                    if isinstance(output, str):
                        result = output
                    elif output is None:
                        result = None
                    else:
                        result = json.dumps(output)
                    status = state.get("status")
                    metadata = state.get("metadata") or {}
                    is_error = status == "error" or (
                        metadata.get("exit") is not None and metadata.get("exit") != 0
                    )
                    result_ts = _ms_to_iso((state.get("time") or {}).get("end"))
                    tc = _make_tool_call(
                        tool_use_id=p.get("callID") or "",
                        name=name,
                        inp=inp,
                        result=result,
                        result_timestamp=result_ts,
                        is_error=bool(is_error),
                    )
                    current_blocks.append(
                        _make_block("tool_use", tool_call=tc, timestamp=msg_ts)
                    )
                # step-start / step-finish and unknown parts are ignored.
            continue

        # Other roles (system, etc.) are ignored.

    _finalize()

    for j, t in enumerate(turns):
        t["index"] = j + 1
    return turns


# ---------------------------------------------------------------------------
# Pi format parser
# ---------------------------------------------------------------------------


# Pi (https://pi.dev) uses lowercase tool names; the player template renders
# rich previews/diffs only for Claude-Code-style TitleCase names, so we map the
# built-in tools (bash, read, edit, write, grep, find, ls) here.
_PI_TOOL_MAP = {
    "bash": "Bash",
    "read": "Read",
    "write": "Write",
    "edit": "Edit",
    "grep": "Grep",
    "find": "Glob",
    "ls": "Glob",
}


def _normalize_pi_tool_input(name: str, inp: dict[str, Any]) -> dict[str, Any]:
    """Map Pi tool input keys to the Claude-Code-style keys the player expects.

    Pi's built-ins use ``path`` (read/write/edit/find/grep/ls), ``oldText`` /
    ``newText`` (edit) and ``command`` (bash).
    """
    if not isinstance(inp, dict):
        return {"raw": inp}
    if name == "Read" and inp.get("path"):
        return {"file_path": inp["path"]}
    if name == "Write" and inp.get("path"):
        return {"file_path": inp["path"], "content": inp.get("content") or ""}
    if name == "Edit" and inp.get("path"):
        normalized = {"file_path": inp["path"]}
        if inp.get("oldText") is not None:
            normalized["old_string"] = inp["oldText"]
        if inp.get("newText") is not None:
            normalized["new_string"] = inp["newText"]
        if inp.get("replaceAll") is not None:
            normalized["replace_all"] = inp["replaceAll"]
        return normalized
    if name == "Glob" and inp.get("path") and not inp.get("pattern"):
        # ``ls`` lists a directory; surface the path as the glob pattern.
        return {"pattern": inp["path"]}
    return inp


def _pi_result_text(content: Any) -> str:
    """Flatten a Pi message ``content`` value into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            p.get("text", "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    if content is None:
        return ""
    return str(content)


def _parse_pi_format(entries: list[dict]) -> list[dict[str, Any]]:
    """Parse Pi JSONL session entries into turn dicts.

    Pi stores one JSON object per line, linked by ``id`` / ``parentId``. We walk
    them in file order. A ``message`` entry with ``message.role == "user"``
    starts a new turn; ``assistant`` messages contribute text / thinking /
    tool_use blocks; ``toolResult`` messages attach their output to the matching
    ``toolCall`` by ``toolCallId``. Non-message entries (session header,
    model_change, thinking_level_change, label, compaction, custom) are ignored.
    """
    turns: list[dict[str, Any]] = []
    turn_index = 0
    current_user_text = ""
    current_timestamp = ""
    current_blocks: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    in_turn = False

    def _finalize() -> None:
        nonlocal turn_index, current_user_text, current_timestamp
        nonlocal current_blocks, pending, in_turn
        if in_turn and (current_user_text or current_blocks):
            turn_index += 1
            turns.append(
                _make_turn(
                    turn_index, current_user_text, current_blocks, current_timestamp
                )
            )
        current_user_text = ""
        current_timestamp = ""
        current_blocks = []
        pending = {}
        in_turn = False

    for entry in entries:
        if entry.get("type") != "message":
            continue
        msg = entry.get("message") or {}
        role = msg.get("role")
        ts: str | None = entry.get("timestamp")
        content = msg.get("content")

        if role == "user":
            _finalize()
            in_turn = True
            current_timestamp = ts or ""
            text_parts = [
                (b.get("text") or "").strip()
                for b in (content if isinstance(content, list) else [])
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            current_user_text = _clean_system_tags(
                "\n".join(t for t in text_parts if t)
            )
            continue

        if role == "assistant":
            if not in_turn:
                in_turn = True
                current_timestamp = ts or ""
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")

                if btype == "text":
                    text = (block.get("text") or "").strip()
                    if text:
                        current_blocks.append(_make_block("text", text=text, timestamp=ts))

                elif btype == "thinking":
                    text = (block.get("thinking") or "").strip()
                    if text:
                        current_blocks.append(
                            _make_block("thinking", text=text, timestamp=ts)
                        )

                elif btype == "toolCall":
                    raw_name = block.get("name") or "unknown"
                    name = _PI_TOOL_MAP.get(
                        raw_name, raw_name[:1].upper() + raw_name[1:]
                    )
                    inp = _normalize_pi_tool_input(name, block.get("arguments") or {})
                    tc = _make_tool_call(
                        tool_use_id=block.get("id") or "",
                        name=name,
                        inp=inp,
                    )
                    current_blocks.append(
                        _make_block("tool_use", tool_call=tc, timestamp=ts)
                    )
                    if tc["tool_use_id"]:
                        pending[tc["tool_use_id"]] = tc
            continue

        if role == "toolResult":
            tid = msg.get("toolCallId") or ""
            tc = pending.get(tid)
            if tc is not None:
                tc["result"] = _pi_result_text(content).strip()
                tc["result_timestamp"] = ts
                tc["is_error"] = bool(msg.get("isError"))
                del pending[tid]
            continue

    _finalize()

    for j, t in enumerate(turns):
        t["index"] = j + 1
    return turns


# ---------------------------------------------------------------------------
# GitHub Copilot CLI parser
# ---------------------------------------------------------------------------

# Envelope event types emitted by the Copilot CLI agent into ``events.jsonl``.
# Used for format detection when a file does not start with ``session.start``
# (e.g. a hand-trimmed excerpt).
_COPILOT_EVENT_TYPES = {
    "session.start",
    "session.shutdown",
    "session.model_change",
    "user.message",
    "assistant.message",
    "assistant.turn_start",
    "assistant.turn_end",
    "tool.execution_start",
    "tool.execution_complete",
}

_COPILOT_TOOL_MAP = {
    "bash": "Bash",
    "shell": "Bash",
    "view": "Read",
    "read": "Read",
    "create": "Write",
    "write": "Write",
    "edit": "Edit",
    "str_replace": "Edit",
    "grep": "Grep",
    "search": "Grep",
    "glob": "Glob",
    "find": "Glob",
    "ls": "Glob",
    "fetch": "WebFetch",
}


def _normalize_copilot_tool_input(name: str, inp: dict[str, Any]) -> dict[str, Any]:
    """Map Copilot CLI tool arguments to the Claude-Code-style keys the player expects.

    Copilot's built-ins use ``path`` (view/create/edit) and ``command`` (bash).
    ``bash`` already matches Claude's ``command`` / ``description`` keys, so it
    passes through untouched.
    """
    if not isinstance(inp, dict):
        return {"raw": inp}
    if name == "Read" and inp.get("path"):
        return {"file_path": inp["path"]}
    if name == "Write" and inp.get("path"):
        return {"file_path": inp["path"], "content": inp.get("content") or ""}
    if name == "Edit" and inp.get("path"):
        normalized = {"file_path": inp["path"]}
        if inp.get("oldStr") is not None:
            normalized["old_string"] = inp["oldStr"]
        if inp.get("newStr") is not None:
            normalized["new_string"] = inp["newStr"]
        return normalized
    if name == "Glob" and inp.get("path") and not inp.get("pattern"):
        return {"pattern": inp["path"]}
    return inp


def _copilot_tool_name(raw_name: str) -> str:
    """Map a Copilot tool name to its Claude-Code-style equivalent.

    Unmapped names are converted from ``snake_case`` to ``TitleCase`` so a tool
    like ``ask_user`` renders as ``AskUser`` rather than ``Ask_user``.
    """
    if not raw_name:
        return "unknown"
    mapped = _COPILOT_TOOL_MAP.get(raw_name)
    if mapped:
        return mapped
    return "".join(part[:1].upper() + part[1:] for part in raw_name.split("_") if part)


def _copilot_result_text(data: dict[str, Any]) -> str:
    """Flatten a Copilot ``tool.execution_complete`` payload into plain text.

    Successful calls carry a ``result`` with both a short ``content`` and a
    longer ``detailedContent``; prefer whichever is richer so the player shows
    the full tool output. Failed calls carry no ``result`` at all — only an
    ``error`` object — so fall back to its message.
    """
    result = data.get("result")
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content") or ""
        detailed = result.get("detailedContent") or ""
        pick = detailed if len(detailed) > len(content) else content
        if pick:
            return pick if isinstance(pick, str) else str(pick)
    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message") or ""
        code = error.get("code")
        if message and code:
            return f"{message} ({code})"
        return str(message or code or "")
    if isinstance(error, str):
        return error
    if result is None:
        return ""
    return str(result)


def _parse_copilot_format(events: list[dict]) -> list[dict[str, Any]]:
    """Parse GitHub Copilot CLI ``events.jsonl`` entries into turn dicts.

    The Copilot CLI writes one event per line, each wrapped in a
    ``{type, data, id, timestamp, parentId}`` envelope. Events are walked in
    file order:

    * ``user.message`` starts a new turn (``data.content`` is the raw prompt;
      ``data.transformedContent`` is the same text with system reminders
      injected, so it is ignored).
    * ``assistant.message`` contributes a thinking block (``data.reasoningText``),
      a text block (``data.content``) and one ``tool_use`` block per entry in
      ``data.toolRequests``. Long responses are split across several events via
      ``chunkIndex`` / ``chunkCount``, but each event carries its own complete
      text, so they simply append in order.
    * ``tool.execution_complete`` attaches output to the matching call by
      ``toolCallId``.

    Session bookkeeping events (``session.*``, ``assistant.turn_*``,
    ``permission.*``, and the ``system.message`` harness prompt) are ignored.
    """
    turns: list[dict[str, Any]] = []
    turn_index = 0
    current_user_text = ""
    current_timestamp = ""
    current_blocks: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    in_turn = False

    def _finalize() -> None:
        nonlocal turn_index, current_user_text, current_timestamp
        nonlocal current_blocks, pending, in_turn
        if in_turn and (current_user_text or current_blocks):
            turn_index += 1
            turns.append(
                _make_turn(
                    turn_index, current_user_text, current_blocks, current_timestamp
                )
            )
        current_user_text = ""
        current_timestamp = ""
        current_blocks = []
        pending = {}
        in_turn = False

    def _add_tool_call(
        call_id: str, raw_name: str, args: Any, ts: str | None
    ) -> None:
        name = _copilot_tool_name(raw_name)
        inp = _normalize_copilot_tool_input(name, args or {})
        tc = _make_tool_call(tool_use_id=call_id, name=name, inp=inp)
        current_blocks.append(_make_block("tool_use", tool_call=tc, timestamp=ts))
        if call_id:
            pending[call_id] = tc

    for event in events:
        etype = event.get("type")
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        ts: str | None = event.get("timestamp")

        if etype == "user.message":
            _finalize()
            in_turn = True
            current_timestamp = ts or ""
            current_user_text = _clean_system_tags((data.get("content") or "").strip())
            continue

        if etype == "assistant.message":
            if not in_turn:
                in_turn = True
                current_timestamp = ts or ""

            reasoning = (data.get("reasoningText") or "").strip()
            if reasoning:
                current_blocks.append(
                    _make_block("thinking", text=reasoning, timestamp=ts)
                )

            text = (data.get("content") or "").strip()
            if text:
                current_blocks.append(_make_block("text", text=text, timestamp=ts))

            for req in data.get("toolRequests") or []:
                if not isinstance(req, dict):
                    continue
                _add_tool_call(
                    req.get("toolCallId") or "",
                    req.get("name") or "unknown",
                    req.get("arguments"),
                    ts,
                )
            continue

        if etype == "tool.execution_start":
            # The call is normally already declared by the preceding
            # ``assistant.message``; only synthesise a block when it is not,
            # so a trimmed transcript still renders its tool calls.
            call_id = data.get("toolCallId") or ""
            if call_id and call_id in pending:
                continue
            if not in_turn:
                in_turn = True
                current_timestamp = ts or ""
            _add_tool_call(
                call_id, data.get("toolName") or "unknown", data.get("arguments"), ts
            )
            continue

        if etype == "tool.execution_complete":
            tc = pending.get(data.get("toolCallId") or "")
            if tc is not None:
                tc["result"] = _copilot_result_text(data).strip()
                tc["result_timestamp"] = ts
                tc["is_error"] = data.get("success") is False
            continue

    _finalize()

    for j, t in enumerate(turns):
        t["index"] = j + 1
    return turns


# ---------------------------------------------------------------------------
# VS Code Copilot Chat parser
# ---------------------------------------------------------------------------

# Response part kinds that carry no replayable content (progress spinners, undo
# markers, editor bookkeeping). Everything else is rendered.
_VSCODE_SKIP_PART_KINDS = {
    "mcpServersStarting",
    "undoStop",
    "progressMessage",
    "progressTaskSerialized",
    "codeblockUri",
    "textEditGroup",
    "notebookEditGroup",
    "elicitationSerialized",
    "questionCarousel",
    "autoModeResolution",
    "confirmation",
    "prepareToolInvocation",
}

_VSCODE_TOOL_MAP = {
    "run_in_terminal": "Bash",
    "Run in Terminal": "Bash",
    "copilot_readFile": "Read",
    "copilot_createFile": "Write",
    "copilot_createDirectory": "Write",
    "copilot_replaceString": "Edit",
    "copilot_applyPatch": "Edit",
    "copilot_insertEdit": "Edit",
    "copilot_findTextInFiles": "Grep",
    "copilot_searchCodebase": "Grep",
    "copilot_findFiles": "Glob",
    "copilot_listDirectory": "Glob",
    "copilot_fetchWebPage": "WebFetch",
    "manage_todo_list": "TodoWrite",
    "execution_subagent": "Task",
}


def _vscode_tool_name(tool_id: str) -> str:
    """Map a VS Code Copilot Chat ``toolId`` to a Claude-Code-style tool name."""
    if not tool_id:
        return "unknown"
    mapped = _VSCODE_TOOL_MAP.get(tool_id)
    if mapped:
        return mapped
    # MCP tools keep their descriptive id (``mcp_server_tool``); strip only the
    # ``copilot_`` vendor prefix and TitleCase the rest.
    name = tool_id[len("copilot_"):] if tool_id.startswith("copilot_") else tool_id
    return name[:1].upper() + name[1:]


def _vscode_md_text(value: Any) -> str:
    """Pull plain text out of a VS Code markdown-ish value.

    Parts store either a bare string or a ``{"value": "..."}`` wrapper.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        inner = value.get("value")
        if isinstance(inner, str):
            return inner
    return ""


def _vscode_apply_journal(lines: list[dict]) -> dict[str, Any]:
    """Replay a VS Code chat journal into the final session state.

    ``chatSessions/<id>.jsonl`` is not a transcript but a journal: ``kind 0`` is
    a full snapshot, ``kind 1`` sets the value at key-path ``k``, and ``kind 2``
    appends to the list at ``k``. Later lines carry content missing from the
    snapshot (a streamed response is appended via ``["requests", 0, "response"]``),
    so the journal has to be replayed rather than read line-by-line.
    """
    state: dict[str, Any] = {}
    for obj in lines:
        kind = obj.get("kind")
        value = obj.get("v")
        path = obj.get("k")

        if kind == 0:
            state = value if isinstance(value, dict) else {}
            continue
        if not isinstance(path, list) or not path:
            continue

        cursor: Any = state
        ok = True
        for key in path[:-1]:
            try:
                cursor = cursor[key]
            except (KeyError, IndexError, TypeError):
                ok = False
                break
        if not ok:
            continue

        last = path[-1]
        try:
            if kind == 2 and isinstance(value, list):
                existing = cursor[last]
                if isinstance(existing, list):
                    cursor[last] = existing + value
                else:
                    cursor[last] = value
            else:
                cursor[last] = value
        except (KeyError, IndexError, TypeError):
            continue

    return state


def _vscode_tool_input(part: dict[str, Any]) -> dict[str, Any]:
    """Recover a tool call's arguments from a serialized invocation."""
    specific = part.get("toolSpecificData")
    if isinstance(specific, dict):
        skind = specific.get("kind")
        if skind == "terminal":
            command_line = specific.get("commandLine")
            command = ""
            if isinstance(command_line, dict):
                command = command_line.get("original") or ""
            elif isinstance(command_line, str):
                command = command_line
            inp: dict[str, Any] = {"command": command}
            if specific.get("cwd"):
                inp["description"] = f"in {specific['cwd']}"
            return inp
        if skind == "input" and isinstance(specific.get("rawInput"), dict):
            return specific["rawInput"]
        if skind == "subagent":
            return {
                k: v
                for k, v in (
                    ("description", specific.get("description")),
                    ("prompt", specific.get("prompt")),
                    ("subagent_type", specific.get("agentName")),
                )
                if v
            }
        if skind == "todoList":
            return {"todos": specific.get("todoList")}

    # Fall back to the JSON-encoded input recorded alongside the result.
    details = part.get("resultDetails")
    if isinstance(details, dict) and details.get("input") is not None:
        raw = details["input"]
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                decoded = json.loads(raw)
                if isinstance(decoded, dict):
                    return decoded
            except (json.JSONDecodeError, ValueError):
                pass

    # Last resort: the human-readable invocation line ("Reading [](file:///…)").
    message = _vscode_md_text(part.get("invocationMessage"))
    return {"description": message} if message else {}


def _vscode_tool_result(part: dict[str, Any]) -> tuple[str | None, bool]:
    """Recover a tool call's output and error flag from a serialized invocation."""
    details = part.get("resultDetails")
    is_error = False
    if isinstance(details, dict):
        is_error = bool(details.get("isError"))
        output = details.get("output")
        if isinstance(output, str):
            return output, is_error
        if isinstance(output, list):
            chunks = [
                str(o.get("value", ""))
                for o in output
                if isinstance(o, dict) and o.get("value") is not None
            ]
            if chunks:
                return "\n".join(chunks), is_error

    # No structured result: fall back to the past-tense summary VS Code shows
    # once a call completes ("Read [](file:///…)").
    past = _vscode_md_text(part.get("pastTenseMessage"))
    if past:
        return past, is_error
    return (None, is_error) if not part.get("isComplete") else ("", is_error)


def _parse_vscode_copilot_format(lines: list[dict]) -> list[dict[str, Any]]:
    """Parse a VS Code Copilot Chat ``chatSessions/<id>.jsonl`` journal into turns.

    The journal is replayed into a final state (see
    :func:`_vscode_apply_journal`), then each entry of ``requests`` becomes one
    turn: ``message.text`` is the prompt and ``response`` is a list of parts —
    bare markdown (a part with no ``kind``), ``thinking``, ``inlineReference``
    and ``toolInvocationSerialized``. Consecutive text parts are merged, since
    VS Code splits a single streamed answer across many of them.
    """
    state = _vscode_apply_journal(lines)
    requests = state.get("requests")
    if not isinstance(requests, list):
        return []

    turns: list[dict[str, Any]] = []

    for request in requests:
        if not isinstance(request, dict):
            continue

        message = request.get("message")
        user_text = ""
        if isinstance(message, dict):
            user_text = _clean_system_tags((message.get("text") or "").strip())

        timestamp = _ms_to_iso(request.get("timestamp")) or ""
        response_ts = _ms_to_iso(request.get("responseTimestamp")) or timestamp

        blocks: list[dict[str, Any]] = []
        text_buffer: list[str] = []

        def _flush_text() -> None:
            joined = "".join(text_buffer).strip()
            text_buffer.clear()
            if joined:
                blocks.append(_make_block("text", text=joined, timestamp=response_ts))

        for part in request.get("response") or []:
            if not isinstance(part, dict):
                continue
            kind = part.get("kind")

            if kind is None:
                text_buffer.append(_vscode_md_text(part.get("value")))
                continue

            if kind == "inlineReference":
                # An inline file mention sitting inside the surrounding prose.
                ref = part.get("inlineReference")
                path = ""
                if isinstance(ref, dict):
                    path = ref.get("fsPath") or ref.get("path") or ""
                elif isinstance(ref, str):
                    path = ref
                if path:
                    text_buffer.append(f"`{Path(path).name}`")
                continue

            if kind == "thinking":
                _flush_text()
                thought = _vscode_md_text(part.get("value")).strip()
                if thought:
                    blocks.append(
                        _make_block("thinking", text=thought, timestamp=response_ts)
                    )
                continue

            if kind == "toolInvocationSerialized":
                _flush_text()
                name = _vscode_tool_name(part.get("toolId") or "")
                result, is_error = _vscode_tool_result(part)
                tc = _make_tool_call(
                    tool_use_id=part.get("toolCallId") or "",
                    name=name,
                    inp=_vscode_tool_input(part),
                    result=result,
                    result_timestamp=response_ts,
                    is_error=is_error,
                )
                blocks.append(
                    _make_block("tool_use", tool_call=tc, timestamp=response_ts)
                )
                continue

            if kind in _VSCODE_SKIP_PART_KINDS:
                continue

        _flush_text()

        if user_text or blocks:
            turns.append(_make_turn(len(turns) + 1, user_text, blocks, timestamp))

    return turns


# ---------------------------------------------------------------------------
# Replay format parser
# ---------------------------------------------------------------------------


def _parse_replay_format(lines: list[dict]) -> list[dict[str, Any]]:
    """
    Pass-through parser for replay JSONL (output of ``agent-replay extract``).
    Each line is already a turn object with ``index``, ``user_text``, ``blocks``,
    ``timestamp`` fields.
    """
    turns: list[dict[str, Any]] = []
    for obj in lines:
        if obj.get("user_text") is None and obj.get("blocks") is None:
            continue

        raw_blocks = obj.get("blocks") or []
        blocks: list[dict[str, Any]] = []
        for rb in raw_blocks:
            tc_raw = rb.get("tool_call")
            tool_call: dict[str, Any] | None = None
            if tc_raw:
                tool_call = _make_tool_call(
                    tool_use_id=tc_raw.get("tool_use_id") or "",
                    name=tc_raw.get("name") or "",
                    inp=tc_raw.get("input") or {},
                    result=tc_raw.get("result"),
                    result_timestamp=(
                        tc_raw.get("result_timestamp") or tc_raw.get("resultTimestamp")
                    ),
                    is_error=bool(tc_raw.get("is_error")),
                )
            blocks.append(
                _make_block(
                    kind=rb.get("kind") or "text",
                    text=rb.get("text") or "",
                    tool_call=tool_call,
                    timestamp=rb.get("timestamp"),
                )
            )

        turns.append(
            _make_turn(
                index=obj.get("index") or len(turns) + 1,
                user_text=obj.get("user_text") or "",
                blocks=blocks,
                timestamp=obj.get("timestamp") or "",
                system_events=list(obj.get("system_events") or []),
                bookmark=obj.get("bookmark"),
            )
        )
    return turns


# ---------------------------------------------------------------------------
# Filtering & paced timing
# ---------------------------------------------------------------------------


def filter_turns(
    turns: list[dict[str, Any]],
    *,
    turn_range: tuple[int, int] | None = None,
    exclude_turns: list[int] | None = None,
    time_from: str | None = None,
    time_to: str | None = None,
) -> list[dict[str, Any]]:
    """
    Filter *turns* by index range, exclusion list, and/or timestamp window.

    Parameters
    ----------
    turns:
        List of turn dicts as produced by :func:`parse_session`.
    turn_range:
        ``(start, end)`` inclusive index range.
    exclude_turns:
        List of turn indices to exclude.
    time_from:
        ISO-8601 timestamp; exclude turns before this time.
    time_to:
        ISO-8601 timestamp; exclude turns after this time.
    """
    result = turns

    if turn_range is not None:
        start, end = turn_range
        result = [t for t in result if start <= t["index"] <= end]

    if exclude_turns:
        excluded = set(exclude_turns)
        result = [t for t in result if t["index"] not in excluded]

    if time_from is not None:
        try:
            dt_from = datetime.fromisoformat(time_from.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Invalid time_from date: {time_from!r}") from exc
        result = [
            t for t in result
            if t.get("timestamp")
            and datetime.fromisoformat(
                t["timestamp"].replace("Z", "+00:00")
            ) >= dt_from
        ]

    if time_to is not None:
        try:
            dt_to = datetime.fromisoformat(time_to.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Invalid time_to date: {time_to!r}") from exc
        result = [
            t for t in result
            if t.get("timestamp")
            and datetime.fromisoformat(
                t["timestamp"].replace("Z", "+00:00")
            ) <= dt_to
        ]

    return result


def apply_paced_timing(turns: list[dict[str, Any]]) -> None:
    """
    Replace timestamps on *turns* with synthetic pacing driven by content length.

    Mutates turns in-place.  Timing parameters mirror the JavaScript original:

    * 500 ms pause before the assistant responds to each turn
    * 30 ms per character of block text, clamped to [1 000, 10 000] ms
    """
    cursor_ms = 0  # milliseconds from Unix epoch (same as JS new Date(0))

    def _ms_to_iso(ms: int) -> str:
        dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"

    for turn in turns:
        turn["timestamp"] = _ms_to_iso(cursor_ms)
        cursor_ms += 500  # brief pause before assistant responds
        for block in turn.get("blocks") or []:
            block["timestamp"] = _ms_to_iso(cursor_ms)
            length = len(block.get("text") or "")
            cursor_ms += min(max(length * 30, 1000), 10000)
            if block.get("tool_call"):
                block["tool_call"]["result_timestamp"] = _ms_to_iso(cursor_ms)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def parse_session(
    file_path: Path | str,
    paced_timing: bool = False,
) -> list[dict[str, Any]]:
    """
    Parse a JSONL transcript file and return a list of turn dicts.

    The format (Claude Code, Cursor, Codex, OpenCode, Pi, Copilot CLI, VS Code
    Copilot Chat, or Replay) is detected automatically.  When *paced_timing* is ``True`` the timestamps are replaced
    with synthetic timing based on content length.

    Parameters
    ----------
    file_path:
        Path to the ``.jsonl`` session file.
    paced_timing:
        Synthesise timestamps from content length instead of using wall-clock
        times embedded in the file.

    Returns
    -------
    list[dict]
        Each element is a turn dict (see module docstring for shape).
    """
    text = Path(file_path).read_text(encoding="utf-8")
    fmt = _detect_format_from_text(text)

    # OpenCode export is a single JSON object, parsed separately from the
    # line-by-line JSONL formats.
    if fmt == "opencode":
        try:
            export = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            export = {}
        turns = _parse_opencode_format(export if isinstance(export, dict) else {})
        if paced_timing:
            apply_paced_timing(turns)
        return turns

    # Parse all JSON lines up-front
    parsed_lines: list[dict] = []
    for line in text.split("\n"):
        trimmed = line.strip()
        if not trimmed:
            continue
        try:
            parsed_lines.append(json.loads(trimmed))
        except json.JSONDecodeError:
            continue

    match fmt:
        case "codex":
            turns = _parse_codex_format(parsed_lines)
        case "pi":
            turns = _parse_pi_format(parsed_lines)
        case "copilot":
            turns = _parse_copilot_format(parsed_lines)
        case "copilot-chat":
            turns = _parse_vscode_copilot_format(parsed_lines)
        case "replay":
            turns = _parse_replay_format(parsed_lines)
        case "cursor":
            turns = _parse_cursor_format(parsed_lines)
        case "claude":
            turns = _parse_claude_format(parsed_lines)
        case _:
            turns = []

    # For claude / cursor: drop empty turns and re-index
    if fmt in ("claude", "cursor"):
        turns = [
            t
            for t in turns
            if t["user_text"]
            or t.get("system_events")
            or any(
                b["kind"] == "tool_use"
                or (b["kind"] == "text" and b.get("text") and b["text"] != "No response requested.")
                or (b["kind"] == "thinking" and b.get("text"))
                for b in t["blocks"]
            )
        ]
        for j, t in enumerate(turns):
            t["index"] = j + 1

    if paced_timing:
        apply_paced_timing(turns)

    return turns
