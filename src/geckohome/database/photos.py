"""Photo BLOB storage (separate media database)."""

import logging

from geckohome.database._core import _media_db

log = logging.getLogger(__name__)


# ── Photos (media db) ──

async def save_photo(data: bytes, source: str = "web", caption: str = None) -> int:
    async with _media_db(write=True) as db:
        cur = await db.execute(
            "INSERT INTO photos (data, source, caption) VALUES (?,?,?)", (data, source, caption)
        )
        return cur.lastrowid


async def get_photos(limit: int = 20, offset: int = 0) -> list[dict]:
    async with _media_db() as db:
        async with db.execute(
            "SELECT id, taken_at, source, caption FROM photos ORDER BY taken_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_photo_data(photo_id: int) -> bytes | None:
    async with _media_db() as db:
        async with db.execute("SELECT data FROM photos WHERE id = ?", (photo_id,)) as cur:
            row = await cur.fetchone()
            return bytes(row["data"]) if row else None


async def delete_photo(photo_id: int):
    async with _media_db(write=True) as db:
        await db.execute("DELETE FROM photos WHERE id = ?", (photo_id,))


async def purge_old_photos():
    async with _media_db(write=True) as db:
        cur = await db.execute("DELETE FROM photos WHERE taken_at < datetime('now', '-1 hour')")
        if cur.rowcount:
            log.info("purged %d photos older than 1h", cur.rowcount)


