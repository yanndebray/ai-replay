"""
Parse Claude Code, Cursor, and Codex CLI JSONL transcripts into structured turns.

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


def _detect_format_from_text(text: str) -> str:
    """
    Detect transcript format by peeking at the first parseable JSON line.

    Returns one of: ``"claude"``, ``"cursor"``, ``"codex"``, ``"replay"``,
    or ``"unknown"``.
    """
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
        if obj.get("type") in ("user", "assistant"):
            return "claude"
        if obj.get("role") in ("user", "assistant"):
            return "cursor"
    return "unknown"


def detect_format(file_path: Path | str) -> str:
    """Detect the session format of a JSONL file.

    Returns one of: ``"claude"``, ``"cursor"``, ``"codex"``, ``"replay"``,
    or ``"unknown"``.
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

    The format (Claude Code, Cursor, Codex, or Replay) is detected
    automatically.  When *paced_timing* is ``True`` the timestamps are replaced
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
