"""Local test for the Runtime Context Agent (phase.txt Phase 2 §3, checkbox
2): trips a real budget breach against a toy graph and prints the 3-line
summary the agent produces, so a human can eyeball accuracy and timing.

Not a modification of examples/toy_graph.py — keeps the Phase 1 reference
graph untouched. Reusable later by Phase 2 §6's fuller end-to-end demo
once the Slack card (§4) and webhook (§5) exist.

Requires: cloud-sql-proxy running on 127.0.0.1:5432 (tokenlens-control-dev
must be RUNNABLE, not stopped) — this test needs a real interrupt, which
needs the real budget_policies/run_spend tables and Postgres checkpointer.

Run: uv run python -m examples.budget_breach_probe
"""

import time
from typing import TypedDict

import sqlalchemy as sa
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, StateGraph

from config import settings
from db import get_engine
from tokenlens_sdk import TokenLens

TENANT_ID = "probe-tenant"
GRAPH_NAME = "budget_breach_probe"
# per_run_cap_usd is Numeric(10,4) in Postgres -- $0.0001 is the smallest
# representable cap, so the "expensive" node below is sized to comfortably
# clear it (~$0.0003), not to match it exactly.
PER_RUN_CAP_USD = 0.0001


class State(TypedDict):
    step: str


def cheap_node(state: State) -> dict:
    return {"step": "cheap_node done"}


def expensive_node(state: State) -> dict:
    # Sized well above PER_RUN_CAP_USD at gemini-2.5-flash's rates
    # ($0.000075/1K in, $0.0003/1K out — tokenlens_sdk/pricing.py):
    # 200 in + 1000 out ~= $0.000315.
    model = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="expensive response",
                    usage_metadata={
                        "input_tokens": 200,
                        "output_tokens": 1000,
                        "total_tokens": 1200,
                    },
                    response_metadata={"model_name": "gemini-2.5-flash"},
                )
            ]
        )
    )
    model.invoke("do something expensive")
    return {"step": "expensive_node done"}


def third_node(state: State) -> dict:
    # Never reached in this probe -- expensive_node's spend already trips
    # this node's own pre-check, so interrupt() fires before this body runs.
    return {"step": "third_node done"}


def build_graph():
    graph = StateGraph(State)
    graph.add_node("cheap_node", cheap_node)
    graph.add_node("expensive_node", expensive_node)
    graph.add_node("third_node", third_node)
    graph.set_entry_point("cheap_node")
    graph.add_edge("cheap_node", "expensive_node")
    graph.add_edge("expensive_node", "third_node")
    graph.add_edge("third_node", END)
    return graph


def _set_budget_policy() -> None:
    with get_engine().begin() as conn:
        conn.execute(
            sa.text(
                """
                INSERT INTO budget_policies (tenant_id, graph_name, per_run_cap_usd, active)
                VALUES (:tenant_id, :graph_name, :cap, true)
                """
            ),
            {"tenant_id": TENANT_ID, "graph_name": GRAPH_NAME, "cap": PER_RUN_CAP_USD},
        )


def _cleanup(thread_id: str | None) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            sa.text(
                "DELETE FROM budget_policies WHERE tenant_id = :t AND graph_name = :g"
            ),
            {"t": TENANT_ID, "g": GRAPH_NAME},
        )
        if thread_id:
            conn.execute(
                sa.text("DELETE FROM run_spend WHERE thread_id = :t"), {"t": thread_id}
            )
    with get_engine().begin() as conn:
        if thread_id:
            for table in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
                conn.execute(
                    sa.text(f"DELETE FROM {table} WHERE thread_id = :t"),
                    {"t": thread_id},
                )


def main() -> None:
    _set_budget_policy()

    # PostgresSaver wants a plain libpq conn string, not SQLAlchemy's
    # "postgresql+psycopg://" dialect prefix.
    conn_string = settings.database_url.replace("postgresql+psycopg://", "postgresql://")

    thread_id = "budget-breach-probe-thread"
    try:
        with PostgresSaver.from_conn_string(conn_string) as checkpointer:
            app = build_graph().compile(checkpointer=checkpointer)

            tl = TokenLens(graph_name=GRAPH_NAME, tenant_id=TENANT_ID)
            instrumented = tl.instrument(app)

            t0 = time.perf_counter()
            result = instrumented.invoke(
                {"step": "start"},
                config={"configurable": {"thread_id": thread_id}},
            )
            elapsed_s = time.perf_counter() - t0

            interrupts = result.get("__interrupt__", ())
            if not interrupts:
                print("FAIL: expected a budget-breach interrupt, got none.")
                print("Final state:", result)
                return

            payload = interrupts[0].value
            summary = payload["summary"]

            print(f"\nInterrupted at node: {payload['node']}")
            print(f"Spend so far: ${payload['spend_so_far_usd']:.6f} / cap ${payload['cap_usd']}")
            print(f"Time to breach+summarize: {elapsed_s:.2f}s\n")
            print("--- Runtime Context Agent summary ---")
            print(summary)
            print("--------------------------------------")

            line_count = len(summary.splitlines())
            assert summary.strip(), "summary must be non-empty"
            assert line_count <= 4, f"expected ~3 lines, got {line_count}"
            print(f"\nOK: non-empty, {line_count} line(s), {elapsed_s:.2f}s elapsed.")
    finally:
        _cleanup(thread_id)


if __name__ == "__main__":
    main()
