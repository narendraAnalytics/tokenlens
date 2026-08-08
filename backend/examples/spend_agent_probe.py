"""Local test for the Spend Agent (phase3.txt Phase 3B §2): runs all 7
deterministic detectors raw (no LLM) against real BigQuery data from
toy_graph.py and Flow 1's real customer_chat run, then runs the full
run_agent() loop end-to-end against a real "why was this run expensive"
question.

Run: uv run python -m examples.spend_agent_probe
"""

from agents import base
from agents.spend import SPEND_AGENT, register_agent
from agents.tools import pricing_reader, spend_detectors

# Real run_ids confirmed present in tokenlens_traces.spans (queried directly
# before writing this probe -- not guessed).
FLOW1_RUN_ID = "fa965b0035424e0e8e81f81651850d45"  # customer_chat
FLOW1_TENANT_ID = "tokenlens-chat-demo"
TOY_RUN_ID = "d3533ff7b21c48d08690dc7ce8d7bcaa"  # toy_support_agent
TOY_TENANT_ID = "demo-tenant"


def main() -> None:
    print("=== raw detector calls (no LLM) ===")

    summary = pricing_reader.summarize_run_cost(run_id=FLOW1_RUN_ID)
    print(f"summarize_run_cost(Flow1): {summary}")
    assert summary["totals"]["total_cost_usd"] > 0, "expected nonzero real cost for Flow 1's run"

    compare = pricing_reader.compare_models(tenant_id=FLOW1_TENANT_ID)
    print(f"compare_models(Flow1 tenant): {compare}")

    trend = pricing_reader.usage_trend(tenant_id=TOY_TENANT_ID)
    print(f"usage_trend(toy tenant): {trend}")

    for run_id, label in [(FLOW1_RUN_ID, "Flow1"), (TOY_RUN_ID, "toy")]:
        print(f"-- detectors against {label} run {run_id} --")
        print("runaway_loop:", spend_detectors.detect_runaway_loop(run_id=run_id, threshold=1))
        print("redundant_tool_call:", spend_detectors.detect_redundant_tool_call(run_id=run_id))
        print("over_retrieval:", spend_detectors.detect_over_retrieval(run_id=run_id, ratio_threshold=0.0))
        print(
            "model_over_specification:",
            spend_detectors.detect_model_over_specification(run_id=run_id, output_token_ceiling=100_000),
        )

    print("orphaned_workflow(toy tenant):", spend_detectors.detect_orphaned_workflow(tenant_id=TOY_TENANT_ID, stale_after_days=0))
    print("retry_storm(toy tenant):", spend_detectors.detect_retry_storm(tenant_id=TOY_TENANT_ID, p95_threshold=-1))
    print("tenant_skew:", spend_detectors.detect_tenant_skew(skew_ratio_threshold=0.0))

    print("\n=== end-to-end run_agent() against a real question ===")
    register_agent()
    response = base.run_agent(
        SPEND_AGENT,
        "Why was this run expensive?",
        extra_context=f"run_id={FLOW1_RUN_ID} tenant_id={FLOW1_TENANT_ID}",
    )
    print(f"answer: {response.text}")
    print(f"tokens: in={response.input_tokens} out={response.output_tokens} cost={response.cost_usd}")

    assert response.text.strip(), "expected a non-empty final answer"
    assert "$" in response.text or "cost" in response.text.lower(), (
        "expected the answer to actually discuss cost, not deflect"
    )
    print("spend_agent_probe: all assertions passed")


if __name__ == "__main__":
    main()
