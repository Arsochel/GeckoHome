import uuid

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from services.scheduler import scheduler, lamp_schedule
from database import save_schedule, delete_schedule as db_delete_schedule, set_schedule_paused
from routers.auth import get_current_user

router = APIRouter(prefix="/api/schedules")


class ScheduleCreate(BaseModel):
    lamp_type: str
    hour: int
    minute: int
    duration_h: float


@router.post("")
async def create_schedule(data: ScheduleCreate, _user: str = Depends(get_current_user)):
    if data.lamp_type not in ("uv", "heat"):
        raise HTTPException(status_code=400, detail="Invalid lamp type")
    job_id = f"{data.lamp_type}_lamp_{uuid.uuid4().hex[:8]}"
    scheduler.add_job(
        lamp_schedule, "cron",
        hour=data.hour, minute=data.minute,
        kwargs={"lamp_type": data.lamp_type, "duration_h": data.duration_h},
        id=job_id,
    )
    await save_schedule(job_id, data.lamp_type, data.hour, data.minute, data.duration_h)
    return {"id": job_id}


@router.delete("/{job_id}")
async def delete_schedule(job_id: str, _user: str = Depends(get_current_user)):
    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Schedule not found")
    job.remove()
    await db_delete_schedule(job_id)
    return {"ok": True}


@router.post("/{job_id}/toggle")
async def toggle_schedule(job_id: str, _user: str = Depends(get_current_user)):
    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if job.next_run_time is None:
        job.resume()
        await set_schedule_paused(job_id, False)
    else:
        job.pause()
        await set_schedule_paused(job_id, True)
    return {"paused": job.next_run_time is None}
