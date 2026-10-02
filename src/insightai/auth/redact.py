"""Strip credential-bearing header values before they reach the model."""

from __future__ import annotations

import re
from typing import Any

_PATTERNS = (
    (re.compile(r"(?i)(authorization\s*[:=]\s*)[^\r\n]+"), r"\1[redacted]"),
    (re.compile(r"(?i)(set-cookie\s*[:=]\s*)[^\r\n]+"), r"\1[redacted]"),
    (re.compile(r"(?i)(\bcookie\s*[:=]\s*)[^\r\n]+"), r"\1[redacted]"),
    (re.compile(r"(?i)(x-api-key\s*[:=]\s*)[^\r\n]+"), r"\1[redacted]"),
    (re.compile(r"(?i)(x-auth-token\s*[:=]\s*)[^\r\n]+"), r"\1[redacted]"),
    (
        re.compile(r"(?i)([?&](?:token|access_token|api_key|apikey|secret)=)[^&\s\"']+"),
        r"\1[redacted]",
    ),
    (
        re.compile(
            r"""(?i)("(?:password|passwd|secret|api_?key|access_token|refresh_token)"\s*:\s*")[^"]*"""
        ),
        '"[redacted]"',
    ),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "[redacted]"),
)


def redact_text(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_secrets(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_secrets(item) for key, item in value.items()}
    return value
