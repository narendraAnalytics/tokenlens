"""HTTP span exporter — POSTs spans to the TokenLens ingest API
(`POST /v1/traces`) in the wire format `ingest/schemas.py` expects.

Swaps in for the default ConsoleSpanExporter (see tracer.py) once
`TokenLens(..., ingest_url=...)` is set; nothing else in the SDK changes —
handler.py and client.py are exporter-agnostic by design.
"""

from datetime import datetime, timezone
from typing import Sequence

import httpx
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

# tokenlens.* attributes that map to bare BigQuery column names (structural
# / identity fields) rather than keeping the tokenlens_ prefix. See
# ingest/schemas.py for why this asymmetry exists — it mirrors the schema
# decided in Phase 1 §3.
_STRUCTURAL_ATTRS = {
    "tokenlens.run_id": "run_id",
    "tokenlens.graph_name": "graph_name",
    "tokenlens.node_name": "node_name",
    "tokenlens.status": "status",
    "tokenlens.latency_ms": "latency_ms",
}


def span_to_wire_dict(span: ReadableSpan) -> dict:
    attrs = dict(span.attributes or {})
    wire: dict = {}

    for key, value in attrs.items():
        if key in _STRUCTURAL_ATTRS:
            wire[_STRUCTURAL_ATTRS[key]] = value
        elif key == "gen_ai.tool.name":
            wire["gen_ai_tool_name"] = list(value) if value else []
        elif key.startswith("gen_ai.") or key.startswith("tokenlens."):
            wire[key.replace(".", "_")] = value

    wire["span_id"] = format(span.context.span_id, "016x")
    wire["parent_span_id"] = (
        format(span.parent.span_id, "016x") if span.parent else None
    )
    wire["timestamp"] = datetime.fromtimestamp(
        span.start_time / 1e9, tz=timezone.utc
    ).isoformat()
    wire.setdefault("gen_ai_tool_name", [])
    return wire


class HTTPSpanExporter(SpanExporter):
    def __init__(self, ingest_url: str, timeout: float = 5.0) -> None:
        self._url = ingest_url
        self._client = httpx.Client(timeout=timeout)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        payload = [span_to_wire_dict(s) for s in spans]
        try:
            response = self._client.post(self._url, json=payload)
            response.raise_for_status()
            return SpanExportResult.SUCCESS
        except httpx.HTTPError as exc:
            print(f"tokenlens_sdk: failed to export spans to {self._url}: {exc}")
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        self._client.close()
