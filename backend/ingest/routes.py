"""POST /v1/traces — validates incoming spans, applies backstop PII
redaction, and publishes each to Pub/Sub.

Redaction here is deliberately a backstop, not the primary mechanism (see
backend/CLAUDE.md "PII / payload-capture policy", decided Phase 1 §2):
primary redaction already happened in the SDK before the span left the
customer's environment. This pass only re-applies the regex-fallback layer
over the already-redacted payload — it can't re-apply the tenant's
field-level sensitive-key rules, since this service doesn't have access to
tenant config yet (no Cloud SQL control store until Phase 2). That's a
known, acceptable gap for Phase 1: it catches a missing/misconfigured SDK
regex rule, not a missing field-level rule.
"""

import json
from typing import Any

from fastapi import APIRouter

from ingest.pubsub_client import publish_span
from ingest.schemas import IngestResponse, SpanIn
from tokenlens_sdk.redaction import scrub_text

router = APIRouter()


def _scrub_json_strings(value: Any) -> Any:
    """Recurse into a parsed JSON structure, applying the regex-fallback
    redaction only to string leaves — never to the raw JSON text as a
    whole. Regex-scrubbing an entire serialized blob directly is unsafe:
    a numeric literal (e.g. a many-significant-digit latency_ms float) can
    spuriously match the card-number pattern and corrupt the JSON syntax,
    confirmed by direct testing against Phase 3A's chat endpoint."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {k: _scrub_json_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_json_strings(v) for v in value]
    return value


def _backstop_redact(span: SpanIn) -> SpanIn:
    if span.tokenlens_payload_redacted:
        try:
            parsed = json.loads(span.tokenlens_payload_redacted)
        except json.JSONDecodeError:
            # Not valid JSON for some other reason -- fall back to the old
            # whole-string scrub rather than dropping the payload.
            span.tokenlens_payload_redacted = scrub_text(span.tokenlens_payload_redacted)
        else:
            span.tokenlens_payload_redacted = json.dumps(_scrub_json_strings(parsed))
    return span


@router.post("/v1/traces", response_model=IngestResponse, status_code=202)
def ingest_traces(spans: list[SpanIn]) -> IngestResponse:
    for span in spans:
        span = _backstop_redact(span)
        publish_span(span.model_dump())
    return IngestResponse(accepted=len(spans))
