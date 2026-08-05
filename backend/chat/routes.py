"""POST /v1/chat — Flow 1's customer-facing entry point (phase3.txt Phase
3A §4). Accepts a text message and an optional file upload (PDF/image),
invokes the dogfooded LangGraph app stored on `request.app.state.chat_graph`
(built once at startup — see main.py's lifespan), and returns the answer
plus token/cost/latency numbers.
"""

import uuid

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from agents import gateway

router = APIRouter()

# No auth/tenant system exists yet (phase3.txt Scope Decision) — a single
# fixed demo tenant is a deliberate, documented Phase 3A simplification,
# same spirit as slack_notify/resume.py's hard-coded _GRAPH_BUILDERS
# registry. Real multi-tenant chat is deferred to the frontend phase.
TENANT_ID = "tokenlens-chat-demo"
GRAPH_NAME = "customer_chat"


class ChatResponse(BaseModel):
    answer: str
    run_id: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float


@router.post("/v1/chat", response_model=ChatResponse)
async def chat(
    request: Request,
    message: str = Form(...),
    thread_id: str | None = Form(None),
    file: UploadFile | None = File(None),
) -> ChatResponse:
    file_bytes = await file.read() if file is not None else None
    file_mime_type = file.content_type if file is not None else None

    # thread_id == run_id (Phase 2 §0's one-ID convention). Minted here
    # rather than left to TokenLens's own auto-mint so the response can
    # always report which run_id this call landed under, whether it's a
    # fresh conversation or a resume of an existing thread_id.
    run_id = thread_id or uuid.uuid4().hex

    try:
        result = request.app.state.chat_graph.invoke(
            {
                "message": message,
                "file_bytes": file_bytes,
                "file_mime_type": file_mime_type,
            },
            config={"configurable": {"thread_id": run_id}},
        )
    except gateway.GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    meta = result["gateway_meta"]
    return ChatResponse(answer=result["answer"], run_id=run_id, **meta)
