"""Durable per-thread spend ledger for budget gating (Phase 2 §2).

The in-process gating counter on TokenLensCallbackHandler
(cumulative_cost_usd) is fast but resets to zero on every fresh
.invoke()/.stream() call — including every resume after an interrupt,
since Command(resume=...) is itself a new invocation with a brand-new
handler. Verified by direct testing that this let a resumed run bypass
budget enforcement entirely regardless of the actual approval decision.

This module is the fix: one row per thread_id (= run_id) in Cloud SQL,
incremented synchronously whenever a real LLM call actually happens (never
per node — most nodes make no paid call at all), and read by the node
wrapper's pre-check in client.py instead of the in-process counter. The
write only ever happens alongside a real LLM call's own latency, so the
added overhead is proportionally small; unlike tokenlens_sdk/budget.py's
policy-cap lookup, failures here are NOT swallowed — a ledger write/read
that silently failed would defeat the entire point of this table, so an
unreachable control store surfaces as a real exception rather than a
quiet fail-open.
"""

import sqlalchemy as sa

from db import get_engine

_UPSERT = sa.text(
    """
    INSERT INTO run_spend (thread_id, cost_usd)
    VALUES (:thread_id, :delta_usd)
    ON CONFLICT (thread_id) DO UPDATE
    SET cost_usd = run_spend.cost_usd + EXCLUDED.cost_usd,
        updated_at = now()
    """
)

_SELECT = sa.text("SELECT cost_usd FROM run_spend WHERE thread_id = :thread_id")


def record_spend(thread_id: str, delta_usd: float) -> None:
    """Adds delta_usd to thread_id's running total. A no-op for a zero
    delta (e.g. a node with no usage_metadata) to avoid a pointless write."""
    if delta_usd == 0:
        return
    with get_engine().begin() as conn:
        conn.execute(_UPSERT, {"thread_id": thread_id, "delta_usd": delta_usd})


def get_cumulative_spend(thread_id: str) -> float:
    """Total USD spent so far on this thread_id, across every invoke()/
    resume that has ever touched it. 0.0 for a thread_id with no recorded
    spend yet — not an error, just a run that hasn't made a paid call."""
    with get_engine().connect() as conn:
        row = conn.execute(_SELECT, {"thread_id": thread_id}).first()
    return float(row[0]) if row is not None else 0.0
