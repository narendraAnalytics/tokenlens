"""Slack interactivity webhook (phase.txt Phase 2 §5): receives the
button-click payload from the approval card, verifies it's genuinely from
Slack, and resumes the interrupted run.

Signature verification is not optional — an unverified webhook is an open
spend-approval bypass (anyone who finds the URL could resume/approve any
run). Uses slack_sdk's SignatureVerifier rather than hand-rolling HMAC
comparison, per phase.txt Phase 2 §0's explicit research note.

Acks within Slack's 3-second window by doing signature verification and
payload parsing synchronously (fast, no I/O) but deferring the actual
resume/DB work to a FastAPI BackgroundTask — Slack sees a 200 immediately,
the run resumes shortly after.
"""

import json
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, Request, Response
from loguru import logger
from slack_sdk.signature import SignatureVerifier

from config import settings
from slack_notify import resume

router = APIRouter()


def _verify(raw_body: bytes, headers: dict[str, str]) -> bool:
    if not settings.slack_signing_secret:
        # Fail closed: no secret configured means no request can ever be
        # verified, so every request is rejected rather than silently
        # trusted. Matches send_approval_card()'s "missing config" case
        # being a soft no-op — but here, a soft no-op would mean SKIPPING
        # verification, which this module's docstring explicitly rules out.
        logger.warning("slack_notify.webhook: SLACK_SIGNING_SECRET not configured — rejecting")
        return False
    return SignatureVerifier(settings.slack_signing_secret).is_valid_request(raw_body, headers)


@router.post("/v1/slack/interactivity")
async def slack_interactivity(request: Request, background_tasks: BackgroundTasks) -> Response:
    raw_body = await request.body()

    if not _verify(raw_body, dict(request.headers)):
        logger.warning("slack_notify.webhook: rejected request with invalid/missing signature")
        return Response(status_code=401)

    # Slack sends this as application/x-www-form-urlencoded with a single
    # "payload" field holding a JSON string — parsed manually (rather than
    # via Starlette's request.form()) since the raw body was already
    # consumed above for signature verification.
    form = parse_qs(raw_body.decode("utf-8"))
    payload_values = form.get("payload")
    if not payload_values:
        logger.warning("slack_notify.webhook: request missing 'payload' form field")
        return Response(status_code=400)

    try:
        payload = json.loads(payload_values[0])
        action = payload["actions"][0]
        action_id = action["action_id"]
        value = action["value"]
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        logger.warning(f"slack_notify.webhook: malformed interactivity payload: {exc}")
        return Response(status_code=400)

    user = payload.get("user", {})
    decided_by = user.get("username") or user.get("id") or "unknown"

    background_tasks.add_task(resume.handle_decision, action_id, value, decided_by)

    # Ack now — resume.handle_decision runs after this response is sent.
    return Response(status_code=200)
