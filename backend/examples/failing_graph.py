"""Tiny probe graph with a node that deliberately raises, so a real
"failed" span lands in tokenlens_traces.spans for the Replay Agent's
failure-explanation test (phase3.txt Phase 3B §3) -- toy_graph.py and
Flow 1 both only ever succeed, so neither has a real failure to test
against.

Run: uv run python -m examples.failing_graph
Then drain: uv run python scripts/bigquery_worker.py --once
"""

import uuid
from typing import TypedDict

from langgraph.graph import END, StateGraph

from tokenlens_sdk import TokenLens


class State(TypedDict):
    step: str


def start(state: State) -> dict:
    return {"step": "started"}


def explode(state: State) -> dict:
    raise RuntimeError("deliberate failure for Replay Agent testing")


def build_graph():
    graph = StateGraph(State)
    graph.add_node("start", start)
    graph.add_node("explode", explode)
    graph.set_entry_point("start")
    graph.add_edge("start", "explode")
    graph.add_edge("explode", END)
    return graph.compile()


def main() -> str:
    app = build_graph()

    tl = TokenLens(
        graph_name="failing_probe",
        tenant_id="demo-tenant",
        ingest_url="http://localhost:8000/v1/traces",
    )
    app = tl.instrument(app)

    run_id = uuid.uuid4().hex
    try:
        result = app.invoke(
            {"step": "init"}, config={"configurable": {"thread_id": run_id}}
        )
        print("UNEXPECTED SUCCESS:", result)
    except RuntimeError as exc:
        print(f"got expected failure: {exc}")

    print(f"run_id={run_id}")
    return run_id


if __name__ == "__main__":
    main()
