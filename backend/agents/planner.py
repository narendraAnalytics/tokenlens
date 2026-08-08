"""Planner Agent (phase3.txt Phase 3B §1) -- plain Python orchestration,
deliberately NOT built on agents/base.py's ReAct tool-calling loop.

agents/base.py's run_agent() is for an agent calling deterministic *tool
functions* it can observe the result of and react to. Planner's job is
different: decide *which specialist agents* to invoke (each a full
run_agent() call with its own gateway round-trip), then merge their text
outputs. Forcing that into the TOOL_CALL/TOOL_RESULT text protocol would
add an unneeded layer of text parsing on top of calls that are already
deterministic Python dispatch -- there's nothing for the model to decide
about *how* to call a specialist once intent is classified, only *which*
one(s). So: one direct gateway.generate() call to classify intent (the one
place an LLM judgment call belongs), plain Python dispatch to invoke the
chosen specialist(s) (done in investigate/orchestrator.py, not here), and
one more direct gateway.generate() call to merge multi-specialist output.

Not registered in agents/registry.py as an AgentSpec -- there's no tool
list or system-prompt-driven loop here for the registry pattern to fit.
"""

from agents import gateway

VALID_LABELS = ("spend", "replay", "policy", "insights")

_INTENT_PROMPT = """Classify which TokenLens specialist agent(s) should
handle the user's question below. Valid labels, exactly as written:
spend, replay, policy, insights.

- spend: cost, token usage, pricing, "why was this expensive", model
  comparison, spend trends, cost anomalies (runaway loops, redundant
  calls, over-retrieval, retry storms, tenant skew).
- replay: what a run actually did step by step, "why did this fail",
  timeline/trace reconstruction.
- policy: budget caps, approvals, "why was this interrupted", governance/
  audit history.
- insights: synthesis/recommendations across the above, "which model
  should I use next time", "why did costs go up this week" (when it's
  asking for a root-cause recommendation, not a raw number).

Reply with ONLY a comma-separated list of one or more of the four labels
above, nothing else -- no punctuation, no explanation.

User question: {question}"""

_MERGE_PROMPT = """The user asked: {question}

Below are findings from TokenLens specialist agents. Write ONE coherent
answer to the user's question, combining these findings. Do not invent
anything not present in the findings below.

{findings}"""


def classify_intent(user_message: str) -> list[str]:
    """Returns 1+ of VALID_LABELS. Raises ValueError on an unparseable or
    empty response -- deliberately not a silent default to e.g. "insights",
    since a masked bad classification is worse than a visible failure the
    caller can react to."""
    response = gateway.generate(
        prompt=_INTENT_PROMPT.format(question=user_message),
    )
    raw_labels = [label.strip().lower() for label in response.text.split(",")]
    labels = [label for label in raw_labels if label in VALID_LABELS]
    if not labels:
        raise ValueError(
            f"classify_intent could not parse a valid label from model output: {response.text!r}"
        )
    # de-duplicate while preserving order
    seen: set[str] = set()
    deduped = []
    for label in labels:
        if label not in seen:
            seen.add(label)
            deduped.append(label)
    return deduped


def merge_responses(
    user_message: str, specialist_outputs: dict[str, str]
) -> gateway.GatewayResponse:
    """Combines multiple specialists' text outputs into one answer. Not
    called for the single-label case -- the caller (investigate/
    orchestrator.py) passes that output straight through to avoid a
    needless extra LLM call+cost."""
    findings = "\n\n".join(
        f"[{label}]\n{text}" for label, text in specialist_outputs.items()
    )
    return gateway.generate(
        prompt=_MERGE_PROMPT.format(question=user_message, findings=findings),
    )
