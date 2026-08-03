"""Shared SQLAlchemy engine for the Cloud SQL control store (Phase 2 §1).
One engine per process, created lazily and cached — engines pool
connections internally, so callers should never construct their own."""

from functools import lru_cache

import sqlalchemy as sa

from config import settings


@lru_cache(maxsize=1)
def get_engine() -> sa.engine.Engine:
    return sa.create_engine(settings.database_url, pool_pre_ping=True)
