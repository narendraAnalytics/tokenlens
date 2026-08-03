"""TokenLens SDK — wraps a compiled LangGraph app to emit OTel GenAI spans.

Usage (the "three lines" the product spec promises):

    from tokenlens_sdk import TokenLens

    tl = TokenLens(graph_name="claims_pipeline", tenant_id="acme-co")
    app = tl.instrument(graph.compile())

`app` behaves exactly like the original compiled graph (`.invoke()`,
`.stream()`, and any other attribute pass straight through) — nothing else
in the customer's code needs to change.
"""

from tokenlens_sdk.client import TokenLens

__all__ = ["TokenLens"]
