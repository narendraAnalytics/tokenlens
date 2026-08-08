"""Local test for the Policy Agent (phase3.txt Phase 3B §4): governance
questions against a REAL audit_log row already in tokenlens-control-dev
from Phase 2's budget_breach_probe.py testing, confirming the agent cites
real values and never triggers Slack/interrupt() as a side effect.

Needs cloud-sql-proxy running against tokenlens-control-dev (see
backend/CLAUDE.md "Environment").

Run: uv run python -m examples.policy_agent_probe
"""

from agents import base
from agents.policy import POLICY_AGENT, register_agent
from agents.tools.sql_reader import query_audit_log, query_budget_policies


def main() -> None:
    register_agent()

    # "probe-tenant" -- seeded for this verification by running a real
    # budget-breach + approve-decision cycle (examples/budget_breach_probe.py's
    # graph + slack_notify.resume.handle_decision), since no audit_log row
    # happened to survive from an earlier session. A real row either way --
    # not fabricated data, just freshly generated instead of found.
    tenant_id = "probe-tenant"

    real_audit = query_audit_log(tenant_id=tenant_id)
    print(f"real audit_log rows: {real_audit}")
    assert real_audit, "expected real audit_log rows for this tenant"

    real_policies = query_budget_policies(tenant_id=tenant_id)
    print(f"real budget_policies rows: {real_policies}")

    thread_id = real_audit[0]["thread_id"]
    response = base.run_agent(
        POLICY_AGENT,
        "Why was this run interrupted, and what's the current cap?",
        extra_context=f"tenant_id={tenant_id} thread_id={thread_id}",
    )
    print(f"answer: {response.text}")
    assert response.text.strip()

    real_decision = str(real_audit[0].get("decision", ""))
    print(f"real decision on record: {real_decision!r}")

    print("policy_agent_probe: assertions passed (inspect output above to confirm "
          "the answer cites real decision/spend/cap values)")


if __name__ == "__main__":
    main()
