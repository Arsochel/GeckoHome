"""Еженедельный дайджест: сводка недели у геккона в Telegram."""

import logging

import httpx

from geckohome.config import TELEGRAM_BOT_TOKEN, TELEGRAM_SUPER_ADMINS
from geckohome.database import get_blocked_user_ids, get_cricket_remaining, get_zone_daily
from geckohome.database._core import _db

log = logging.getLogger(__name__)

_ZONE_LABELS = {"skull": "💀 череп", "water": "💧 поилка", "sauna": "♨️ баня"}


def _fmt_duration(seconds: int) -> str:
    minutes = seconds // 60
    if minutes >= 60:
        return f"{minutes // 60}ч {minutes % 60:02d}м"
    return f"{minutes}м"


async def build_weekly_digest() -> str:
    async with _db() as db:
        async with db.execute(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(crickets), 0) AS crickets"
            " FROM feedings WHERE fed_at >= datetime('now', '-7 days')"
        ) as cur:
            row = await cur.fetchone()
            assert row is not None
            feedings, crickets = row["cnt"], row["crickets"]

        async with db.execute(
            "SELECT COUNT(*) AS cnt FROM motion_events"
            " WHERE created_at >= datetime('now', '-7 days')"
        ) as cur:
            row = await cur.fetchone()
            assert row is not None
            motions = row["cnt"]

        async with db.execute(
            "SELECT MIN(temperature) AS tmin, MAX(temperature) AS tmax,"
            " MIN(humidity) AS hmin, MAX(humidity) AS hmax"
            " FROM sensor_readings WHERE recorded_at >= datetime('now', '-7 days')"
        ) as cur:
            env = await cur.fetchone()
            assert env is not None

    lines = ["📋 *Неделя у геккона*", "━━━━━━━━━━━━━━━", ""]

    if feedings:
        lines.append(f"🍎 Кормлений: *{feedings}* ({crickets} 🦗)")
    else:
        lines.append("🍎 Кормлений: *0* — проверь дневник!")

    lines.append(f"🏃 Событий движения: *{motions}*")

    zone_rows = await get_zone_daily(days=7)
    if zone_rows:
        totals: dict[str, int] = {}
        for r in zone_rows:
            bucket = r["zone"] if r["zone"] in _ZONE_LABELS else "прогулки"
            totals[bucket] = totals.get(bucket, 0) + r["seconds"]
        parts = [
            f"{_ZONE_LABELS.get(zone, '🏃 ' + zone)} {_fmt_duration(sec)}"
            for zone, sec in sorted(totals.items(), key=lambda kv: -kv[1])
        ]
        lines.append("📍 Где жил: " + " · ".join(parts))

    if env["tmin"] is not None:
        lines.append(f"🌡 Температура: *{env['tmin'] / 10:.1f}–{env['tmax'] / 10:.1f}°C*")
    if env["hmin"] is not None:
        lines.append(f"💧 Влажность: *{env['hmin']:.0f}–{env['hmax']:.0f}%*")

    remaining = await get_cricket_remaining()
    if remaining is not None:
        lines.append(f"🦗 Сверчков осталось: *{remaining}*")

    return "\n".join(lines)


async def send_weekly_digest():
    text = await build_weekly_digest()
    blocked = await get_blocked_user_ids()
    recipients = TELEGRAM_SUPER_ADMINS - blocked
    if not TELEGRAM_BOT_TOKEN or not recipients:
        return
    for uid in recipients:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": uid, "text": text, "parse_mode": "Markdown"},
                )
        except Exception as e:
            log.debug("digest send failed: %s", e)
    log.info("weekly digest sent to %d users", len(recipients))
