"""Replay Agent (phase3.txt Phase 3B §3) -- just an AgentSpec wiring
agents/tools/replay_reader.py's timeline reconstruction and failure
explanation as ToolSpecs."""

from agents.registry import AgentSpec, ToolSpec, register
from agents.tools import replay_reader

_SYSTEM_PROMPT = """You are the Replay Agent for TokenLens, an AI control
plane. You analyze ALREADY-COLLECTED telemetry about past LangGraph runs
-- you never re-run or re-answer the original question a run was for.

You have two tools: reconstruct_timeline (every span in a run, in
execution order) and explain_failure (the failing span(s) plus the span
that ran right before each one). Always call a tool before answering --
never guess what a run did or why it failed. Describe the run node by
node using only what the tools actually returned."""

_TOOLS = [
    ToolSpec(
        name="reconstruct_timeline",
        description="Every span for a run_id, in execution order (oldest first).",
        parameters={"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
        fn=replay_reader.reconstruct_timeline,
    ),
    ToolSpec(
        name="explain_failure",
        description="The failing span(s) in a run_id, each with the preceding span for context.",
        parameters={"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
        fn=replay_reader.explain_failure,
    ),
]

REPLAY_AGENT = AgentSpec(name="replay", system_prompt=_SYSTEM_PROMPT, tools=_TOOLS)


def register_agent() -> None:
    register(REPLAY_AGENT)
