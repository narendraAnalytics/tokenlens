"""Shared BigQuery reader tool (phase3.txt Phase 3A §3) — the one thing
Spend, Replay, and Insights (Phase 3B) will all need: querying
tokenlens_traces.spans by run_id / tenant_id / time range. Built once here,
shared, not reimplemented per agent.

Unlike scripts/setup_bigquery.py (a one-shot script that constructs a
fresh bigquery.Client per run), this is a long-lived, reusable module — so
the client is cached via the same lru_cache(maxsize=1) singleton idiom
used by db.get_engine() and agents/gateway.py's _get_client().
"""

import functools

from google.cloud import bigquery

from config import settings

_TABLE = "tokenlens_traces.spans"


class ToolError(Exception):
    """Raised on any BigQuery query failure. Deliberately fail-loud, not
    fail-open (return []): an agent silently getting an empty result back
    for "BigQuery is unreachable" would misreport that as "no spend/no
    activity for this run," which is a worse failure mode than a visible
    tool error the base-agent loop (agents/base.py) can fold back into the
    model's next turn."""


@functools.lru_cache(maxsize=1)
def _get_client() -> bigquery.Client:
    return bigquery.Client(project=settings.gcp_project_id)


def get_client() -> bigquery.Client:
    """Public accessor for the cached client singleton -- used by
    agents/tools/spend_detectors.py and pricing_reader.py so they don't
    each open a second bigquery.Client per process."""
    return _get_client()


def query_spans(
    *,
    run_id: str | None = None,
    tenant_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Query spans by run_id and/or tenant_id, optionally bounded by a
    timestamp range. Requires at least one of run_id/tenant_id — guards
    against an accidental full-table scan across every tenant's traces.

    start_time/end_time are ISO-8601 strings (e.g. "2026-08-01T00:00:00Z"),
    filtered against the `timestamp` column that the spans table is already
    day-partitioned on (scripts/setup_bigquery.py) — bounding the range
    keeps queries cheap even as the table grows.
    """
    if not run_id and not tenant_id:
        raise ValueError("query_spans requires at least one of run_id or tenant_id")

    conditions: list[str] = []
    params: list[bigquery.ScalarQueryParameter] = [
        bigquery.ScalarQueryParameter("limit", "INT64", limit)
    ]

    if run_id:
        conditions.append("run_id = @run_id")
        params.append(bigquery.ScalarQueryParameter("run_id", "STRING", run_id))
    if tenant_id:
        conditions.append("tokenlens_tenant_id = @tenant_id")
        params.append(bigquery.ScalarQueryParameter("tenant_id", "STRING", tenant_id))
    if start_time:
        conditions.append("timestamp >= @start_time")
        params.append(bigquery.ScalarQueryParameter("start_time", "TIMESTAMP", start_time))
    if end_time:
        conditions.append("timestamp <= @end_time")
        params.append(bigquery.ScalarQueryParameter("end_time", "TIMESTAMP", end_time))

    query = (
        f"SELECT * FROM `{settings.gcp_project_id}.{_TABLE}` "
        f"WHERE {' AND '.join(conditions)} "
        "ORDER BY timestamp DESC LIMIT @limit"
    )

    try:
        job = _get_client().query(
            query, job_config=bigquery.QueryJobConfig(query_parameters=params)
        )
        rows = [dict(row) for row in job.result()]
    except Exception as exc:  # noqa: BLE001 -- normalize every failure to ToolError
        raise ToolError(f"query_spans failed: {exc}") from exc

    return rows
