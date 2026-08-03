"""Core span-emitting logic.

TokenLens instruments a LangGraph app via a LangChain callback handler
rather than by monkeypatching compiled-graph internals. This is the same
integration point LangSmith and other LangChain-ecosystem observability
tools use, and — empirically confirmed against the installed langgraph
1.2.x / langchain-core 1.5.x — LangGraph tags every node's chain run with
`metadata["langgraph_node"] = <node name>`, which is what lets us detect
"this chain run IS a graph node execution" and open exactly one span per
node execution, regardless of how much LangChain machinery happens inside
that node's function body.

Nested LLM/tool calls inside a node arrive as separate callback events
(on_llm_start/on_llm_end, on_tool_start/on_tool_end) with their own
run_id/parent_run_id. We walk the parent chain (recorded from every start
event, not just node ones) to find which open node span a nested call
belongs to, and fold its gen_ai.* data into that span.
"""

import json
import time
from dataclasses import dataclass, field

from langchain_core.callbacks.base import BaseCallbackHandler
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from tokenlens_sdk import pricing, spend_ledger
from tokenlens_sdk.redaction import redact_state

_SYSTEM_HINTS = (
    ("gemini", "google"),
    ("gemma", "google"),
    ("claude", "anthropic"),
    ("grok", "xai"),
    ("gpt", "openai"),
    ("llama", "meta"),
    ("qwen", "alibaba"),
)


def _infer_system(model_name: str) -> str | None:
    lowered = model_name.lower()
    for hint, system in _SYSTEM_HINTS:
        if hint in lowered:
            return system
    return None


@dataclass
class _NodeSpanRecord:
    span: Span
    start_time: float
    node_name: str
    input_state: dict
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    request_model: str | None = None
    response_model: str | None = None
    system: str | None = None
    tool_names: list[str] = field(default_factory=list)
    retry_count: int = 0


