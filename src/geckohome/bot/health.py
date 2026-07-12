"""Мониторинг живости веб-процесса из бота.

Бот и веб-сервер — отдельные процессы, общаются только через БД. Если
``sensor_readings`` перестали обновляться — веб-процесс лежит (или все
источники показаний молчат). Проверяем периодически и шлём супер-админам
алерт; когда показания вернулись — отбой.
"""

import asyncio
import logging

from geckohome.config import TELEGRAM_SUPER_ADMINS
from geckohome.database._core import _db

log = logging.getLogger(__name__)

CHECK_INTERVAL = 15 * 60  # секунд между проверками
# Показания пишутся каждые 30 мин; 3 пропущенных цикла подряд — уже не флап.
STALE_AFTER_MIN = 90

STALE_MSG = (
    "⚠️ Веб-сервер не пишет показания сенсоров уже больше "
    f"{STALE_AFTER_MIN} минут — возможно, процесс упал."
)
OK_MSG = "✅ Веб-сервер снова пишет показания сенсоров."


async def is_web_stale() -> bool:
    """True, если за последние STALE_AFTER_MIN минут нет ни одного показания."""
    async with (
        _db() as db,
        db.execute(
            "SELECT COUNT(*) AS cnt FROM sensor_readings WHERE recorded_at >= datetime('now', ?)",
            (f"-{STALE_AFTER_MIN} minutes",),
        ) as cur,
    ):
        row = await cur.fetchone()
    assert row is not None  # агрегат без GROUP BY всегда возвращает строку
    return row["cnt"] == 0


class HealthMonitor:
    """Переходы stale/ok → какое сообщение слать (или ничего)."""

    def __init__(self):
        self._alerted = False

    def transition(self, stale: bool) -> str | None:
        if stale and not self._alerted:
            self._alerted = True
            return STALE_MSG
        if not stale and self._alerted:
            self._alerted = False
            return OK_MSG
        return None


async def health_loop(bot):
    monitor = HealthMonitor()
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            msg = monitor.transition(await is_web_stale())
        except Exception as e:
            log.error("health check failed: %s", e)
            continue
        if msg is None:
            continue
        log.warning("health: %s", msg)
        for uid in TELEGRAM_SUPER_ADMINS:
            try:
                await bot.send_message(chat_id=uid, text=msg)
            except Exception as e:
                log.warning("health alert to %s failed: %s", uid, e)
