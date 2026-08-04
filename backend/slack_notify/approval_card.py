"""Slack approval card (phase.txt Phase 2 §4). Builds and sends the Block
Kit card a human uses to Approve / Kill / Approve-and-raise-cap a halted
run. Called synchronously from tokenlens_sdk/client.py's `_budget_gate`,
right before `interrupt()` fires — same call site as the Runtime Context
Agent (agents/runtime_context.py), whose 3-line summary this card displays.

Button clicks are handled by webhook.py (phase.txt Phase 2 §5) — this
module only builds and sends the message.
"""

import functools
import json

from loguru import logger
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from config import settings

# Block Kit JSON validated via Slack's public blocks.validate endpoint
# (no auth required) before being wired in here.


@functools.lru_cache(maxsize=1)
def _get_client() -> WebClient:
    return WebClient(token=settings.slack_bot_token)


def _build_blocks(payload: dict) -> tuple[str, list[dict]]:
    """Returns (fallback_text, blocks). fallback_text is what notifications
    and screen readers show instead of the blocks, per Slack's Block Kit
    guidance — summarizes what the card conveys, not left empty."""
    graph_name = payload["graph_name"]
    tenant_id = payload["tenant_id"]
    node = payload["node"]
    thread_id = payload["thread_id"]
    spend = payload["spend_so_far_usd"]
    cap = payload["cap_usd"]
    summary = payload["summary"]

    fallback_text = (
        f"Budget breach on {graph_name} ({tenant_id}) at node '{node}' — "
        f"${spend:.4f} / ${cap:.4f} cap. Approval needed."
    )

    # Button `value` carries everything webhook.py needs to resume without
    # a second DB round-trip: which graph/tenant/thread to resume, and the
    # spend/cap at decision time for the audit_log row. A JSON string, not
    # a bare thread_id — well under Slack's ~2000-char value limit.
    button_value = json.dumps(
        {
            "thread_id": thread_id,
            "graph_name": graph_name,
            "tenant_id": tenant_id,
            "spend_so_far_usd": spend,
            "cap_usd": cap,
        }
    )

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🚨 Budget breach — approval needed"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Graph:* {graph_name}\n"
                    f"*Tenant:* {tenant_id}\n"
                    f"*Halted at node:* {node}"
                ),
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": summary},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Spend: ${spend:.4f} / Cap: ${cap:.4f}  •  Run: `{thread_id}`",
                }
            ],
        },
        {"type": "divider"},
        {
            "type": "actions",
            "block_id": "budget_approval_actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": "approve_run",
                    "value": button_value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve & Raise Cap"},
                    "action_id": "approve_raise_cap",
                    "value": button_value,
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Raise budget cap?"},
                        "text": {
                            "type": "plain_text",
                            "text": (
                                "This raises the per-run cap for this "
                                "tenant/graph and lets the run continue "
                                "past its current budget."
                            ),
                        },
                        "confirm": {"type": "plain_text", "text": "Raise cap & approve"},
                        "deny": {"type": "plain_text", "text": "Cancel"},
                        "style": "primary",
                    },
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Kill"},
                    "style": "danger",
                    "action_id": "kill_run",
                    "value": button_value,
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Kill this run?"},
                        "text": {
                            "type": "plain_text",
                            "text": "This leaves the run interrupted permanently. It will not resume.",
                        },
                        "confirm": {"type": "plain_text", "text": "Kill run"},
                        "deny": {"type": "plain_text", "text": "Cancel"},
                        "style": "danger",
                    },
                },
            ],
        },
    ]
    return fallback_text, blocks


def send_approval_card(payload: dict) -> None:
    """Sends the approval card via chat.postMessage. Never raises — a
    Slack outage or missing config must not fail the budget gate that
    calls this right before `interrupt()`; the run still pauses correctly
    either way, a human just won't get a Slack notification this time."""
    if not settings.slack_bot_token or not settings.slack_approval_channel:
        logger.warning(
            "slack_notify.approval_card: SLACK_BOT_TOKEN/SLACK_APPROVAL_CHANNEL "
            "not configured — skipping approval card send"
        )
        return

    fallback_text, blocks = _build_blocks(payload)
    try:
        _get_client().chat_postMessage(
            channel=settings.slack_approval_channel,
            text=fallback_text,
            blocks=blocks,
        )
    except SlackApiError as exc:
        logger.warning(f"slack_notify.approval_card: send failed, continuing: {exc}")
