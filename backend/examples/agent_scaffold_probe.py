"""Local test for the agent registry + base agent scaffolding (phase3.txt
Phase 3A §2): registers one dummy AgentSpec with one trivial ToolSpec, and
confirms run_agent() actually invokes the tool, folds the result back into
the transcript, and returns a final answer within the iteration bound.

Not a real Phase 3B agent -- pure scaffolding verification.

Run: uv run python -m examples.agent_scaffold_probe
"""

from agents import base
from agents.registry import AgentSpec, ToolSpec, get, list_agents, register


def get_fixed_fact() -> dict:
    return {"fact": "TokenLens Phase 3A was built to prove the agent platform foundation."}


def main() -> None:
    tool = ToolSpec(
        name="get_fixed_fact",
        description="Returns a fixed fact you must use to answer the question.",
        parameters={},
        fn=get_fixed_fact,
    )
    spec = AgentSpec(
        name="probe_agent",
        system_prompt=(
            "You are a test agent. You MUST call the get_fixed_fact tool "
            "before answering, then answer using only the fact it returns."
        ),
        tools=[tool],
    )
    register(spec)

    assert list_agents() == ["probe_agent"]
    assert get("probe_agent") is spec

    response = base.run_agent(spec, "What is the fact?")
    print(f"final answer: {response.text!r}")
    print(f"tokens: in={response.input_tokens} out={response.output_tokens} cost={response.cost_usd}")

    assert "phase 3a" in response.text.lower() or "agent platform foundation" in response.text.lower(), (
        "expected the final answer to reflect the tool's fixed fact"
    )
    print("agent_scaffold_probe: all assertions passed")


if __name__ == "__main__":
    main()
