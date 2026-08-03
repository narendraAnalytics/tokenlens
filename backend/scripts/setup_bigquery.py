"""Idempotently create the TokenLens BigQuery trace warehouse.

Creates the `tokenlens_traces.spans` dataset/table on tokenlens-504404 if
they don't already exist. Safe to re-run — `exists_ok=True` on both calls.

This is the single source of truth for the spans table schema; Phase 1 §5
(ingest pipeline) should import SPANS_SCHEMA from here rather than
redefining columns, so the warehouse schema and the writer never drift
apart.

Run: uv run python scripts/setup_bigquery.py
"""

from google.cloud import bigquery

PROJECT_ID = "tokenlens-504404"
DATASET_ID = "tokenlens_traces"
TABLE_ID = "spans"
LOCATION = "asia-south1"

# Column families mirror the SDK's two attribute namespaces (see
# backend/CLAUDE.md "Span attribute naming — OTel GenAI conventions"):
#   gen_ai_*   — derived from OpenTelemetry GenAI semantic-convention
#                attributes (gen_ai.*). Never add a TokenLens-only field here.
#   tokenlens_* — TokenLens-specific fields the OTel spec doesn't define.
SPANS_SCHEMA = [
    # --- identity / structure ---
    bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("span_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("parent_span_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("graph_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("node_name", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("timestamp", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("ingested_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("latency_ms", "FLOAT64", mode="REQUIRED"),
    # --- gen_ai.* (OTel GenAI semantic conventions) ---
    bigquery.SchemaField("gen_ai_operation_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("gen_ai_system", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("gen_ai_request_model", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("gen_ai_response_model", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("gen_ai_usage_input_tokens", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("gen_ai_usage_output_tokens", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("gen_ai_agent_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("gen_ai_agent_type", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("gen_ai_tool_name", "STRING", mode="REPEATED"),
    # --- tokenlens.* (custom — cost, cache, retries, tenancy, confidence) ---
    bigquery.SchemaField("tokenlens_cost", "NUMERIC", mode="NULLABLE"),
    bigquery.SchemaField("tokenlens_cached_tokens", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("tokenlens_retry_count", "INT64", mode="NULLABLE"),
    bigquery.SchemaField("tokenlens_tenant_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("tokenlens_confidence", "FLOAT64", mode="NULLABLE"),
    # "full" or "metadata_only" — recorded per-span (not just looked up from
    # tenant config) so replay eligibility is queryable directly even if the
    # tenant's capture mode changes later. See backend/CLAUDE.md PII policy.
    bigquery.SchemaField("tokenlens_payload_capture_mode", "STRING", mode="REQUIRED"),
    # Full node input/output state, after SDK-side redaction. NULL when
    # capture mode is "metadata_only" for this span.
    bigquery.SchemaField("tokenlens_payload_redacted", "JSON", mode="NULLABLE"),
]


def main() -> None:
    client = bigquery.Client(project=PROJECT_ID)

    dataset = bigquery.Dataset(f"{PROJECT_ID}.{DATASET_ID}")
    dataset.location = LOCATION
    dataset = client.create_dataset(dataset, exists_ok=True)
    print(f"Dataset ready: {dataset.dataset_id} ({dataset.location})")

    table = bigquery.Table(
        f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}", schema=SPANS_SCHEMA
    )
    # Day-partitioned on event time (not ingestion time) so a late-arriving
    # span still lands in the partition it actually happened in.
    table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="timestamp",
    )
    # Clustered on tenant first (per phase.txt), run_id second — the two
    # dominant query patterns are "this tenant's spend/usage" and "every
    # span in this run" (trace replay, incident debugging).
    table.clustering_fields = ["tokenlens_tenant_id", "run_id"]
    table = client.create_table(table, exists_ok=True)
    print(f"Table ready: {table.project}.{table.dataset_id}.{table.table_id}")


if __name__ == "__main__":
    main()
