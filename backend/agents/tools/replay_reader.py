"""Trace/timeline reconstruction and failure explanation tools for the
Replay Agent (phase3.txt Phase 3B §3). Reads BigQuery spans only --
LangGraph checkpoint state (`read_checkpoint_state`) is a documented
stretch goal, not built here: it needs a separate empirical safety check
(a short-lived PostgresSaver read alongside main.py's long-lived one) the
same way agents/base.py's session design itself was verified before being
built on (examples/checkpointer_lifespan_probe.py) -- not assumed safe.

Status values ("completed"/"failed") come straight from
tokenlens_sdk/handler.py's `_finish_span` (confirmed by reading that
source, not guessed) -- not OTel's own "OK"/"ERROR" span-status strings,
which are a separate, un-queried field.
"""

from agents.tools.bigquery_reader import query_spans

_FAILED_STATUS = "failed"


def reconstruct_timeline(*, run_id: str) -> list[dict]:
    """Every span for a run, oldest first (bigquery_reader.query_spans
    defaults to DESC for its own "most recent first" use case; this
    re-sorts ascending since a timeline reads start-to-end)."""
    spans = query_spans(run_id=run_id, limit=500)
    return sorted(spans, key=lambda s: s["timestamp"])


def explain_failure(*, run_id: str) -> dict:
    """The failing span(s) in a run, each paired with the span that ran
    immediately before it for context (what happened right before the
    failure)."""
    timeline = reconstruct_timeline(run_id=run_id)
    failures = []
    for i, span in enumerate(timeline):
        if span.get("status") == _FAILED_STATUS:
            preceding = timeline[i - 1] if i > 0 else None
            failures.append({"failed_span": span, "preceding_span": preceding})
    return {"run_id": run_id, "failure_count": len(failures), "failures": failures}
