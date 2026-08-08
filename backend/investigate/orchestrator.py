"""Orchestration for POST /v1/investigate (phase3.txt Phase 3B §6):
classify intent via the Planner, invoke the chosen specialist(s) via
agents/base.py's run_agent(), merge if more than one was routed to.

Deliberately synchronous (`def`, not `async def`) -- matches agents/
base.py and agents/gateway.py's own synchronous+ThreadPoolExecutor shape,
nothing here awaits.
"""

from agents import base, planner
from agents.registry import get as get_agent


def investigate(*, user_message: str, run_id: str | None, tenant_id: str) -> dict:
    labels = planner.classify_intent(user_message)

    extra_context_parts = [f"tenant_id={tenant_id}"]
    if run_id:
        extra_context_parts.append(f"run_id={run_id}")
    extra_context = " ".join(extra_context_parts)

    outputs: dict[str, str] = {}
    for label in labels:
        spec = get_agent(label)
        response = base.run_agent(spec, user_message, extra_context=extra_context)
        outputs[label] = response.text

    if len(labels) == 1:
        return {"answer": outputs[labels[0]], "routed_to": labels}

    merged = planner.merge_responses(user_message, outputs)
    return {"answer": merged.text, "routed_to": labels}
