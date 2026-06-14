"""APScheduler singleton, shared by all scheduler modules."""

from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()
