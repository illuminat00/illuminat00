"""
Storage for the bot.

- If DATABASE_URL is set -> Postgres (Neon, Supabase, ...) through asyncpg
- Otherwise              -> a SQLite file (DB_PATH). Use this on hosts with a persistent disk.
"""
import asyncio
import os
import sqlite3
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PG_SCHEMA = """
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

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS confessions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    full_name TEXT,
    username TEXT,
    text TEXT NOT NULL,
    tags TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    channel_msg_id INTEGER,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS blocked(user_id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS accepted(user_id INTEGER PRIMARY KEY);
"""


def _clean_dsn(dsn: str) -> str:
    """asyncpg doesn't understand some libpq options (e.g. channel_binding)."""
    parts = urlsplit(dsn.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "channel_binding"]
    return urlunsplit(parts._replace(query=urlencode(query)))


class Database:
    def __init__(self):
        self.pool: Any = None                        # Postgres pool
        self.lite: sqlite3.Connection | None = None  # SQLite connection
        self._pg_errors: tuple = ()

    @property
    def backend(self) -> str:
        return "sqlite" if self.lite is not None else "postgres"

    async def connect(self, dsn: str = "", sqlite_path: str = "confessions.db"):
        if dsn:
            import asyncpg

            self._pg_errors = (
                asyncpg.PostgresConnectionError,
                asyncpg.InterfaceError,
                ConnectionError,
                OSError,
            )
            self.pool = await asyncpg.create_pool(
                _clean_dsn(dsn),
                min_size=1,
                max_size=5,
                command_timeout=30,
                statement_cache_size=0,               # safe with poolers (pgbouncer)
                max_inactive_connection_lifetime=30,  # drop idle conns before the server does
            )
            async with self.pool.acquire() as conn:
                await conn.execute(PG_SCHEMA)
                # items left "processing" by a crash go back to the queue
                await conn.execute("UPDATE confessions SET status='pending' WHERE status='processing'")
        else:
            folder = os.path.dirname(os.path.abspath(sqlite_path))
            os.makedirs(folder, exist_ok=True)
            self.lite = sqlite3.connect(sqlite_path, check_same_thread=False)
            self.lite.row_factory = sqlite3.Row
            self.lite.executescript(SQLITE_SCHEMA)
            self.lite.execute("UPDATE confessions SET status='pending' WHERE status='processing'")
            self.lite.commit()

    # ---- helpers
    async def _call(self, method: str, *args):
        """Run a Postgres pool method, retrying once on a dropped connection."""
        for attempt in (1, 2):
            try:
                return await getattr(self.pool, method)(*args)
            except self._pg_errors:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.5)

    def _run(self, sql: str, params: tuple = ()):
        cur = self.lite.execute(sql, params)
        self.lite.commit()
        return cur

    # ---- users
    async def is_blocked(self, user_id: int) -> bool:
        if self.lite is not None:
            return self.lite.execute("SELECT 1 FROM blocked WHERE user_id=?", (user_id,)).fetchone() is not None
        return await self._call("fetchval", "SELECT 1 FROM blocked WHERE user_id=$1", user_id) is not None

    async def block(self, user_id: int):
        if self.lite is not None:
            self._run("INSERT OR IGNORE INTO blocked(user_id) VALUES(?)", (user_id,))
            return
        await self._call("execute", "INSERT INTO blocked(user_id) VALUES($1) ON CONFLICT DO NOTHING", user_id)

    async def has_accepted(self, user_id: int) -> bool:
        if self.lite is not None:
            return self.lite.execute("SELECT 1 FROM accepted WHERE user_id=?", (user_id,)).fetchone() is not None
        return await self._call("fetchval", "SELECT 1 FROM accepted WHERE user_id=$1", user_id) is not None

    async def accept(self, user_id: int):
        if self.lite is not None:
            self._run("INSERT OR IGNORE INTO accepted(user_id) VALUES(?)", (user_id,))
            return
        await self._call("execute", "INSERT INTO accepted(user_id) VALUES($1) ON CONFLICT DO NOTHING", user_id)

    # ---- news
    async def add_news(self, user_id: int, full_name: str, username, text: str, tags: str) -> int:
        now = int(time.time())
        if self.lite is not None:
            cur = self._run(
                "INSERT INTO confessions(user_id, full_name, username, text, tags, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (user_id, full_name, username, text, tags, now),
            )
            return cur.lastrowid
        return await self._call(
            "fetchval",
            "INSERT INTO confessions(user_id, full_name, username, text, tags, created_at) "
            "VALUES($1,$2,$3,$4,$5,$6) RETURNING id",
            user_id, full_name, username, text, tags, now,
        )

    async def claim(self, cid: int):
        """Atomically take a pending item so two admins can't handle it twice."""
        if self.lite is not None:
            cur = self._run(
                "UPDATE confessions SET status='processing' WHERE id=? AND status='pending'", (cid,)
            )
            if cur.rowcount != 1:
                return None
            return self.lite.execute("SELECT * FROM confessions WHERE id=?", (cid,)).fetchone()
        return await self._call(
            "fetchrow",
            "UPDATE confessions SET status='processing' WHERE id=$1 AND status='pending' RETURNING *",
            cid,
        )

    async def set_status(self, cid: int, status: str, channel_msg_id=None):
        if self.lite is not None:
            self._run(
                "UPDATE confessions SET status=?, channel_msg_id=COALESCE(?, channel_msg_id) WHERE id=?",
                (status, channel_msg_id, cid),
            )
            return
        await self._call(
            "execute",
            "UPDATE confessions SET status=$2, channel_msg_id=COALESCE($3::bigint, channel_msg_id) WHERE id=$1",
            cid, status, channel_msg_id,
        )
