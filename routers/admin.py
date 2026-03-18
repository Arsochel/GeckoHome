from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from services import tuya
from services.scheduler import scheduler
from config import TUYA_ENDPOINT
from routers.auth import get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def root():
    return RedirectResponse(url="/login")


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, user: str = Depends(get_current_user)):
    temp = tuya.get_sensor("thermometer", "va_temperature") or "N/A"
    hum = tuya.get_sensor("humidifier", "va_humidity") or "N/A"
    uv_status = tuya.get_lamp_status("uv")
    heat_status = tuya.get_lamp_status("heat")

    schedules = []
    for job in scheduler.get_jobs():
        trigger = job.trigger
        if not hasattr(trigger, "fields"):
            continue
        kwargs = job.kwargs or {}
        hour = next((str(f) for f in trigger.fields if f.name == "hour"), "?")
        minute = next((str(f) for f in trigger.fields if f.name == "minute"), "?")
        schedules.append({
            "id": job.id,
            "lamp_type": kwargs.get("lamp_type", "?"),
            "hour": hour,
            "minute": minute,
            "duration_h": kwargs.get("duration_h", 0),
            "paused": job.next_run_time is None,
            "next_run": job.next_run_time.strftime("%Y-%m-%d %H:%M") if job.next_run_time else "Paused",
        })

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "temperature": temp,
        "humidity": hum,
        "uv_status": uv_status,
        "heat_status": heat_status,
        "tuya_endpoint": TUYA_ENDPOINT,
        "tuya_token": tuya.is_connected(),
        "schedules": schedules,
    })


@router.post("/control/{action}")
async def control_device(action: str, user: str = Depends(get_current_user)):
    actions = {
        "uv_on": ("uv", True),
        "uv_off": ("uv", False),
        "heat_on": ("heat", True),
        "heat_off": ("heat", False),
    }
    if action in actions:
        lamp, on = actions[action]
        tuya.switch_lamp(lamp, on)
    return RedirectResponse(url="/admin", status_code=303)
