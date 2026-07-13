"""Настройки соединений SQLite: WAL и busy_timeout на обеих базах."""

from geckohome.database._core import _db, _media_db


async def test_main_db_uses_wal():
    async with _db() as db:
        async with db.execute("PRAGMA journal_mode") as cur:
            row = await cur.fetchone()
    assert row[0] == "wal"


async def test_media_db_uses_wal():
    async with _media_db() as db:
        async with db.execute("PRAGMA journal_mode") as cur:
            row = await cur.fetchone()
    assert row[0] == "wal"


async def test_busy_timeout_is_set():
    async with _db() as db:
        async with db.execute("PRAGMA busy_timeout") as cur:
            row = await cur.fetchone()
    assert row[0] == 5000
