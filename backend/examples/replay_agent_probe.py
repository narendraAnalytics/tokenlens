"""Local test for the Replay Agent (phase3.txt Phase 3B §3): timeline
reconstruction against real Flow 1 / toy_graph runs, then (once
examples/failing_graph.py has landed a real failed span) failure
explanation end-to-end via run_agent().

Run: uv run python -m examples.replay_agent_probe
"""

from agents import base
from agents.replay import REPLAY_AGENT, register_agent
from agents.tools import replay_reader

FLOW1_RUN_ID = "fa965b0035424e0e8e81f81651850d45"
TOY_RUN_ID = "d3533ff7b21c48d08690dc7ce8d7bcaa"

# Set by examples/failing_graph.py's last run -- filled in once that probe
# has landed a real failed span in BigQuery.
FAILING_RUN_ID = "8cbd75655b5b48c087c7ecf700da2722"


def main() -> None:
    register_agent()

    for run_id, label in [(FLOW1_RUN_ID, "Flow1"), (TOY_RUN_ID, "toy")]:
        timeline = replay_reader.reconstruct_timeline(run_id=run_id)
        print(f"-- timeline for {label} run {run_id} --")
        for span in timeline:
            print(f"  {span['timestamp']} {span['node_name']} status={span['status']}")
        assert timeline, f"expected a non-empty timeline for {label}"
        timestamps = [s["timestamp"] for s in timeline]
        assert timestamps == sorted(timestamps), "expected oldest-first ordering"

        response = base.run_agent(
            REPLAY_AGENT,
            "What did this run actually do, step by step?",
            extra_context=f"run_id={run_id}",
        )
        print(f"  answer: {response.text}\n")
        assert response.text.strip()

    if FAILING_RUN_ID:
        result = replay_reader.explain_failure(run_id=FAILING_RUN_ID)
        print(f"-- explain_failure for {FAILING_RUN_ID} --\n{result}")
        assert result["failure_count"] >= 1, "expected at least one failure"

        response = base.run_agent(
            REPLAY_AGENT, "Why did this run fail?", extra_context=f"run_id={FAILING_RUN_ID}"
        )
        print(f"answer: {response.text}")
        assert "explode" in response.text.lower() or "runtimeerror" in response.text.lower(), (
            "expected the answer to identify the actual failing node/error"
        )
    else:
        print("FAILING_RUN_ID not set yet -- run examples/failing_graph.py first, "
              "drain via bigquery_worker.py --once, then set FAILING_RUN_ID here and re-run.")

    print("replay_agent_probe: assertions passed (failure case " +
          ("included" if FAILING_RUN_ID else "SKIPPED -- see note above") + ")")


if __name__ == "__main__":
    main()
