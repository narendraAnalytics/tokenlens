"""Handles a decoded Slack button click (phase.txt Phase 2 §5): writes the
audit_log row, raises the budget cap on Approve-and-raise-cap, and resumes
the interrupted graph from its checkpoint. Called from webhook.py's
background task — never on the request/ack path (Slack's 3s window).

Known Phase 2 scope limitation: resuming requires reconstructing the exact
graph that was running (same nodes/edges) so `_budget_gate`'s wrapper is
back in place for the rest of the run — LangGraph's checkpointer restores
*state*, not the Python graph object itself. TokenLens's backend can only
do this for graphs it has the build function for, which for a real
production customer it never would (their graph lives in their own
process/infra, not here). `_GRAPH_BUILDERS` below is therefore a small,
explicit registry seeded only with this phase's demo graph — not a general
solution. A real implementation would have the *customer's own process*
perform the resume (e.g. polling this table for a decision), which is
deferred to a later phase; this local demo intentionally does the resume
from the backend process instead, per phase.txt Phase 2 §0's research note
("this is the mechanism the Slack webhook handler triggers on Approve").
"""

import json
from typing import Any, Callable

import sqlalchemy as sa
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from loguru import logger

from config import settings
from db import get_engine
from tokenlens_sdk import budget
from tokenlens_sdk.client import TokenLens

_AUDIT_LOG_INSERT = sa.text(
    """
    INSERT INTO audit_log
        (tenant_id, thread_id, graph_name, decision, spend_at_decision_usd,
         new_cap_usd, decided_by)
    VALUES
        (:tenant_id, :thread_id, :graph_name, :decision, :spend_at_decision_usd,
         :new_cap_usd, :decided_by)
    """
)


def _build_budget_breach_probe_graph() -> Any:
    # Imported lazily, not at module load, so slack_notify doesn't force-
    # import examples/ (a demo package) on every backend startup — only
    # when an actual resume for this graph_name is requested.
    from examples.budget_breach_probe import build_graph

    return build_graph()


_GRAPH_BUILDERS: dict[str, Callable[[], Any]] = {
    "budget_breach_probe": _build_budget_breach_probe_graph,
}


def _write_audit_log(
    *,
    tenant_id: str,
    thread_id: str,
    graph_name: str,
    decision: str,
    spend_at_decision_usd: float,
    new_cap_usd: float | None,
    decided_by: str,
) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            _AUDIT_LOG_INSERT,
            {
                "tenant_id": tenant_id,
                "thread_id": thread_id,
                "graph_name": graph_name,
                "decision": decision,
                "spend_at_decision_usd": spend_at_decision_usd,
                "new_cap_usd": new_cap_usd,
                "decided_by": decided_by,
            },
        )


def _resume_graph(*, graph_name: str, tenant_id: str, thread_id: str) -> None:
    build_fn = _GRAPH_BUILDERS.get(graph_name)
    if build_fn is None:
        logger.error(
            f"slack_notify.resume: no registered graph builder for "
            f"graph_name={graph_name!r} — cannot resume thread_id={thread_id!r}. "
            "This is a Phase 2 demo-scope limitation, not a bug: see this "
            "module's docstring."
        )
        return

    # PostgresSaver wants a plain libpq conn string, not SQLAlchemy's
    # "postgresql+psycopg://" dialect prefix (same conversion as
    # examples/budget_breach_probe.py).
    conn_string = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    with PostgresSaver.from_conn_string(conn_string) as checkpointer:
        raw_graph = build_fn().compile(checkpointer=checkpointer)
        # Re-instrument, not a bare .invoke() on raw_graph: resuming through
        # the uninstrumented graph would silently drop telemetry AND the
        # budget gate for the rest of the run — the wrapper only exists on
        # an instrumented graph object, and LangGraph's checkpointer
        # restores state, not this Python wrapping.
        instrumented = TokenLens(graph_name=graph_name, tenant_id=tenant_id).instrument(
            raw_graph
        )
        instrumented.invoke(
            Command(resume={"decision": "approved"}),
            config={"configurable": {"thread_id": thread_id}},
        )


def handle_decision(action_id: str, value_json: str, decided_by: str) -> None:
    """Entry point for webhook.py's background task. Never lets an
    exception escape — this runs after Slack has already been ack'd, so
    there's no request to fail; errors are logged instead."""
    try:
        ctx = json.loads(value_json)
        tenant_id = ctx["tenant_id"]
        thread_id = ctx["thread_id"]
        graph_name = ctx["graph_name"]
        spend = float(ctx["spend_so_far_usd"])
        cap = float(ctx["cap_usd"])

        if action_id == "kill_run":
            _write_audit_log(
                tenant_id=tenant_id,
                thread_id=thread_id,
                graph_name=graph_name,
                decision="kill",
                spend_at_decision_usd=spend,
                new_cap_usd=None,
                decided_by=decided_by,
            )
            return  # Leave interrupted — no resume, per phase.txt §5.

        new_cap_usd = None
        if action_id == "approve_raise_cap":
            # No UI for a custom amount (the card's confirm dialog doesn't
            # collect one — keeping the card to 3 buttons, no modal, per
            # Phase 2's demo scope). Simple, documented heuristic: raise to
            # double the actual spend so far, not double the old cap —
            # spend can already be well past the old cap by the time a
            # human clicks (see the probe's own breach: spend was ~3x
            # cap), so doubling the cap alone might not even clear current
            # spend. Floor at $0.0001, the smallest value the
            # Numeric(10,4) per_run_cap_usd column can represent.
            new_cap_usd = max(round(spend * 2, 4), 0.0001)
            budget.raise_active_cap(tenant_id, graph_name, new_cap_usd)
        elif action_id != "approve_run":
            logger.warning(f"slack_notify.resume: unknown action_id {action_id!r}, ignoring")
            return

        decision = "approve" if action_id == "approve_run" else "approve_and_raise_cap"
        _write_audit_log(
            tenant_id=tenant_id,
            thread_id=thread_id,
            graph_name=graph_name,
            decision=decision,
            spend_at_decision_usd=spend,
            new_cap_usd=new_cap_usd,
            decided_by=decided_by,
        )
        _resume_graph(graph_name=graph_name, tenant_id=tenant_id, thread_id=thread_id)
    except Exception:
        logger.exception(
            f"slack_notify.resume: failed to handle decision action_id={action_id!r}"
        )
