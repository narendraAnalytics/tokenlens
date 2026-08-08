"""The Overview doc's 7 deterministic SQL cost/reliability detectors,
ported as callable tools for the Spend Agent (phase3.txt Phase 3B §2),
folded in here rather than staying a separate "Analyst Detectors" phase
per phase3.txt's Scope Decision §2.

Deterministic first: these functions run raw SQL against
`tokenlens_traces.spans` and return the exact rows that tripped each
detector's threshold. The Spend Agent (agents/spend.py) is instructed to
call these and only rank/phrase the results, never invent numbers -- the
same principle the original Overview doc establishes for the Analyst
Detectors.

Reuses agents/tools/bigquery_reader.py's cached client singleton (don't
create a second bigquery.Client per process) and its ToolError, so
agents/base.py's existing exception handling (which already catches
BigQueryToolError) covers these without changes.

Two detectors are documented approximations, not exact per the Overview
doc's literal wording, because the spans schema (scripts/setup_bigquery.py)
doesn't carry the fields that would make them exact:
- redundant tool call: grouped by tool NAME only (gen_ai_tool_name is a
  REPEATED STRING column with no paired argument-hash column), not
  "identical name+argument hash" -- a real per-call argument hash would
  need a new ingest-side field, not something to add speculatively here.
- over-retrieval: no span is tagged "retrieval" vs. "generation" in the
  schema, so this approximates via input/output token ratio + an absolute
  floor rather than joining retrieved-context tokens to what a downstream
  node actually referenced.
- orphaned workflow: no separate "business event" table exists yet, so
  "no downstream business event" is approximated as "no span activity at
  all for this run_id" past the staleness window -- a recency proxy, not
  a true CRM/business-event join.

Thresholds are keyword-argument defaults, not hardcoded constants, so a
caller (or the Spend Agent itself, via its tool parameters) can override
per-tenant sensitivity without a code change.
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


def detect_runaway_loop(*, run_id: str, threshold: int = 10) -> list[dict]:
    """Same node_name executing more than `threshold` times in one run.
    Default 10: toy_graph.py and Flow 1 are 2-3 node graphs, so >10
    repeats of one node in a single run is well outside any legitimate
    retry/loop pattern seen so far -- tune down once real customer graphs
    with intentional bounded loops are observed."""
    query = f"""
        SELECT run_id, node_name, COUNT(*) AS exec_count
        FROM `{_TABLE}`
        WHERE run_id = @run_id
        GROUP BY run_id, node_name
        HAVING COUNT(*) > @threshold
        ORDER BY exec_count DESC
    """
    params = [
        bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
        bigquery.ScalarQueryParameter("threshold", "INT64", threshold),
    ]
    return _run(query, params, label="detect_runaway_loop")


def detect_redundant_tool_call(*, run_id: str) -> list[dict]:
    """The same tool name invoked 2+ times within one run -- any repeat is
    redundant by definition, no threshold to tune. Grouped by tool name
    only (see module docstring's "documented approximations" note)."""
    query = f"""
        SELECT run_id, tool_name, COUNT(*) AS call_count
        FROM `{_TABLE}`, UNNEST(gen_ai_tool_name) AS tool_name
        WHERE run_id = @run_id
        GROUP BY run_id, tool_name
        HAVING COUNT(*) >= 2
        ORDER BY call_count DESC
    """
    params = [bigquery.ScalarQueryParameter("run_id", "STRING", run_id)]
    return _run(query, params, label="detect_redundant_tool_call")


def detect_over_retrieval(*, run_id: str, ratio_threshold: float = 5.0) -> list[dict]:
    """A span whose input tokens exceed `ratio_threshold`x its own output
    tokens AND exceed an absolute 3000-token floor -- a conservative
    "clearly wasteful" bar given no retrieval-vs-generation tag exists in
    the schema to join against (see module docstring)."""
    query = f"""
        SELECT run_id, node_name,
               gen_ai_usage_input_tokens AS input_tokens,
               gen_ai_usage_output_tokens AS output_tokens
        FROM `{_TABLE}`
        WHERE run_id = @run_id
          AND gen_ai_usage_input_tokens IS NOT NULL
          AND gen_ai_usage_output_tokens IS NOT NULL
          AND gen_ai_usage_output_tokens > 0
          AND gen_ai_usage_input_tokens > 3000
          AND gen_ai_usage_input_tokens > gen_ai_usage_output_tokens * @ratio_threshold
        ORDER BY gen_ai_usage_input_tokens DESC
    """
    params = [
        bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
        bigquery.ScalarQueryParameter("ratio_threshold", "FLOAT64", ratio_threshold),
    ]
    return _run(query, params, label="detect_over_retrieval")


def detect_model_over_specification(
    *,
    run_id: str | None = None,
    tenant_id: str | None = None,
    output_token_ceiling: int = 200,
) -> list[dict]:
    """A frontier ("pro") model used on a node whose output is under
    `output_token_ceiling` tokens with no tool call -- matches the
    Overview doc's definition verbatim. Requires run_id or tenant_id, same
    guard as bigquery_reader.query_spans."""
    if not run_id and not tenant_id:
        raise ValueError(
            "detect_model_over_specification requires at least one of run_id or tenant_id"
        )

    conditions = [
        "LOWER(gen_ai_request_model) LIKE '%pro%'",
        "gen_ai_usage_output_tokens IS NOT NULL",
        "gen_ai_usage_output_tokens < @output_token_ceiling",
        "ARRAY_LENGTH(gen_ai_tool_name) = 0",
    ]
    params = [
        bigquery.ScalarQueryParameter("output_token_ceiling", "INT64", output_token_ceiling)
    ]
    if run_id:
        conditions.append("run_id = @run_id")
        params.append(bigquery.ScalarQueryParameter("run_id", "STRING", run_id))
    if tenant_id:
        conditions.append("tokenlens_tenant_id = @tenant_id")
        params.append(bigquery.ScalarQueryParameter("tenant_id", "STRING", tenant_id))

    query = f"""
        SELECT run_id, node_name, gen_ai_request_model, gen_ai_usage_output_tokens
        FROM `{_TABLE}`
        WHERE {' AND '.join(conditions)}
        ORDER BY timestamp DESC
    """
    return _run(query, params, label="detect_model_over_specification")


def detect_orphaned_workflow(
    *,
    tenant_id: str,
    graph_name: str | None = None,
    stale_after_days: int = 14,
) -> list[dict]:
    """A run whose most recent span is older than `stale_after_days` --
    the Overview doc's own explicit 14-day default. Recency proxy for "no
    downstream business event" (see module docstring's caveat)."""
    conditions = ["tokenlens_tenant_id = @tenant_id"]
    params = [
        bigquery.ScalarQueryParameter("tenant_id", "STRING", tenant_id),
        bigquery.ScalarQueryParameter("stale_after_days", "INT64", stale_after_days),
    ]
    if graph_name:
        conditions.append("graph_name = @graph_name")
        params.append(bigquery.ScalarQueryParameter("graph_name", "STRING", graph_name))

    query = f"""
        SELECT run_id, graph_name, MAX(timestamp) AS last_span_at
        FROM `{_TABLE}`
        WHERE {' AND '.join(conditions)}
        GROUP BY run_id, graph_name
        HAVING TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(timestamp), DAY) > @stale_after_days
        ORDER BY last_span_at ASC
    """
    return _run(query, params, label="detect_orphaned_workflow")


def detect_retry_storm(
    *,
    tenant_id: str,
    graph_name: str | None = None,
    p95_threshold: int = 3,
) -> list[dict]:
    """Node-level p95 retry count above `p95_threshold` -- the Overview
    doc's own explicit default."""
    conditions = ["tokenlens_tenant_id = @tenant_id"]
    params = [
        bigquery.ScalarQueryParameter("tenant_id", "STRING", tenant_id),
        bigquery.ScalarQueryParameter("p95_threshold", "INT64", p95_threshold),
    ]
    if graph_name:
        conditions.append("graph_name = @graph_name")
        params.append(bigquery.ScalarQueryParameter("graph_name", "STRING", graph_name))

    query = f"""
        SELECT node_name,
               APPROX_QUANTILES(tokenlens_retry_count, 100)[OFFSET(95)] AS p95_retry_count
        FROM `{_TABLE}`
        WHERE {' AND '.join(conditions)}
        GROUP BY node_name
        HAVING p95_retry_count > @p95_threshold
        ORDER BY p95_retry_count DESC
    """
    return _run(query, params, label="detect_retry_storm")


def detect_tenant_skew(
    *, window_days: int = 7, skew_ratio_threshold: float = 0.5
) -> list[dict]:
    """A single tenant accounting for more than `skew_ratio_threshold`
    (default 50%) of total spend across all tenants in the trailing
    `window_days`. Does not yet filter to flat-fee tenants specifically --
    budget_policies has no plan-type column today, so this compares every
    tenant against the global total (documented limitation, see module
    docstring)."""
    query = f"""
        WITH tenant_costs AS (
            SELECT tokenlens_tenant_id, SUM(tokenlens_cost) AS total_cost
            FROM `{_TABLE}`
            WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @window_days DAY)
            GROUP BY tokenlens_tenant_id
        ),
        grand_total AS (
            SELECT SUM(total_cost) AS grand_total FROM tenant_costs
        )
        SELECT tokenlens_tenant_id, total_cost, total_cost / grand_total.grand_total AS share
        FROM tenant_costs, grand_total
        WHERE grand_total.grand_total > 0
          AND total_cost / grand_total.grand_total > @skew_ratio_threshold
        ORDER BY share DESC
    """
    params = [
        bigquery.ScalarQueryParameter("window_days", "INT64", window_days),
        bigquery.ScalarQueryParameter("skew_ratio_threshold", "FLOAT64", skew_ratio_threshold),
    ]
    return _run(query, params, label="detect_tenant_skew")
