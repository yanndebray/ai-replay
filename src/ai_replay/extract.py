"""
Extract embedded turn/bookmark data from a generated HTML replay file.

Ported from extract.mjs.
"""

from __future__ import annotations

import base64
import json
import re
import zlib
from typing import Any


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _decode_blob(raw: str) -> Any:
    """Decode a data blob — either raw JSON or base64-encoded raw deflate.

    For raw JSON (--no-compress mode), undoes the JS string literal escaping
    applied by ``escapeJsonForScript`` before parsing.
    """
    if raw.startswith(("[", "{", "\\")):
        # Raw JSON (--no-compress mode) — undo JS string literal escaping.
        # Process char-by-char to correctly handle \\\\ vs \\" vs \\n etc.
        json_chars: list[str] = []
        i = 0
        while i < len(raw):
            if raw[i] == "\\" and i + 1 < len(raw):
                nxt = raw[i + 1]
                if nxt == "\\":
                    json_chars.append("\\")
                    i += 2
                elif nxt == '"':
                    json_chars.append('"')
                    i += 2
                elif nxt == "n":
                    json_chars.append("\n")
                    i += 2
                elif nxt == "r":
                    json_chars.append("\r")
                    i += 2
                else:
                    # Pass through unknown escapes unchanged
                    json_chars.append(raw[i])
                    i += 1
            else:
                json_chars.append(raw[i])
                i += 1

        json_str = "".join(json_chars)
        # Undo HTML-in-script escapes (these don't use backslash)
        json_str = json_str.replace("<\\/", "</").replace("<\\!--", "<!--")
        return json.loads(json_str)

    # Compressed: base64-encoded zlib deflate (matches Node.js deflateSync / zlib.compress())
    compressed = base64.b64decode(raw)
    decompressed = zlib.decompress(compressed)
    return json.loads(decompressed.decode("utf-8"))


def _find_blobs(html: str) -> list[str]:
    """Find all data blobs passed to the async decode function.

    Works with both minified (e.g. ``f=await Tt("...")``) and
    unminified (``const TURNS = await decodeData("...")``) output.
    Handles escaped quotes within the data blob.

    Returns blobs in source order: [turnsBlob, bookmarksBlob].
    """
    blobs: list[str] = []
    pattern = re.compile(r'await\s+[\w$]+\("')

    for m in pattern.finditer(html):
        start = m.end()  # character index just after the opening quote
        i = start
        while i < len(html):
            if html[i] == "\\":
                i += 2  # skip escaped character
                continue
            if html[i] == '"' and i + 1 < len(html) and html[i + 1] == ")":
                blobs.append(html[start:i])
                break
            i += 1

    return blobs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_turns(html_content: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract turns and bookmarks from a generated HTML replay string.

    Parameters
    ----------
    html_content:
        The full text of a generated HTML replay file.

    Returns
    -------
    tuple[list[dict], list[dict]]
        A ``(turns, bookmarks)`` tuple of plain Python dicts.

    Raises
    ------
    ValueError
        If fewer than two data blobs can be found in the HTML.
    """
    blobs = _find_blobs(html_content)

    # The template has exactly two decodeData calls: TURNS first, BOOKMARKS second.
    if len(blobs) < 2:
        raise ValueError(
            "Could not find data blobs in HTML "
            "(expected at least 2 decodeData calls)"
        )

    turns = _decode_blob(blobs[0])
    bookmarks = _decode_blob(blobs[1])
    return turns, bookmarks
