"""Explicit, single entry point that registers all 5 Phase 3B specialist
agents (phase3.txt Phase 3B) -- called once from main.py's lifespan and
from any probe/test script that needs the registry populated. Explicit
rather than import-side-effect registration in each agent module, same
"explicit, not implicit" spirit as slack_notify/resume.py's
_GRAPH_BUILDERS dict -- registration order and completeness is visible in
one place instead of depending on import order across 5 files.
"""

from agents import insights, policy, replay, spend

# Note: planner.py is deliberately NOT registered here -- it's plain
# Python orchestration (classify_intent/merge_responses), not an
# AgentSpec fetched via registry.get() (see agents/planner.py's
# docstring).


def register_all() -> None:
    spend.register_agent()
    replay.register_agent()
    policy.register_agent()
    insights.register_agent()
