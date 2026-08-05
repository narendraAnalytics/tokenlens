"""Wire format for POST /v1/traces.

This is the contract between tokenlens_sdk's exporter (which builds one of
these per OTel span) and the ingest API. Field names deliberately match the
BigQuery `spans` table columns 1:1 (see backend/scripts/setup_bigquery.py)
so the worker (Phase 1 §5, Pub/Sub -> BigQuery) can insert with no
translation step beyond adding `ingested_at` and `tokenlens_cost` — the two
fields that are computed server-side, never sent by the client.

Note the naming asymmetry, inherited from the schema decided in §3: most
tokenlens_sdk `tokenlens.*` attributes map to bare column names here
(run_id, graph_name, node_name, status, latency_ms) since they're
structural/identity fields, not custom business metrics — only
tenant_id/cached_tokens/retry_count/confidence/payload_capture_mode/
payload_redacted keep the `tokenlens_` prefix. See
tokenlens_sdk/exporter.py for the attribute-to-wire-format mapping.
"""

from pydantic import BaseModel, Field


class SpanIn(BaseModel):
    run_id: str
    span_id: str
    parent_span_id: str | None = None
    graph_name: str
    node_name: str
    timestamp: str  # ISO 8601
    status: str
    latency_ms: float

    gen_ai_operation_name: str | None = None
    gen_ai_system: str | None = None
    gen_ai_request_model: str | None = None
    gen_ai_response_model: str | None = None
    gen_ai_usage_input_tokens: int = 0
    gen_ai_usage_output_tokens: int = 0
    gen_ai_agent_name: str | None = None
    gen_ai_agent_type: str | None = None
    gen_ai_tool_name: list[str] = Field(default_factory=list)

    tokenlens_cached_tokens: int = 0
    tokenlens_retry_count: int = 0
    tokenlens_tenant_id: str
    tokenlens_confidence: float | None = None
    tokenlens_payload_capture_mode: str
    # JSON-encoded string (already SDK-redacted). None when capture mode is
    # metadata_only. The backstop redaction pass in routes.py parses this
    # back into a structure and re-serializes it (see _backstop_redact) —
    # regex-scrubbing the raw JSON text directly is unsafe, since a numeric
    # literal (e.g. a many-digit latency_ms float) can spuriously match the
    # card-number pattern and corrupt the JSON syntax.
    tokenlens_payload_redacted: str | None = None


class IngestResponse(BaseModel):
    accepted: int
