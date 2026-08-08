"""Policy Agent (phase3.txt Phase 3B §4) -- wraps Phase 2's Spend Guard
AS-IS: read-only over budget_policies/audit_log via
agents/tools/policy_reader.py. Nothing here can trigger Slack or
`interrupt()` -- this AgentSpec's tools are exactly the two read-only
pass-throughs, no more, so there is no code path by which answering a
question can re-trigger an approval flow as a side effect.
"""

from agents.registry import AgentSpec, ToolSpec, register
from agents.tools import policy_reader

_SYSTEM_PROMPT = """You are the Policy Agent for TokenLens, an AI control
plane. You answer governance questions ("why was this run interrupted",
"what's the current budget cap", "who approved this run") by READING the
budget_policies and audit_log tables via your tools -- you NEVER trigger a
new approval, notification, or budget change. You have no tools capable
of doing that; if a user asks you to approve/change/raise something,
explain that this requires the actual Slack approval flow, which you
cannot invoke.

Always call a tool before answering -- and call BOTH tools if the question
touches both governance history and the current cap (e.g. "why was this
interrupted, and what's the current cap" needs query_audit_log AND
query_budget_policies, not just one). audit_log has no free-text "reason"
column -- explain an interruption using the decision value
(approve/approve_and_raise_cap/kill) and spend_at_decision_usd/new_cap_usd,
not a reason string that doesn't exist in the schema. Cite the real
decision/spend/cap values the tools return -- never invent one."""

_TOOLS = [
    ToolSpec(
        name="query_budget_policies",
        description="All budget_policies rows (active and historical) for a tenant, optionally by graph_name.",
        parameters={
            "type": "object",
            "properties": {"tenant_id": {"type": "string"}, "graph_name": {"type": "string"}},
            "required": ["tenant_id"],
        },
        fn=policy_reader.query_budget_policies,
    ),
    ToolSpec(
        name="query_audit_log",
        description="Audit-log rows for a tenant, optionally narrowed to one thread_id (= run_id).",
        parameters={
            "type": "object",
            "properties": {"tenant_id": {"type": "string"}, "thread_id": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["tenant_id"],
        },
        fn=policy_reader.query_audit_log,
    ),
]

POLICY_AGENT = AgentSpec(name="policy", system_prompt=_SYSTEM_PROMPT, tools=_TOOLS)


def register_agent() -> None:
    register(POLICY_AGENT)
