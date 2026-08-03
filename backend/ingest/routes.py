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

from fastapi import APIRouter

from ingest.pubsub_client import publish_span
from ingest.schemas import IngestResponse, SpanIn
from tokenlens_sdk.redaction import scrub_text

router = APIRouter()


def _backstop_redact(span: SpanIn) -> SpanIn:
    if span.tokenlens_payload_redacted:
        span.tokenlens_payload_redacted = scrub_text(span.tokenlens_payload_redacted)
    return span


@router.post("/v1/traces", response_model=IngestResponse, status_code=202)
def ingest_traces(spans: list[SpanIn]) -> IngestResponse:
    for span in spans:
        span = _backstop_redact(span)
        publish_span(span.model_dump())
    return IngestResponse(accepted=len(spans))
