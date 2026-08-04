"""Per-run budget cap lookup against the Cloud SQL control store's
budget_policies table (Phase 2 §1).

Looked up fresh on every graph invocation, not cached/polled on the 60s
cadence used for payload_capture_mode (backend/CLAUDE.md) — a stale spend
cap is a security-relevant gap (an approved cap raise must take effect on
the very next resume, not up to 60s later), so a per-invocation read is
the deliberate choice here, not an oversight.
"""

import logging

import sqlalchemy as sa

from db import get_engine

logger = logging.getLogger(__name__)

_ACTIVE_CAP_QUERY = sa.text(
    """
    SELECT per_run_cap_usd FROM budget_policies
    WHERE tenant_id = :tenant_id AND graph_name = :graph_name AND active
    LIMIT 1
    """
)

_RAISE_CAP_UPDATE = sa.text(
    """
    UPDATE budget_policies
    SET per_run_cap_usd = :new_cap_usd, updated_at = now()
    WHERE tenant_id = :tenant_id AND graph_name = :graph_name AND active
    """
)


def get_active_per_run_cap(tenant_id: str, graph_name: str) -> float | None:
    """Returns the active per-run cap in USD for this tenant+graph, or None
    if no policy row exists (no cap enforced — Phase 2's default for a
    graph nobody has set a budget on) or the control store can't be
    reached right now (fails open with a logged warning rather than
    blocking every run on Cloud SQL being healthy — a tradeoff worth
    revisiting before this gates real production spend, not a silent
    default)."""
    try:
        with get_engine().connect() as conn:
            row = conn.execute(
                _ACTIVE_CAP_QUERY,
                {"tenant_id": tenant_id, "graph_name": graph_name},
            ).first()
    except sa.exc.SQLAlchemyError:
        logger.warning(
            "tokenlens_sdk.budget: could not reach the control store for "
            "tenant=%s graph=%s — proceeding with no budget cap enforced "
            "this run",
            tenant_id,
            graph_name,
            exc_info=True,
        )
        return None
    return float(row[0]) if row is not None else None


def raise_active_cap(tenant_id: str, graph_name: str, new_cap_usd: float) -> None:
    """Updates the active policy row's per_run_cap_usd (Phase 2 §5's
    Approve-and-raise-cap decision). Takes effect on the very next resume
    since the cap is looked up fresh per invocation, not on the 60s poll
    cadence (see get_active_per_run_cap's docstring). Unlike that lookup,
    this does NOT fail open on a control-store error — an "Approve and
    raise cap" click that silently failed to raise the cap would leave the
    subsequent resume immediately re-blocked with no visible reason, which
    is worse than a loud failure the caller (webhook handler) can log."""
    with get_engine().begin() as conn:
        conn.execute(
            _RAISE_CAP_UPDATE,
            {"tenant_id": tenant_id, "graph_name": graph_name, "new_cap_usd": new_cap_usd},
        )
