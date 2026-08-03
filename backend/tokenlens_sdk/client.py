"""Public entry point: `TokenLens(...).instrument(compiled_graph)`."""

import uuid
from typing import Any

from langchain_core.runnables import RunnableLambda
from langgraph.types import interrupt
from opentelemetry.sdk.trace.export import SpanExporter

from tokenlens_sdk import budget, spend_ledger
from tokenlens_sdk.exporter import HTTPSpanExporter
from tokenlens_sdk.handler import TokenLensCallbackHandler
from tokenlens_sdk.tracer import get_tracer

# Pregel's pseudo-nodes — never wrap these, they aren't customer node
# functions and don't take the (state, config) shape we assume below.
_NON_WRAPPABLE_NODES = frozenset({"__start__", "__end__"})


class TokenLens:
    """Configuration for one instrumented graph. Create one instance per
    graph (not per invocation) and reuse it across the process lifetime."""

    def __init__(
        self,
        *,
        graph_name: str,
        tenant_id: str,
        sensitive_keys: set[str] | None = None,
        payload_capture_mode: str = "full",
        exporter: SpanExporter | None = None,
        ingest_url: str | None = None,
    ) -> None:
        self.graph_name = graph_name
        self.tenant_id = tenant_id
        # TODO(Phase 2): once the Cloud SQL control store exists, poll
        # payload_capture_mode from the tenant record every 60s (same poll
        # the SDK uses for routing policy — see backend/CLAUDE.md PII
        # policy) instead of taking it as a static constructor argument.
        self.payload_capture_mode = payload_capture_mode
        self._sensitive_keys = frozenset(k.lower() for k in (sensitive_keys or ()))
        # ingest_url takes precedence over an explicit exporter; without
        # either, tracer.py falls back to ConsoleSpanExporter (local dev).
        if ingest_url is not None and exporter is None:
            exporter = HTTPSpanExporter(ingest_url)
        self._tracer = get_tracer(exporter=exporter)

    def instrument(self, compiled_graph: Any) -> "_InstrumentedGraph":
        """Wrap a compiled LangGraph app. The returned object is a
        transparent drop-in replacement — every method/attribute other than
        invoke/stream passes straight through to the original graph.

        Also wraps every real node's runnable in-place (Phase 2 §2) so the
        budget-gating check in _InstrumentedGraph runs literally inside the
        node's own execution — required for LangGraph's interrupt() to work
        at all; it cannot be done from a callback handler, which observes
        node execution but isn't part of the node's call stack. The caller
        must have compiled `compiled_graph` with a checkpointer for
        interrupt()/resume to function — TokenLens does not attach one."""
        instrumented = _InstrumentedGraph(compiled_graph, self)
        instrumented._wrap_nodes_for_budget_gate()
        return instrumented


class _InstrumentedGraph:
    def __init__(self, graph: Any, config: TokenLens) -> None:
        self._graph = graph
        self._config = config

    def _wrap_nodes_for_budget_gate(self) -> None:
        for node_name, node in self._graph.nodes.items():
            if node_name in _NON_WRAPPABLE_NODES:
                continue
            node.bound = RunnableLambda(
                self._budget_gate(node_name, node.bound), name=node_name
            )

    def _budget_gate(self, node_name: str, original_bound: Any):
        """Returns a (state, config) -> Any callable that checks the
        current run's cumulative gating spend BEFORE running the real node
        body, and calls interrupt() if it's already over the active cap.

        Deliberately a pre-check on the node about to run, not a post-check
        on the node that just finished: per LangGraph's own docs, resuming
        from an interrupt re-executes the whole node from the top. Gating
        at the end of the node that caused the breach would re-run that
        node's (possibly expensive) work a second time on resume. Gating at
        the *start* of the next node instead means the only thing that
        re-runs on resume is this cheap check."""

        def wrapped(state: Any, config: dict | None = None) -> Any:
            cfg = config or {}
            handler = self._find_handler(cfg)
            # Deliberately NOT handler.cumulative_cost_usd: that in-process
            # mirror resets to zero on every resume (a fresh handler per
            # invocation), which would let a resumed run skip re-validation
            # entirely — verified by direct testing. spend_ledger.py is the
            # durable, resume-safe source of truth for "how much has this
            # thread_id spent so far, ever."
            if handler is not None and handler.budget_cap_usd is not None:
                total_spend = spend_ledger.get_cumulative_spend(handler.run_id)
                if total_spend >= handler.budget_cap_usd:
                    interrupt(
                        {
                            "reason": "budget_exceeded",
                            "graph_name": self._config.graph_name,
                            "tenant_id": self._config.tenant_id,
                            "node": node_name,
                            "spend_so_far_usd": total_spend,
                            "cap_usd": handler.budget_cap_usd,
                        }
                    )
                    # Only reached after Command(resume=...) — the caller
                    # chose to resume despite the breach (e.g. raised the
                    # cap first) since this check would otherwise raise
                    # again on the very next pre-check.
            return original_bound.invoke(state, cfg)

        return wrapped

    @staticmethod
    def _find_handler(config: dict) -> TokenLensCallbackHandler | None:
        callbacks = config.get("callbacks")
        handlers = getattr(callbacks, "handlers", None) or []
        for h in handlers:
            if isinstance(h, TokenLensCallbackHandler):
                return h
        return None

    def _new_handler(self, run_id: str) -> TokenLensCallbackHandler:
        # Looked up fresh per invocation, not cached on TokenLens — an
        # Approve-and-raise-cap decision must take effect on the very next
        # resume, see tokenlens_sdk/budget.py's module docstring.
        cap = budget.get_active_per_run_cap(
            self._config.tenant_id, self._config.graph_name
        )
        return TokenLensCallbackHandler(
            tracer=self._config._tracer,
            run_id=run_id,
            graph_name=self._config.graph_name,
            tenant_id=self._config.tenant_id,
            sensitive_keys=self._config._sensitive_keys,
            payload_capture_mode=self._config.payload_capture_mode,
            budget_cap_usd=cap,
        )

    def _with_tokenlens_callback(self, config: dict | None) -> dict:
        merged = dict(config or {})
        configurable = dict(merged.get("configurable", {}))
        # thread_id = run_id (Phase 2 §0): reuse an explicitly-passed
        # thread_id (the resume path, targeting an already-interrupted run)
        # rather than always minting a new one, which is only correct for a
        # fresh run.
        run_id = configurable.get("thread_id") or uuid.uuid4().hex
        configurable["thread_id"] = run_id
        merged["configurable"] = configurable
        handler = self._new_handler(run_id)
        merged["callbacks"] = [*merged.get("callbacks", []), handler]
        return merged

    def invoke(self, state: Any, config: dict | None = None, **kwargs: Any) -> Any:
        return self._graph.invoke(
            state, config=self._with_tokenlens_callback(config), **kwargs
        )

    def stream(self, state: Any, config: dict | None = None, **kwargs: Any):
        return self._graph.stream(
            state, config=self._with_tokenlens_callback(config), **kwargs
        )

    def __getattr__(self, name: str) -> Any:
        # Transparent passthrough for anything not explicitly wrapped above
        # (get_graph(), ainvoke, astream, .nodes, etc.) — keeps `instrument()`
        # a true drop-in replacement rather than a partial reimplementation.
        return getattr(self._graph, name)
