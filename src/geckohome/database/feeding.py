"""Feeding log, cricket stock and supplement scheduling."""

from datetime import datetime, timedelta

from geckohome.database._core import _db

# ── Feedings ──

_last_feeding_time: datetime | None = None


async def load_last_feeding():
    global _last_feeding_time
    async with _db() as db:
        async with db.execute("SELECT fed_at FROM feedings ORDER BY fed_at DESC LIMIT 1") as cur:
            row = await cur.fetchone()
            if row:
                _last_feeding_time = datetime.fromisoformat(row["fed_at"])


async def _today_feeding(db):
    async with db.execute(
        "SELECT id, crickets, vitamins, hornworm FROM feedings"
        " WHERE DATE(fed_at) = DATE('now', 'localtime') ORDER BY fed_at DESC LIMIT 1"
    ) as cur:
        return await cur.fetchone()


async def log_feeding(crickets: int | None = None, vitamins: bool = False, hornworm: bool = False):
    global _last_feeding_time
    _last_feeding_time = datetime.now().replace(microsecond=0)
    async with _db(write=True) as db:
        row = await _today_feeding(db)
        if row:
            new_crickets = crickets if crickets is not None else row["crickets"]
            new_vitamins = int(vitamins or row["vitamins"])
            new_hornworm = int(hornworm or row["hornworm"])
            await db.execute(
                "UPDATE feedings SET crickets=?, vitamins=?, hornworm=? WHERE id=?",
                (new_crickets, new_vitamins, new_hornworm, row["id"]),
            )
        else:
            await db.execute(
                "INSERT INTO feedings (fed_at, crickets, vitamins, hornworm) VALUES (?, ?, ?, ?)",
                (_last_feeding_time, crickets, int(vitamins), int(hornworm)),
            )


async def append_feeding_note(note: str):
    """Добавляет заметку к сегодняшнему кормлению; если кормления нет — создаёт новое."""
    col = {"vitamins": "vitamins", "hornworm": "hornworm"}.get(note)
    if col:
        async with _db(write=True) as db:
            row = await _today_feeding(db)
            if row:
                await db.execute(f"UPDATE feedings SET {col}=1 WHERE id=?", (row["id"],))
            else:
                await log_feeding(**{col: True})
    else:
        # неизвестная заметка — игнорируем
        pass


def get_last_feeding_cached() -> datetime | None:
    return _last_feeding_time


async def get_last_feeding_db() -> datetime | None:
    """Читает дату последнего кормления из БД (не из кэша)."""
    async with _db() as db:
        async with db.execute("SELECT fed_at FROM feedings ORDER BY fed_at DESC LIMIT 1") as cur:
            row = await cur.fetchone()
            return datetime.fromisoformat(row["fed_at"]) if row else None


async def get_feeding_count() -> int:
    async with _db() as db:
        async with db.execute("SELECT COUNT(*) as cnt FROM feedings") as cur:
            row = await cur.fetchone()
            return row["cnt"] if row else 0


