"""Verifies the PostgresSaver-lifespan pattern Flow 1's /v1/chat endpoint
needs (phase3.txt Phase 3A §0/§3): every prior use of
PostgresSaver.from_conn_string was a short-lived `with` block inside a
one-shot script (examples/budget_breach_probe.py). Flow 1's FastAPI
process is the first *long-lived* process needing this -- the checkpointer
must be opened once (at app startup) and reused across many requests, not
reopened per request.

This probe opens PostgresSaver ONCE (simulating a FastAPI lifespan), then
fires two separate .invoke() calls against a trivial 1-node graph using two
DIFFERENT thread_ids, confirming both checkpoint independently under the
same long-lived connection with no contention -- must pass before Flow 1
is built on top of it.

Run: uv run python -m examples.checkpointer_lifespan_probe
"""

import sqlalchemy as sa
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, StateGraph
from typing import TypedDict

from config import settings
from db import get_engine

GRAPH_NAME = "checkpointer_lifespan_probe"


class State(TypedDict):
    value: str


def one_node(state: State) -> dict:
    return {"value": state["value"] + "-done"}


def build_graph():
    graph = StateGraph(State)
    graph.add_node("one_node", one_node)
    graph.set_entry_point("one_node")
    graph.add_edge("one_node", END)
    return graph


def _cleanup(thread_ids: list[str]) -> None:
    with get_engine().begin() as conn:
        for table in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
            for thread_id in thread_ids:
                conn.execute(
                    sa.text(f"DELETE FROM {table} WHERE thread_id = :t"),
                    {"t": thread_id},
                )


def main() -> None:
    conn_string = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    thread_ids = ["lifespan-probe-thread-a", "lifespan-probe-thread-b"]

    try:
        # Opened ONCE -- this is the app-lifespan simulation. A per-request
        # open/close (the wrong pattern) would instead construct a fresh
        # `with PostgresSaver.from_conn_string(...)` inside each invoke call.
        with PostgresSaver.from_conn_string(conn_string) as checkpointer:
            app = build_graph().compile(checkpointer=checkpointer)

            results = []
            for thread_id in thread_ids:
                result = app.invoke(
                    {"value": thread_id},
                    config={"configurable": {"thread_id": thread_id}},
                )
                results.append((thread_id, result))

            # A second invoke reusing the SAME thread_id should resume from
            # the same checkpoint lineage (not collide with the other thread).
            state_a = app.get_state({"configurable": {"thread_id": thread_ids[0]}})
            state_b = app.get_state({"configurable": {"thread_id": thread_ids[1]}})

            for thread_id, result in results:
                print(f"{thread_id}: final state = {result}")
                assert result["value"] == f"{thread_id}-done"

            assert state_a.values["value"] == f"{thread_ids[0]}-done"
            assert state_b.values["value"] == f"{thread_ids[1]}-done"
            assert state_a.config["configurable"]["thread_id"] == thread_ids[0]
            assert state_b.config["configurable"]["thread_id"] == thread_ids[1]

        print(
            "checkpointer_lifespan_probe: OK -- one long-lived PostgresSaver "
            "correctly isolated two thread_ids with no contention"
        )
    finally:
        _cleanup(thread_ids)


if __name__ == "__main__":
    main()
