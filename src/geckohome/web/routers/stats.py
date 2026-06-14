from datetime import datetime, timedelta, date as date_type
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import aiosqlite

from geckohome import paths
from geckohome.database import DB_PATH, get_cricket_remaining
from geckohome.web.routers.auth import get_current_user

router = APIRouter()
templates = Jinja2Templates(directory=paths.TEMPLATES_DIR)

_auth = Depends(get_current_user)


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request, _=_auth):
    return templates.TemplateResponse(request, "stats.html", {})


@router.get("/api/stats/summary")
async def stats_summary(_=_auth):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cur = await db.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(crickets), 0) as total_c,"
            " COALESCE(SUM(vitamins), 0) as vit_cnt, COALESCE(SUM(hornworm), 0) as horn_cnt"
            " FROM feedings"
        )
        r = await cur.fetchone()
        total_feedings = int(r["cnt"])
        total_crickets = int(r["total_c"])
        vitamins_count = int(r["vit_cnt"])
        hornworm_count = int(r["horn_cnt"])

        cur = await db.execute("SELECT fed_at FROM feedings ORDER BY fed_at DESC LIMIT 1")
        r = await cur.fetchone()
        last_feeding = r["fed_at"][:10] if r else None

        cur = await db.execute("SELECT fed_at FROM feedings ORDER BY fed_at ASC LIMIT 1")
        r = await cur.fetchone()
        first_feeding = r["fed_at"][:10] if r else None

        cur = await db.execute("SELECT fed_at FROM feedings ORDER BY fed_at")
        rows = await cur.fetchall()
        dates = [datetime.fromisoformat(r["fed_at"]) for r in rows]
        avg_interval = None
        if len(dates) >= 2:
            intervals = [(dates[i + 1] - dates[i]).total_seconds() / 86400 for i in range(len(dates) - 1)]
            avg_interval = round(sum(intervals) / len(intervals), 1)

        cur = await db.execute(
            "SELECT AVG(crickets) as avg FROM feedings WHERE crickets IS NOT NULL AND crickets > 0"
        )
        r = await cur.fetchone()
        avg_crickets = round(float(r["avg"]), 1) if r["avg"] else None

        days_since = None
        if last_feeding:
            days_since = (datetime.now().date() - date_type.fromisoformat(last_feeding)).days

        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        cur = await db.execute(
            "SELECT COUNT(*) as cnt FROM motion_events WHERE created_at >= ?", (cutoff,)
        )
        r = await cur.fetchone()
        motion_24h = int(r["cnt"])

        cur = await db.execute(
            "SELECT COALESCE(SUM(count), 0) as total FROM cricket_batches WHERE count > 0"
        )
        r = await cur.fetchone()
        total_bought = int(r["total"])

    remaining = await get_cricket_remaining()

    return {
        "feedings_total": total_feedings,
        "days_since_last_feeding": days_since,
        "avg_feeding_interval": avg_interval,
        "avg_crickets": avg_crickets,
        "crickets_total_fed": total_crickets,
        "crickets_remaining": remaining,
        "vitamins_count": vitamins_count,
        "hornworm_count": hornworm_count,
        "motion_24h": motion_24h,
        "last_feeding": last_feeding,
        "first_feeding": first_feeding,
        "total_bought": total_bought,
    }


@router.get("/api/stats/feedings")
async def stats_feedings(_=_auth):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT fed_at, crickets, vitamins, hornworm FROM feedings ORDER BY fed_at"
        )
        rows = await cur.fetchall()
    return [
        {
            "date": r["fed_at"][:10],
            "crickets": r["crickets"],
            "vitamins": bool(r["vitamins"]),
            "hornworm": bool(r["hornworm"]),
        }
        for r in rows
    ]


@router.get("/api/stats/cricket")
async def stats_cricket(_=_auth):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT bought_at, count, COALESCE(deaths, 0) as deaths FROM cricket_batches WHERE count > 0 ORDER BY bought_at"
        )
        batches = [{"date": r["bought_at"][:10], "count": max(0, int(r["count"]) - int(r["deaths"]))} for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT DATE(fed_at, 'localtime') as day, SUM(crickets) as eaten"
            " FROM feedings WHERE crickets IS NOT NULL GROUP BY day ORDER BY day"
        )
        feedings_by_day = {r["day"]: int(r["eaten"] or 0) for r in await cur.fetchall()}

    if not batches:
        return []

    start = date_type.fromisoformat(batches[0]["date"])
    end = date_type.today()
    result = []
    current_day = start

    while current_day <= end:
        day_str = current_day.isoformat()
        current_batch = None
        for b in batches:
            if b["date"] <= day_str:
                current_batch = b
        if current_batch:
            eaten = sum(v for k, v in feedings_by_day.items() if current_batch["date"] <= k <= day_str)
            remaining = max(0, current_batch["count"] - eaten)
        else:
            remaining = 0
        result.append({"date": day_str, "remaining": remaining})
        current_day += timedelta(days=1)

    return result


@router.get("/api/stats/motion")
async def stats_motion(_=_auth):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT strftime('%H', created_at, 'localtime') as hour, COUNT(*) as cnt"
            " FROM motion_events GROUP BY hour ORDER BY hour"
        )
        rows = await cur.fetchall()
    hours = [0] * 24
    for r in rows:
        try:
            hours[int(r["hour"])] = int(r["cnt"])
        except (TypeError, ValueError):
            pass
    return {"hours": hours}
