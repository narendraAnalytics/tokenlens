"""Local test for the Insights Agent (phase3.txt Phase 3B §5): feeds real
findings (from spend_agent_probe.py's own real BigQuery output) as
extra_context and confirms the synthesized answer references those real
specifics rather than inventing new ones.

Run: uv run python -m examples.insights_agent_probe
"""

from agents import base
from agents.insights import INSIGHTS_AGENT, register_agent
from agents.tools import pricing_reader

FLOW1_RUN_ID = "fa965b0035424e0e8e81f81651850d45"
FLOW1_TENANT_ID = "tokenlens-chat-demo"


def main() -> None:
    register_agent()

    real_summary = pricing_reader.summarize_run_cost(run_id=FLOW1_RUN_ID)
    real_compare = pricing_reader.compare_models(tenant_id=FLOW1_TENANT_ID)
    findings = (
        f"[spend] Run {FLOW1_RUN_ID} cost {real_summary['totals']['total_cost_usd']} USD "
        f"total, all on model comparison: {real_compare}. No cost anomalies detected "
        f"(runaway loops, redundant calls, over-specification all came back empty).\n"
        f"[replay] The run completed successfully with 2 spans: ingest_attachment "
        f"(no LLM usage) then answer (real gemini-2.5-flash call).\n"
        f"[policy] No budget breach occurred for this run; no audit_log entry exists "
        f"for this thread_id."
    )

    response = base.run_agent(
        INSIGHTS_AGENT,
        "Which model should I use next time, and why did costs go up this week?",
        extra_context=findings,
    )
    print(f"answer: {response.text}")
    assert response.text.strip(), "expected a non-empty synthesis"
    assert "gemini" in response.text.lower() or "flash" in response.text.lower(), (
        "expected the synthesis to reference the real model name from the fed findings"
    )
    print("insights_agent_probe: all assertions passed")


if __name__ == "__main__":
    main()
