"""Pub/Sub -> BigQuery worker: pulls spans off tokenlens-traces-worker,
computes tokenlens_cost from the pricing table, and streams the row into
tokenlens_traces.spans. Cost is computed here, never in the SDK or the
ingest API — see backend/CLAUDE.md.

A message that fails to insert is nacked, so Pub/Sub redelivers it (up to
the subscription's max_delivery_attempts, set in setup_pubsub.py) before it
lands on the dead-letter topic instead of being silently dropped.

Run:
  uv run python scripts/bigquery_worker.py             # continuous
  uv run python scripts/bigquery_worker.py --once       # drain once, exit
"""

import argparse
import json
from datetime import datetime, timezone

from google.cloud import bigquery, pubsub_v1

from config import settings
from ingest.pricing import compute_cost

TABLE_ID = "spans"


def _process_message(bq_client: bigquery.Client, data: bytes) -> None:
    row = json.loads(data.decode("utf-8"))
    row["ingested_at"] = datetime.now(timezone.utc).isoformat()
    row["tokenlens_cost"] = compute_cost(
        row.get("gen_ai_response_model") or row.get("gen_ai_request_model"),
        row.get("gen_ai_usage_input_tokens", 0),
        row.get("gen_ai_usage_output_tokens", 0),
    )
    table_ref = f"{settings.gcp_project_id}.{settings.bigquery_dataset}.{TABLE_ID}"
    errors = bq_client.insert_rows_json(table_ref, [row])
    if errors:
        raise RuntimeError(f"BigQuery insert failed: {errors}")


def run(once: bool) -> None:
    subscriber = pubsub_v1.SubscriberClient()
    bq_client = bigquery.Client(project=settings.gcp_project_id)
    subscription_path = subscriber.subscription_path(
        settings.gcp_project_id, settings.pubsub_subscription_traces
    )

    while True:
        response = subscriber.pull(
            request={"subscription": subscription_path, "max_messages": 20},
            timeout=10,
        )
        if not response.received_messages:
            if once:
                print("No messages available — exiting (--once).")
                return
            continue

        ack_ids: list[str] = []
        for received in response.received_messages:
            try:
                _process_message(bq_client, received.message.data)
                ack_ids.append(received.ack_id)
                print(f"Inserted 1 row from message {received.message.message_id}")
            except Exception as exc:  # noqa: BLE001 — nack and move on
                print(f"Failed to process message {received.message.message_id}: {exc}")
                subscriber.modify_ack_deadline(
                    request={
                        "subscription": subscription_path,
                        "ack_ids": [received.ack_id],
                        "ack_deadline_seconds": 0,
                    }
                )

        if ack_ids:
            subscriber.acknowledge(
                request={"subscription": subscription_path, "ack_ids": ack_ids}
            )

        if once:
            return


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="drain once and exit")
    args = parser.parse_args()
    run(once=args.once)
