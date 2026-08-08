"""Spend Agent (phase3.txt Phase 3B §2) -- just an AgentSpec wiring the 7
deterministic detectors (agents/tools/spend_detectors.py) and the cost/
usage tools (agents/tools/pricing_reader.py) as ToolSpecs. No new agent
loop here -- agents/base.py's run_agent() is the shared implementation
every specialist reuses.

System prompt is explicit that the detectors and cost tools are
deterministic ground truth: the model's job is to call them and then only
rank/phrase the findings, never invent a number that didn't come back from
a tool call (same principle the Overview doc establishes for the original
Analyst Detectors).
"""

from agents.registry import AgentSpec, ToolSpec, register
from agents.tools import pricing_reader, spend_detectors

_SYSTEM_PROMPT = """You are the Spend Agent for TokenLens, an AI control
plane. You analyze ALREADY-COLLECTED telemetry about past LangGraph runs
-- you never re-run or re-answer the original question a run was for.

You have 7 deterministic detector tools and 3 cost-analysis tools. Always
call the tools relevant to the question before answering. The numbers
these tools return are ground truth -- never invent, estimate, or round a
cost/token/count figure that didn't come back from a tool call. Your job
is to rank and phrase the findings for a human, not to compute them
yourself.

If a question mentions a specific run, call summarize_run_cost first for
an overview, then the relevant detector(s). If a question is about a
tenant's overall spend/trends, use compare_models/usage_trend and the
tenant-scoped detectors (detect_orphaned_workflow, detect_retry_storm,
detect_tenant_skew)."""

_TOOLS = [
    ToolSpec(
        name="summarize_run_cost",
        description="Total cost/tokens/latency for a run_id, plus a per-node breakdown.",
        parameters={"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
        fn=pricing_reader.summarize_run_cost,
    ),
    ToolSpec(
        name="compare_models",
        description="Cost/latency/volume by model for a tenant over a trailing window (default 7 days).",
        parameters={
            "type": "object",
            "properties": {"tenant_id": {"type": "string"}, "window_days": {"type": "integer"}},
            "required": ["tenant_id"],
        },
        fn=pricing_reader.compare_models,
    ),
    ToolSpec(
        name="usage_trend",
        description="Daily cost/token rollup for a tenant over a trailing window (default 30 days).",
        parameters={
            "type": "object",
            "properties": {"tenant_id": {"type": "string"}, "window_days": {"type": "integer"}},
            "required": ["tenant_id"],
        },
        fn=pricing_reader.usage_trend,
    ),
    ToolSpec(
        name="detect_runaway_loop",
        description="Finds nodes executed more than `threshold` (default 10) times in one run.",
        parameters={
            "type": "object",
            "properties": {"run_id": {"type": "string"}, "threshold": {"type": "integer"}},
            "required": ["run_id"],
        },
        fn=spend_detectors.detect_runaway_loop,
    ),
    ToolSpec(
        name="detect_redundant_tool_call",
        description="Finds tool names invoked 2+ times within one run.",
        parameters={"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
        fn=spend_detectors.detect_redundant_tool_call,
    ),
    ToolSpec(
        name="detect_over_retrieval",
        description="Finds spans whose input tokens far exceed their output tokens (wasteful context).",
        parameters={
            "type": "object",
            "properties": {"run_id": {"type": "string"}, "ratio_threshold": {"type": "number"}},
            "required": ["run_id"],
        },
        fn=spend_detectors.detect_over_retrieval,
    ),
    ToolSpec(
        name="detect_model_over_specification",
        description="Finds a frontier ('pro') model used on a node with a short, tool-free output.",
        parameters={
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "tenant_id": {"type": "string"},
                "output_token_ceiling": {"type": "integer"},
            },
        },
        fn=spend_detectors.detect_model_over_specification,
    ),
    ToolSpec(
        name="detect_orphaned_workflow",
        description="Finds runs with no span activity for 14+ days (stale/orphaned workflows) for a tenant.",
        parameters={
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "graph_name": {"type": "string"},
                "stale_after_days": {"type": "integer"},
            },
            "required": ["tenant_id"],
        },
        fn=spend_detectors.detect_orphaned_workflow,
    ),
    ToolSpec(
        name="detect_retry_storm",
        description="Finds nodes whose p95 retry count exceeds a threshold (default 3) for a tenant.",
        parameters={
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "graph_name": {"type": "string"},
                "p95_threshold": {"type": "integer"},
            },
            "required": ["tenant_id"],
        },
        fn=spend_detectors.detect_retry_storm,
    ),
    ToolSpec(
        name="detect_tenant_skew",
        description="Finds a tenant accounting for a disproportionate share (default >50%) of total spend.",
        parameters={
            "type": "object",
            "properties": {"window_days": {"type": "integer"}, "skew_ratio_threshold": {"type": "number"}},
        },
        fn=spend_detectors.detect_tenant_skew,
    ),
]

SPEND_AGENT = AgentSpec(name="spend", system_prompt=_SYSTEM_PROMPT, tools=_TOOLS)


def register_agent() -> None:
    register(SPEND_AGENT)
