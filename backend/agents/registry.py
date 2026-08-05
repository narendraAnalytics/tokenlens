"""Agent registry (phase3.txt Phase 3A §2) — a lightweight way to register
and list agents (name, system prompt, tools, model) that a future Planner
(Phase 3B) can route to by name/intent. Pure scaffolding this phase —
nothing real gets registered here; Phase 3B populates it with the 5
specialist agents.
"""

from dataclasses import dataclass, field
from typing import Any, Callable

from agents import gateway


@dataclass(frozen=True)
class ToolSpec:
    """A tool callable an agent can invoke via the text-based ReAct loop
    in agents/base.py. `parameters` is a JSON-schema-shaped dict describing
    the tool's arguments, rendered into the agent's prompt so the model
    knows how to call it."""

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]


@dataclass(frozen=True)
class AgentSpec:
    """Everything shared across all 5 Phase 3B agents vs. what's
    agent-specific: a system prompt + tool list + model is genuinely all
    that varies. agents/base.py's run_agent() is the one shared
    implementation of "call the gateway, format tools, parse output" every
    agent module reuses."""

    name: str
    system_prompt: str
    tools: list[ToolSpec] = field(default_factory=list)
    model: str = gateway.DEFAULT_MODEL


_REGISTRY: dict[str, AgentSpec] = {}


def register(spec: AgentSpec) -> None:
    if spec.name in _REGISTRY:
        raise ValueError(f"agent {spec.name!r} is already registered")
    _REGISTRY[spec.name] = spec


def get(name: str) -> AgentSpec:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"no agent registered as {name!r} — registered agents: {list(_REGISTRY)}"
        ) from None


def list_agents() -> list[str]:
    return list(_REGISTRY)
