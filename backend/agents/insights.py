"""Insights Agent (phase3.txt Phase 3B §5) -- synthesizes text findings
already produced by Spend/Replay/Policy into root-cause analysis /
recommendations. No tools of its own: it reads whatever's passed via
agents/base.py's existing `extra_context` parameter, it doesn't query
BigQuery/Cloud SQL directly.

Starts on Gemini Flash per phase3.txt (Pro is an implementation-time
decision, not fixed here) -- if Flash's synthesis quality proves
insufficient once real findings are available to judge against, this is a
one-line `model=` change, not a rewrite.
"""

from agents import gateway
from agents.registry import AgentSpec, register

_SYSTEM_PROMPT = """You are the Insights Agent for TokenLens, an AI
control plane. You synthesize findings already produced by the Spend,
Replay, and Policy agents (given to you as context) into one coherent
root-cause analysis or recommendation. You do NOT have tools and cannot
query BigQuery/Cloud SQL yourself -- only use the numbers and findings
given to you in the context below. Never invent a number, model name, or
finding that isn't present in the given context. If the context doesn't
contain enough information to answer, say so explicitly rather than
guessing."""

INSIGHTS_AGENT = AgentSpec(
    name="insights",
    system_prompt=_SYSTEM_PROMPT,
    tools=[],
    model=gateway.DEFAULT_MODEL,  # "gemini-2.5-flash" -- see module docstring
)


def register_agent() -> None:
    register(INSIGHTS_AGENT)
