"""SDK-side PII redaction — runs before any payload leaves the customer's
environment, per the policy decided in Phase 1 §2 (see backend/CLAUDE.md,
"PII / payload-capture policy"). Two layers:

1. Field-level: state keys the customer has explicitly marked sensitive are
   fully replaced, regardless of type or content.
2. Regex fallback: common PII patterns (email, phone, card-like digit runs)
   are scrubbed out of any remaining string values, structured or free-text.

Field-level rules always take precedence — a key already redacted in layer
1 is not also regex-scanned.
"""

import re
from typing import Any

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?\(?\d{3,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}(?!\d)")
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")

_PATTERNS = (
    (_EMAIL_RE, "[REDACTED:EMAIL]"),
    (_CARD_RE, "[REDACTED:CARD_OR_ID]"),
    (_PHONE_RE, "[REDACTED:PHONE]"),
)


def scrub_text(value: str) -> str:
    """Apply the regex-fallback PII patterns to a single string. Exposed
    publicly so the ingest API's backstop redaction pass (Phase 1 §5) can
    reuse the exact same patterns instead of duplicating them."""
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _scrub_string(value: str) -> str:
    return scrub_text(value)


def redact_value(value: Any, sensitive_keys: frozenset[str]) -> Any:
    """Redact a single value. Container types recurse; sensitive_keys is
    only consulted by redact_state for dict keys, not here."""
    if isinstance(value, str):
        return _scrub_string(value)
    if isinstance(value, dict):
        return redact_state(value, sensitive_keys)
    if isinstance(value, list):
        return [redact_value(v, sensitive_keys) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_value(v, sensitive_keys) for v in value)
    return value


def redact_state(state: dict, sensitive_keys: frozenset[str]) -> dict:
    """Redact a LangGraph node's input or output state dict.

    Field-level: any key in `sensitive_keys` (case-insensitive) is fully
    replaced with a placeholder, whatever its value.
    Regex fallback: every remaining string value (including inside nested
    dicts/lists) is scanned for common PII patterns.
    """
    redacted: dict = {}
    for key, value in state.items():
        if key.lower() in sensitive_keys:
            redacted[key] = "[REDACTED:FIELD]"
        else:
            redacted[key] = redact_value(value, sensitive_keys)
    return redacted