class TokenLensCallbackHandler(BaseCallbackHandler):
    """One instance per graph invocation — created fresh by
    `_InstrumentedGraph.invoke()` so concurrent invocations never share
    state. Emits one OTel span per LangGraph node execution."""

    def __init__(
        self,
        *,
        tracer: Tracer,
        run_id: str,
        graph_name: str,
        tenant_id: str,
        sensitive_keys: frozenset[str],
        payload_capture_mode: str,
        budget_cap_usd: float | None = None,
    ) -> None:
        self._tracer = tracer
        self._run_id = run_id
        self._graph_name = graph_name
        self._tenant_id = tenant_id
        self._sensitive_keys = sensitive_keys
        self._payload_capture_mode = payload_capture_mode
        self._parents: dict[str, str | None] = {}
        self._node_spans: dict[str, _NodeSpanRecord] = {}
        # Budget-gating state (Phase 2 §2). budget_cap_usd is None when no
        # active policy exists for this tenant+graph, so nothing is gated.
        # cumulative_cost_usd is a same-invocation-only mirror, priced via
        # the SDK's own local pricing table — never tokenlens_cost (the
        # official, ingest-computed figure) — kept for local
        # debugging/telemetry. It is NOT what the node wrapper's pre-check
        # actually gates on: that reads tokenlens_sdk.spend_ledger instead,
        # since this in-process value resets to zero on every resume (see
        # spend_ledger.py's module docstring for why that matters).
        self.budget_cap_usd = budget_cap_usd
        self.cumulative_cost_usd: float = 0.0

    @property
    def run_id(self) -> str:
        return self._run_id

    # -- parent-chain bookkeeping, used by every *_start callback --

    def _record_parent(self, run_id: object, parent_run_id: object) -> None:
        self._parents[str(run_id)] = str(parent_run_id) if parent_run_id else None

    def _find_node_run_id(self, run_id: object) -> str | None:
        seen: set[str] = set()
        current = str(run_id) if run_id else None
        while current is not None and current not in seen:
            if current in self._node_spans:
                return current
            seen.add(current)
            current = self._parents.get(current)
        return None

    # -- node-level spans (LangGraph tags these via metadata) --

    def on_chain_start(
        self, serialized, inputs, *, run_id, parent_run_id=None, tags=None,
        metadata=None, **kwargs,
    ) -> None:
        self._record_parent(run_id, parent_run_id)
        node_name = (metadata or {}).get("langgraph_node")
        if not node_name:
            return  # internal LangChain plumbing, not a graph node — ignore
        span = self._tracer.start_span(f"{self._graph_name}.{node_name}")
        self._node_spans[str(run_id)] = _NodeSpanRecord(
            span=span,
            start_time=time.monotonic(),
            node_name=node_name,
            input_state=inputs if isinstance(inputs, dict) else {"value": inputs},
        )

    def on_chain_end(self, outputs, *, run_id, **kwargs) -> None:
        record = self._node_spans.pop(str(run_id), None)
        if record is not None:
            self._finish_span(record, outputs, status="completed")

    def on_chain_error(self, error, *, run_id, **kwargs) -> None:
        record = self._node_spans.pop(str(run_id), None)
        if record is not None:
            self._finish_span(record, {}, status="failed", error=error)

    # -- nested LLM calls, folded into the enclosing node span --

    def on_llm_start(
        self, serialized, prompts, *, run_id, parent_run_id=None, **kwargs
    ) -> None:
        self._record_parent(run_id, parent_run_id)

    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs) -> None:
        node_run_id = self._find_node_run_id(parent_run_id)
        if node_run_id is None:
            return
        record = self._node_spans[node_run_id]
        try:
            message = response.generations[0][0].message
        except (IndexError, AttributeError):
            message = None
        if message is None:
            return
        usage = getattr(message, "usage_metadata", None) or {}
        call_input_tokens = usage.get("input_tokens", 0) or 0
        call_output_tokens = usage.get("output_tokens", 0) or 0
        record.input_tokens += call_input_tokens
        record.output_tokens += call_output_tokens
        record.cached_tokens += (usage.get("input_token_details") or {}).get(
            "cache_read", 0
        ) or 0
        resp_meta = getattr(message, "response_metadata", None) or {}
        model_name = resp_meta.get("model_name") or resp_meta.get("model")
        if model_name and record.response_model is None:
            record.response_model = model_name
            record.request_model = model_name
            record.system = _infer_system(model_name)
        # Budget-gating cost — priced on this call's own tokens, not the
        # node's running total, so a node with several LLM calls counts
        # each one exactly once. Written to both the same-invocation mirror
        # (cheap, for local telemetry) and the durable ledger (the actual
        # source of truth the node wrapper's pre-check reads).
        call_cost_usd = pricing.estimate_cost_usd(
            model_name, call_input_tokens, call_output_tokens
        )
        self.cumulative_cost_usd += call_cost_usd
        if self.budget_cap_usd is not None:
            spend_ledger.record_spend(self._run_id, call_cost_usd)

    # -- nested tool calls, folded into the enclosing node span --

    def on_tool_start(
        self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs
    ) -> None:
        self._record_parent(run_id, parent_run_id)
        node_run_id = self._find_node_run_id(parent_run_id)
        if node_run_id is None:
            return
        name = serialized.get("name") if isinstance(serialized, dict) else None
        if name:
            self._node_spans[node_run_id].tool_names.append(name)

    # -- retries, folded into the enclosing node span --

    def on_retry(self, retry_state, *, run_id, **kwargs) -> None:
        node_run_id = self._find_node_run_id(run_id)
        if node_run_id is not None:
            self._node_spans[node_run_id].retry_count += 1

    # -- finalize a node span: gen_ai.* + tokenlens.* attributes --

    def _finish_span(
        self, record: _NodeSpanRecord, outputs, *, status: str, error=None
    ) -> None:
        latency_ms = (time.monotonic() - record.start_time) * 1000
        span = record.span

        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        if record.system:
            span.set_attribute("gen_ai.system", record.system)
        if record.request_model:
            span.set_attribute("gen_ai.request.model", record.request_model)
        if record.response_model:
            span.set_attribute("gen_ai.response.model", record.response_model)
        span.set_attribute("gen_ai.usage.input_tokens", record.input_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", record.output_tokens)
        span.set_attribute("gen_ai.agent.name", record.node_name)
        span.set_attribute("gen_ai.agent.type", "langgraph_node")
        if record.tool_names:
            span.set_attribute("gen_ai.tool.name", record.tool_names)

        span.set_attribute("tokenlens.run_id", self._run_id)
        span.set_attribute("tokenlens.graph_name", self._graph_name)
        span.set_attribute("tokenlens.node_name", record.node_name)
        span.set_attribute("tokenlens.tenant_id", self._tenant_id)
        span.set_attribute("tokenlens.status", status)
        span.set_attribute("tokenlens.latency_ms", latency_ms)
        span.set_attribute("tokenlens.retry_count", record.retry_count)
        span.set_attribute("tokenlens.cached_tokens", record.cached_tokens)
        span.set_attribute(
            "tokenlens.payload_capture_mode", self._payload_capture_mode
        )
        # tokenlens.cost is deliberately never set here — cost is computed
        # at ingest from a pricing table, never in the SDK (backend/CLAUDE.md).

        if self._payload_capture_mode == "full":
            redacted_input = redact_state(record.input_state, self._sensitive_keys)
            redacted_output = redact_state(
                outputs if isinstance(outputs, dict) else {"value": outputs},
                self._sensitive_keys,
            )
            span.set_attribute(
                "tokenlens.payload_redacted",
                json.dumps(
                    {"input": redacted_input, "output": redacted_output},
                    default=str,
                ),
            )

        if status == "failed":
            span.set_status(Status(StatusCode.ERROR, str(error) if error else "node failed"))
        else:
            span.set_status(Status(StatusCode.OK))
        span.end()
