"""
Postgres storage for the bot (asyncpg).
Works with Neon, Supabase or any Postgres. Set DATABASE_URL to the connection string.
"""
import asyncio
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

SCHEMA = """
CREATE TABLE IF NOT EXISTS confessions(
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    full_name TEXT,
    username TEXT,
    text TEXT NOT NULL,
    tags TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    channel_msg_id BIGINT,
    created_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS blocked(user_id BIGINT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS accepted(user_id BIGINT PRIMARY KEY);
"""


def _clean_dsn(dsn: str) -> str:
    """asyncpg doesn't understand some libpq options (e.g. channel_binding)."""
    parts = urlsplit(dsn.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "channel_binding"]
    return urlunsplit(parts._replace(query=urlencode(query)))


class Database:
    def __init__(self):
        self.pool: asyncpg.Pool | None = None

    async def connect(self, dsn: str):
        self.pool = await asyncpg.create_pool(
            _clean_dsn(dsn),
            min_size=1,
            max_size=5,
            command_timeout=30,
            statement_cache_size=0,               # safe with poolers (pgbouncer)
            max_inactive_connection_lifetime=30,  # drop idle conns before the server does
        )
        async with self.pool.acquire() as conn:
            await conn.execute(SCHEMA)
            # items left "processing" by a crash go back to the queue
            await conn.execute("UPDATE confessions SET status='pending' WHERE status='processing'")

    async def _call(self, method: str, *args):
        """Run a pool method, retrying once on a dropped connection."""
        for attempt in (1, 2):
            try:
                return await getattr(self.pool, method)(*args)
            except (asyncpg.PostgresConnectionError, asyncpg.InterfaceError, ConnectionError, OSError):
                if attempt == 2:
                    raise
                await asyncio.sleep(0.5)

    # ---- users
    async def is_blocked(self, user_id: int) -> bool:
        return await self._call("fetchval", "SELECT 1 FROM blocked WHERE user_id=$1", user_id) is not None

    async def block(self, user_id: int):
        await self._call("execute", "INSERT INTO blocked(user_id) VALUES($1) ON CONFLICT DO NOTHING", user_id)

    async def has_accepted(self, user_id: int) -> bool:
        return await self._call("fetchval", "SELECT 1 FROM accepted WHERE user_id=$1", user_id) is not None

    async def accept(self, user_id: int):
        await self._call("execute", "INSERT INTO accepted(user_id) VALUES($1) ON CONFLICT DO NOTHING", user_id)

    # ---- news
    async def add_news(self, user_id: int, full_name: str, username, text: str, tags: str) -> int:
        return await self._call(
            "fetchval",
            "INSERT INTO confessions(user_id, full_name, username, text, tags, created_at) "
            "VALUES($1,$2,$3,$4,$5,$6) RETURNING id",
            user_id, full_name, username, text, tags, int(time.time()),
        )

    async def claim(self, cid: int):
        """Atomically take a pending item so two admins can't handle it twice."""
        return await self._call(
            "fetchrow",
            "UPDATE confessions SET status='processing' WHERE id=$1 AND status='pending' RETURNING *",
            cid,
        )

    async def set_status(self, cid: int, status: str, channel_msg_id=None):
        await self._call(
            "execute",
            "UPDATE confessions SET status=$2, channel_msg_id=COALESCE($3::bigint, channel_msg_id) WHERE id=$1",
            cid, status, channel_msg_id,
        )
