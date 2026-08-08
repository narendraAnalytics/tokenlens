"""POST /v1/investigate -- Phase 3B's entry point for asking the 5
specialist agents a question about ALREADY-COLLECTED telemetry (phase3.txt
Phase 3B §6). Mirrors chat/routes.py's shape: same fixed demo tenant_id
simplification (no auth/tenant system exists yet, documented in
backend/CLAUDE.md's Agent Platform Foundation section), same "curl against
a running server" verification idiom.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents import gateway
from investigate.orchestrator import investigate

router = APIRouter()


class InvestigateRequest(BaseModel):
    message: str
    run_id: str | None = None
    tenant_id: str = "tokenlens-chat-demo"


class InvestigateResponse(BaseModel):
    answer: str
    routed_to: list[str]


@router.post("/v1/investigate", response_model=InvestigateResponse)
def investigate_endpoint(request: InvestigateRequest) -> InvestigateResponse:
    try:
        result = investigate(
            user_message=request.message,
            run_id=request.run_id,
            tenant_id=request.tenant_id,
        )
    except gateway.GatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return InvestigateResponse(**result)
