"""OTel tracer setup for the TokenLens SDK.

Phase 1 §4 (this step) only needs spans to be constructed correctly in OTel
GenAI format and exported somewhere inspectable — a ConsoleSpanExporter is
the default and is enough to verify span shape locally. Phase 1 §5 (ingest
pipeline) will point this at an OTLP/HTTP exporter targeting the FastAPI
ingest endpoint instead; nothing in handler.py or client.py needs to change
when that happens, since they only depend on `get_tracer()`.
"""

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)

_provider: TracerProvider | None = None


def _get_provider(exporter: SpanExporter | None) -> TracerProvider:
    global _provider
    if _provider is None:
        _provider = TracerProvider(
            resource=Resource.create({"service.name": "tokenlens-sdk"})
        )
        _provider.add_span_processor(
            SimpleSpanProcessor(exporter or ConsoleSpanExporter())
        )
        trace.set_tracer_provider(_provider)
    return _provider


def get_tracer(exporter: SpanExporter | None = None) -> trace.Tracer:
    """Return the TokenLens tracer, initializing the global provider on
    first call. `exporter` is only honored on the first call in a process —
    later calls reuse the already-configured provider, matching how OTel's
    global provider is meant to be set up once per process."""
    _get_provider(exporter)
    return trace.get_tracer("tokenlens_sdk")
