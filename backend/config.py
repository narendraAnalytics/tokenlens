"""Shared settings, loaded from backend/.env. Single source of truth for
config used by main.py, the ingest routes, and the standalone worker
script — don't read os.environ directly elsewhere."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    gcp_project_id: str = "tokenlens-504404"
    gcp_region: str = "asia-south1"

    bigquery_dataset: str = "tokenlens_traces"

    pubsub_topic_traces: str = "tokenlens-traces"
    pubsub_dlq_topic: str = "tokenlens-traces-dlq"
    pubsub_subscription_traces: str = "tokenlens-traces-worker"

    database_url: str = (
        "postgresql+psycopg://tokenlens:tokenlens@127.0.0.1:5432/tokenlens_control"
    )


settings = Settings()
