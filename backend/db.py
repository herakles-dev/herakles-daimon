"""
Async database connection module for Herakles Play.

Uses asyncpg connection pooling. Schema is applied on first boot
via docker-entrypoint-initdb.d (schema.sql mounted in docker-compose).
The get_pool() helper is safe to call multiple times — it returns the
cached pool after the first initialisation.
"""

import asyncpg
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

DATABASE_URL = os.environ["DATABASE_URL"]  # Required — set in .env


async def get_pool() -> asyncpg.Pool:
    """Return the shared connection pool, creating it on first call."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
    return _pool


async def close_pool() -> None:
    """Gracefully close the connection pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def execute(query: str, *args) -> str:
    """Execute a write query (INSERT / UPDATE / DELETE). Returns status string."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(query, *args)


async def fetch_one(query: str, *args) -> asyncpg.Record | None:
    """Fetch a single row, or None if no rows match."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(query, *args)


async def fetch_all(query: str, *args) -> list[asyncpg.Record]:
    """Fetch all matching rows."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(query, *args)


async def ensure_schema() -> None:
    """
    Apply schema.sql if the core tables are not yet present.

    This is a safety net for local dev runs without Docker init scripts.
    In production the schema is applied by docker-entrypoint-initdb.d.

    After the base schema is guaranteed, also applies migration 002
    (music tables) when the 'tracks' table does not yet exist.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        videos_exist = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'videos')"
        )
        if not videos_exist:
            schema_path = Path(__file__).parent / "schema.sql"
            sql = schema_path.read_text()
            await conn.execute(sql)
            # schema.sql now includes music tables, so we are done.
            return

        # Base schema already present — check whether music tables need
        # to be applied (e.g. existing deployment being upgraded).
        tracks_exist = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'tracks')"
        )
        if not tracks_exist:
            migration_path = Path(__file__).parent / "migrations" / "002_music_tables.sql"
            sql = migration_path.read_text()
            await conn.execute(sql)

        # Check if migration 003 remediation has been applied
        # Simple check: does the idx_tracks_title_trgm index exist?
        has_trgm = await conn.fetchrow(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'idx_tracks_title_trgm'"
        )
        if not has_trgm:
            migration_003 = (Path(__file__).parent / "migrations" / "003_remediation.sql").read_text()
            await conn.execute(migration_003)
            logger.info("Applied migration 003: remediation indexes and constraints")
