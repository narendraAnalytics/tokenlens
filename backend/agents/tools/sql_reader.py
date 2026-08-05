"""Shared Cloud SQL reader tool (phase3.txt Phase 3A §3) — what the Policy
Agent (Phase 3B) needs: reading budget_policies and audit_log. Built once
here, shared, not reimplemented per agent.

Uses db.get_engine() + raw sa.text(...), exactly like
tokenlens_sdk/budget.py — the one shared, cached SQLAlchemy engine every
control-store reader/writer reuses.
"""

import sqlalchemy as sa

from db import get_engine


class ToolError(Exception):
    """Raised on any control-store query failure. Deliberately fail-loud,
    unlike tokenlens_sdk/budget.py's get_active_per_run_cap() (which fails
    open because "no cap enforced" is an acceptable degraded state for the
    hot gating path) — a governance question like "who approved this run"
    silently returning "no rows" on a DB hiccup is indistinguishable from
    "no one approved it," which is the wrong failure mode for an
    audit-answering tool. The base-agent loop (agents/base.py) can fold a
    visible tool error back into the model's next turn instead."""


_BUDGET_POLICIES_QUERY_ALL = sa.text(
    """
    SELECT id, tenant_id, graph_name, per_run_cap_usd, active, updated_at
    FROM budget_policies
    WHERE tenant_id = :tenant_id
    ORDER BY updated_at DESC
    """
)

_BUDGET_POLICIES_QUERY_BY_GRAPH = sa.text(
    """
    SELECT id, tenant_id, graph_name, per_run_cap_usd, active, updated_at
    FROM budget_policies
    WHERE tenant_id = :tenant_id AND graph_name = :graph_name
    ORDER BY updated_at DESC
    """
)


def query_budget_policies(*, tenant_id: str, graph_name: str | None = None) -> list[dict]:
    """All budget_policies rows (active and historical) for a tenant,
    optionally narrowed to one graph."""
    query = _BUDGET_POLICIES_QUERY_BY_GRAPH if graph_name else _BUDGET_POLICIES_QUERY_ALL
    params: dict = {"tenant_id": tenant_id}
    if graph_name:
        params["graph_name"] = graph_name

    try:
        with get_engine().connect() as conn:
            rows = conn.execute(query, params).mappings().all()
    except sa.exc.SQLAlchemyError as exc:
        raise ToolError(f"query_budget_policies failed: {exc}") from exc

    return [dict(row) for row in rows]


_AUDIT_LOG_QUERY_ALL = sa.text(
    """
    SELECT id, tenant_id, thread_id, graph_name, decision,
           spend_at_decision_usd, new_cap_usd, decided_by, decided_at
    FROM audit_log
    WHERE tenant_id = :tenant_id
    ORDER BY decided_at DESC
    LIMIT :limit
    """
)

_AUDIT_LOG_QUERY_BY_THREAD = sa.text(
    """
    SELECT id, tenant_id, thread_id, graph_name, decision,
           spend_at_decision_usd, new_cap_usd, decided_by, decided_at
    FROM audit_log
    WHERE tenant_id = :tenant_id AND thread_id = :thread_id
    ORDER BY decided_at DESC
    LIMIT :limit
    """
)


def query_audit_log(
    *, tenant_id: str, thread_id: str | None = None, limit: int = 50
) -> list[dict]:
    """Audit-log rows for a tenant, optionally narrowed to one thread_id
    (= run_id, per Phase 2 §0's one-ID convention) — governance questions
    like "who approved this run" / "what was the decision" answer directly
    from this table, never by re-triggering a Slack approval as a side
    effect of answering a question (phase3.txt Phase 3B §4)."""
    query = _AUDIT_LOG_QUERY_BY_THREAD if thread_id else _AUDIT_LOG_QUERY_ALL
    params: dict = {"tenant_id": tenant_id, "limit": limit}
    if thread_id:
        params["thread_id"] = thread_id

    try:
        with get_engine().connect() as conn:
            rows = conn.execute(query, params).mappings().all()
    except sa.exc.SQLAlchemyError as exc:
        raise ToolError(f"query_audit_log failed: {exc}") from exc

    return [dict(row) for row in rows]
