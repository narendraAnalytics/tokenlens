"""Read-only pass-throughs to agents/tools/sql_reader.py for the Policy
Agent (phase3.txt Phase 3B §4). Deliberately nothing else: this module
must never expose a tool that can trigger Slack or `interrupt()` --
Phase 2's Spend Guard (tokenlens_sdk/client.py's `_budget_gate`,
agents/runtime_context.py, slack_notify/) is reused AS-IS and stays wired
only at that call site, unchanged. A governance question is answered by
reading audit_log/budget_policies, never by re-triggering an approval as
a side effect of answering it.
"""

from agents.tools.sql_reader import query_audit_log, query_budget_policies

__all__ = ["query_budget_policies", "query_audit_log"]
