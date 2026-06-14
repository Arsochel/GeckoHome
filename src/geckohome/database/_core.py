"""Shared async SQLite connection helpers."""

from contextlib import asynccontextmanager

import aiosqlite

from geckohome.paths import DB_PATH, MEDIA_DB_PATH

__all__ = ["DB_PATH", "MEDIA_DB_PATH", "_db", "_media_db"]


@asynccontextmanager
async def _db(write=False):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db
        if write:
            await db.commit()


@asynccontextmanager
async def _media_db(write=False):
    async with aiosqlite.connect(MEDIA_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db
        if write:
            await db.commit()
