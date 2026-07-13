"""Shared async SQLite connection helpers."""

from contextlib import asynccontextmanager

import aiosqlite

from geckohome.paths import DB_PATH, MEDIA_DB_PATH

__all__ = ["DB_PATH", "MEDIA_DB_PATH", "_db", "_media_db"]


async def _configure(db: aiosqlite.Connection):
    """Веб и бот — два процесса на одних файлах: WAL разрешает читателям
    не блокироваться о писателя, busy_timeout ждёт вместо 'database is locked'."""
    # курсоры закрываем сразу — незакрытые statements блокируют VACUUM
    await (await db.execute("PRAGMA journal_mode=WAL")).close()
    await (await db.execute("PRAGMA busy_timeout=5000")).close()
    db.row_factory = aiosqlite.Row


@asynccontextmanager
async def _db(write=False):
    async with aiosqlite.connect(DB_PATH) as db:
        await _configure(db)
        yield db
        if write:
            await db.commit()


@asynccontextmanager
async def _media_db(write=False):
    async with aiosqlite.connect(MEDIA_DB_PATH) as db:
        await _configure(db)
        yield db
        if write:
            await db.commit()