async def get_last_note_date(note: str) -> datetime | None:
    """Возвращает дату последнего кормления с указанной заметкой (vitamins или hornworm)."""
    col = {"vitamins": "vitamins", "hornworm": "hornworm"}.get(note)
    if not col:
        return None
    async with _db() as db:
        async with db.execute(
            f"SELECT fed_at FROM feedings WHERE {col}=1 ORDER BY fed_at DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            return datetime.fromisoformat(row["fed_at"]) if row else None


async def log_cricket_purchase(count: int = 20):
    async with _db(write=True) as db:
        await db.execute("INSERT INTO cricket_batches (bought_at, count) VALUES (?, ?)", (datetime.now(), count))


async def log_cricket_ran_out():
    """Помечает последнюю партию как закончившуюся — сдвигает bought_at так чтобы days_since >= LIFESPAN."""
    LIFESPAN = 6
    ran_out_date = datetime.now() - timedelta(days=LIFESPAN)
    async with _db(write=True) as db:
        async with db.execute("SELECT id FROM cricket_batches ORDER BY bought_at DESC LIMIT 1") as cur:
            row = await cur.fetchone()
        if row:
            await db.execute("UPDATE cricket_batches SET bought_at = ?, count = 0 WHERE id = ?",
                             (ran_out_date, row["id"]))
        else:
            await db.execute("INSERT INTO cricket_batches (bought_at, count) VALUES (?, ?)", (ran_out_date, 0))


async def get_last_cricket_purchase() -> tuple[datetime | None, int]:
    """Возвращает (дата закупки, кол-во за вычетом смертей) последней партии или (None, 0)."""
    async with _db() as db:
        async with db.execute(
            "SELECT bought_at, count, COALESCE(deaths, 0) as deaths FROM cricket_batches ORDER BY bought_at DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None, 0
            return datetime.fromisoformat(row["bought_at"]), max(0, row["count"] - row["deaths"])


async def get_cricket_remaining() -> int | None:
    """Остаток сверчков: куплено минус сдохло минус съедено с момента последней закупки."""
    bought_at, total = await get_last_cricket_purchase()
    if bought_at is None or total == 0:
        return None
    async with _db() as db:
        async with db.execute(
            "SELECT COALESCE(SUM(crickets), 0) as eaten FROM feedings WHERE fed_at >= ? AND crickets IS NOT NULL",
            (bought_at,),
        ) as cur:
            row = await cur.fetchone()
    return max(0, total - (row["eaten"] or 0))


async def log_cricket_deaths(count: int):
    """Записывает гибель сверчков в текущую партию."""
    async with _db() as db:
        await db.execute(
            "UPDATE cricket_batches SET deaths = COALESCE(deaths, 0) + ? WHERE id = (SELECT id FROM cricket_batches ORDER BY bought_at DESC LIMIT 1)",
            (count,),
        )
        await db.commit()


async def get_feedings_count_since(since: datetime) -> int:
    """Количество кормлений начиная с даты since (не включая)."""
    async with _db() as db:
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM feedings WHERE fed_at > ?", (since.isoformat(),)
        ) as cur:
            row = await cur.fetchone()
            return row["cnt"] if row else 0


async def get_next_feeding_supplements() -> list[str]:
    """Возвращает список добавок для следующего кормления."""
    supplements = []
    last_vitamins = await get_last_note_date("vitamins")
    if last_vitamins is None or await get_feedings_count_since(last_vitamins) >= 1:
        # каждое 2-е кормление (1-2 раза в неделю по статье)
        supplements.append("vitamins")
    last_hornworm = await get_last_note_date("hornworm")
    if last_hornworm is None or (datetime.now() - last_hornworm).days >= 14:
        supplements.append("hornworm")
    return supplements


# ── Feeding history ──

async def get_feeding_history(limit: int = 10) -> list[dict]:
    async with _db() as db:
        async with db.execute(
            "SELECT fed_at, crickets, vitamins, hornworm FROM feedings ORDER BY fed_at DESC LIMIT ?", (limit,)
        ) as cur:
            return [
                {
                    "fed_at": datetime.fromisoformat(r["fed_at"]),
                    "crickets": r["crickets"],
                    "vitamins": bool(r["vitamins"]),
                    "hornworm": bool(r["hornworm"]),
                }
                for r in await cur.fetchall()
            ]


async def get_cricket_stats() -> dict:
    """Суммарная статистика по количеству сверчков."""
    async with _db() as db:
        async with db.execute(
            "SELECT COALESCE(SUM(crickets), 0) as total, COUNT(*) as count"
            " FROM feedings WHERE crickets IS NOT NULL"
        ) as cur:
            row = await cur.fetchone()
    total, count = row["total"] or 0, row["count"] or 0
    return {"total": total, "count": count, "avg": round(total / count, 1) if count else 0}


