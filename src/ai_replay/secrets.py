"""
Secret detection and redaction for replay output.

Ported from secrets.mjs.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

REDACTED = "[REDACTED]"

# Each entry is (name, compiled_regex). Order matters: more specific patterns first.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Private keys (multi-line, checked first)
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    # AWS access key IDs
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    # Anthropic API keys
    ("sk_ant_key", re.compile(r"sk-ant-[a-zA-Z0-9-]{20,}")),
    # Generic sk- / key- prefixed secrets
    ("sk_key", re.compile(r"sk-[a-zA-Z0-9]{20,}")),
    ("key_prefix", re.compile(r"key-[a-zA-Z0-9]{20,}")),
    # Bearer tokens
    ("bearer", re.compile(r"Bearer [A-Za-z0-9_.~+/=-]{20,}")),
    # JWT tokens
    (
        "jwt",
        re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"),
    ),
    # Connection strings
    (
        "connection_string",
        re.compile(r"(?:mongodb|postgres|mysql|redis|amqp|mssql)://[^\s\"']+"),
    ),
    # Generic key=value secrets
    (
        "key_value",
        re.compile(
            r'(?:api[_-]?key|api[_-]?secret|secret[_-]?key|access[_-]?key|auth[_-]?token|bearer)\s*[:=]\s*["\']?[^\s"\',]{8,}["\']?',
            re.IGNORECASE,
        ),
    ),
    # Env var patterns (PASSWORD=..., TOKEN=..., etc.)
    (
        "env_var",
        re.compile(r"(?:PASSWORD|TOKEN|SECRET|CREDENTIAL|PRIVATE_KEY)=[^\s]+"),
    ),
    # Standalone hex tokens (40+ hex chars, word-bounded)
    ("hex_token", re.compile(r"\b[0-9a-fA-F]{40,}\b")),
]


def redact_secrets(text: str) -> str:
    """Replace detected secrets in a string with [REDACTED]."""
    if not isinstance(text, str):
        return text
    result = text
    for _name, pattern in SECRET_PATTERNS:
        result = pattern.sub(REDACTED, result)
    return result


def redact_object(obj: Any) -> Any:
    """Recursively walk an object/array, redacting string values."""
    if isinstance(obj, str):
        return redact_secrets(obj)
    if isinstance(obj, list):
        return [redact_object(v) for v in obj]
    if isinstance(obj, dict):
        return {k: redact_object(v) for k, v in obj.items()}
    return obj


def create_custom_redactor(rules: list[str]) -> Callable[[str], str]:
    """Create a redactor function from custom regex rules.

    Parameters
    ----------
    rules:
        List of regex pattern strings. Each match will be replaced with
        [REDACTED].

    Returns
    -------
    Callable[[str], str]
        A function that applies all rules to a string.
    """
    if not rules:
        return lambda t: t

    compiled = [re.compile(r) for r in rules]

    def _redact(text: str) -> str:
        if not isinstance(text, str):
            return text
        result = text
        for pattern in compiled:
            result = pattern.sub(REDACTED, result)
        return result

    return _redact


def build_redactor(
    rules: list[dict[str, str]] | None,
) -> Callable[[str], str]:
    """Build a text-replacement function from search/replacement rule dicts.

    Parameters
    ----------
    rules:
        List of ``{"search": str, "replacement": str}`` dicts. Each entry
        performs a plain-string ``replace`` (not regex).

    Returns
    -------
    Callable[[str], str]
        A function that applies all rules to a string.
    """
    if not rules:
        return lambda t: t

    def _redact(text: str) -> str:
        if not isinstance(text, str):
            return text
        result = text
        for rule in rules:
            result = result.replace(rule["search"], rule["replacement"])
        return result

    return _redact
