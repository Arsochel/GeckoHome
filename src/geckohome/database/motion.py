"""Motion detection events."""

from datetime import datetime, timedelta

from geckohome.database._core import _db

# ── Motion events ──

async def add_motion_event(photo_file_id: str, caption: str) -> int:
    async with _db(write=True) as db:
        cur = await db.execute(
            "INSERT INTO motion_events (photo_file_id, caption) VALUES (?,?)",
            (photo_file_id, caption),
        )
        return cur.lastrowid


async def get_motion_event(event_id: int) -> dict | None:
    async with _db() as db:
        async with db.execute("SELECT * FROM motion_events WHERE id = ?", (event_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_motion_events_24h_count() -> int:
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    async with _db() as db:
        async with db.execute(
            "SELECT COUNT(*) AS n FROM motion_events WHERE created_at >= ?",
            (cutoff,),
        ) as cur:
            row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def get_recent_motion_events(limit: int = 20) -> list[dict]:
    async with _db() as db:
        async with db.execute(
            "SELECT created_at, caption, photo_file_id, status FROM motion_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    out = []
    for r in rows:
        ts = None
        try:
            ts = datetime.fromisoformat(r["created_at"]).timestamp()
        except (TypeError, ValueError):
            pass
        out.append({
            "ts": ts,
            "caption": r["caption"],
            "photo_file_id": r["photo_file_id"],
            "status": r["status"],
        })
    return out


async def update_motion_status(event_id: int, status: str):
    async with _db(write=True) as db:
        await db.execute("UPDATE motion_events SET status = ? WHERE id = ?", (status, event_id))


async def update_motion_photo(event_id: int, photo_file_id: str):
    async with _db(write=True) as db:
        await db.execute("UPDATE motion_events SET photo_file_id = ? WHERE id = ?", (photo_file_id, event_id))


