"""Token/cost/latency analysis and provider/model comparison tools for the
Spend Agent (phase3.txt Phase 3B §2) -- reads BigQuery aggregates over
`tokenlens_traces.spans`. Reuses `tokenlens_cost` (the ingest-computed,
official-billing-grade figure -- see backend/CLAUDE.md's budget-gating
policy section for the distinction from the SDK's own gating-only local
estimate) rather than introducing a second pricing mechanism.
"""

from google.cloud import bigquery

from agents.tools.bigquery_reader import ToolError, get_client
from config import settings

_TABLE = f"{settings.gcp_project_id}.tokenlens_traces.spans"


def _run(query: str, params: list[bigquery.ScalarQueryParameter], *, label: str) -> list[dict]:
    try:
        job = get_client().query(
            query, job_config=bigquery.QueryJobConfig(query_parameters=params)
        )
        return [dict(row) for row in job.result()]
    except Exception as exc:  # noqa: BLE001 -- normalize every failure to ToolError
        raise ToolError(f"{label} failed: {exc}") from exc


def summarize_run_cost(*, run_id: str) -> dict:
    """Total cost/tokens/latency for a run, plus a per-node breakdown."""
    totals_query = f"""
        SELECT
            COALESCE(SUM(tokenlens_cost), 0) AS total_cost_usd,
            COALESCE(SUM(gen_ai_usage_input_tokens), 0) AS total_input_tokens,
            COALESCE(SUM(gen_ai_usage_output_tokens), 0) AS total_output_tokens,
            COALESCE(SUM(latency_ms), 0) AS total_latency_ms,
            COUNT(*) AS span_count
        FROM `{_TABLE}`
        WHERE run_id = @run_id
    """
    per_node_query = f"""
        SELECT
            node_name,
            COALESCE(SUM(tokenlens_cost), 0) AS cost_usd,
            COALESCE(SUM(gen_ai_usage_input_tokens), 0) AS input_tokens,
            COALESCE(SUM(gen_ai_usage_output_tokens), 0) AS output_tokens,
            COALESCE(SUM(latency_ms), 0) AS latency_ms,
            ANY_VALUE(gen_ai_request_model) AS model
        FROM `{_TABLE}`
        WHERE run_id = @run_id
        GROUP BY node_name
        ORDER BY cost_usd DESC
    """
    params = [bigquery.ScalarQueryParameter("run_id", "STRING", run_id)]
    totals = _run(totals_query, params, label="summarize_run_cost")
    per_node = _run(per_node_query, params, label="summarize_run_cost")
    return {
        "run_id": run_id,
        "totals": totals[0] if totals else {},
        "per_node": per_node,
    }


def compare_models(*, tenant_id: str, window_days: int = 7) -> list[dict]:
    """Cost/latency/volume by gen_ai_request_model for a tenant over the
    trailing `window_days`."""
    query = f"""
        SELECT
            gen_ai_request_model AS model,
            COUNT(*) AS call_count,
            COALESCE(SUM(tokenlens_cost), 0) AS total_cost_usd,
            COALESCE(AVG(latency_ms), 0) AS avg_latency_ms,
            COALESCE(SUM(gen_ai_usage_input_tokens), 0) AS total_input_tokens,
            COALESCE(SUM(gen_ai_usage_output_tokens), 0) AS total_output_tokens
        FROM `{_TABLE}`
        WHERE tokenlens_tenant_id = @tenant_id
          AND gen_ai_request_model IS NOT NULL
          AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @window_days DAY)
        GROUP BY model
        ORDER BY total_cost_usd DESC
    """
    params = [
        bigquery.ScalarQueryParameter("tenant_id", "STRING", tenant_id),
        bigquery.ScalarQueryParameter("window_days", "INT64", window_days),
    ]
    return _run(query, params, label="compare_models")


def usage_trend(*, tenant_id: str, window_days: int = 30) -> list[dict]:
    """Daily cost/token rollup for a tenant over the trailing
    `window_days`."""
    query = f"""
        SELECT
            DATE(timestamp) AS day,
            COALESCE(SUM(tokenlens_cost), 0) AS cost_usd,
            COALESCE(SUM(gen_ai_usage_input_tokens), 0) AS input_tokens,
            COALESCE(SUM(gen_ai_usage_output_tokens), 0) AS output_tokens,
            COUNT(DISTINCT run_id) AS run_count
        FROM `{_TABLE}`
        WHERE tokenlens_tenant_id = @tenant_id
          AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @window_days DAY)
        GROUP BY day
        ORDER BY day ASC
    """
    params = [
        bigquery.ScalarQueryParameter("tenant_id", "STRING", tenant_id),
        bigquery.ScalarQueryParameter("window_days", "INT64", window_days),
    ]
    return _run(query, params, label="usage_trend")
