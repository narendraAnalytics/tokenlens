"""Thin publisher wrapper used by the ingest routes."""

import json
from functools import lru_cache

from google.cloud import pubsub_v1

from config import settings


@lru_cache(maxsize=1)
def _publisher() -> pubsub_v1.PublisherClient:
    return pubsub_v1.PublisherClient()


@lru_cache(maxsize=1)
def _topic_path() -> str:
    return _publisher().topic_path(settings.gcp_project_id, settings.pubsub_topic_traces)


def publish_span(span: dict) -> None:
    """Fire-and-forget publish — matches the async telemetry path contract
    in backend/CLAUDE.md (must never block the caller on delivery).
    `.result()` is intentionally not called here; publish failures surface
    via the client library's own retry/logging, not by blocking the
    request."""
    data = json.dumps(span).encode("utf-8")
    _publisher().publish(_topic_path(), data)
