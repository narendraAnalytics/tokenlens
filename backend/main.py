from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres import PostgresSaver

from agents.register_all import register_all
from chat.graph import build_graph
from chat.routes import GRAPH_NAME, TENANT_ID
from chat.routes import router as chat_router
from config import settings
from ingest.routes import router as ingest_router
from investigate.routes import router as investigate_router
from slack_notify.webhook import router as slack_webhook_router
from tokenlens_sdk import TokenLens

# PostgresSaver wants a plain libpq conn string, not SQLAlchemy's
# "postgresql+psycopg://" dialect prefix (same conversion examples/
# budget_breach_probe.py already does for its own one-shot use).
_CHECKPOINTER_CONN_STRING = settings.database_url.replace(
    "postgresql+psycopg://", "postgresql://"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Opens the PostgresSaver ONCE for the process lifetime (verified safe
    via examples/checkpointer_lifespan_probe.py — Flow 1 is the first
    long-lived use of PostgresSaver; every prior use was a short-lived
    `with` block inside a one-shot script) and builds the dogfooded chat
    graph once, not per-request."""
    with PostgresSaver.from_conn_string(_CHECKPOINTER_CONN_STRING) as checkpointer:
        checkpointer.setup()
        compiled = build_graph().compile(checkpointer=checkpointer)
        tl = TokenLens(
            graph_name=GRAPH_NAME,
            tenant_id=TENANT_ID,
            # Raw file/image bytes are redacted rather than captured in
            # full — they'd otherwise bloat the ingested span payload with
            # a str()-ified binary blob for no analytical benefit (the
            # extracted_text/answer fields already carry what matters).
            sensitive_keys={"file_bytes", "image_bytes"},
            ingest_url=f"http://localhost:{settings.api_port}/v1/traces",
        )
        app.state.chat_graph = tl.instrument(compiled)
        register_all()
        yield


app = FastAPI(title="TokenLens ingest API", lifespan=lifespan)
app.include_router(ingest_router)
app.include_router(slack_webhook_router)
app.include_router(chat_router)
app.include_router(investigate_router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


def main() -> None:
    print("Hello from backend!")


if __name__ == "__main__":
    main()
