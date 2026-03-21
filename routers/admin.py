from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from services import tuya
from services.scheduler import scheduler
from config import DEVICE_IDS
from routers.auth import get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="templates")

_DEVICE_LABELS = {
    "uv_lamp":     "UV Lamp",
    "heat_lamp":   "Heat Lamp",
    "thermometer": "Thermometer",
    "humidifier":  "Humidifier",
}


@router.get("/", response_class=HTMLResponse)
async def root():
    return RedirectResponse(url="/login")


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, user: str = Depends(get_current_user)):
    temp = tuya.get_sensor("thermometer", "va_temperature") or "N/A"
    hum  = tuya.get_sensor("humidifier",  "va_humidity")    or "N/A"

    schedules = []
    for job in scheduler.get_jobs():
        if not hasattr(job.trigger, "fields"):
            continue
        kw     = job.kwargs or {}
        hour   = next((str(f) for f in job.trigger.fields if f.name == "hour"),   "?")
        minute = next((str(f) for f in job.trigger.fields if f.name == "minute"), "?")
        schedules.append({
            "id":         job.id,
            "lamp_type":  kw.get("lamp_type", "?"),
            "hour":       hour,
            "minute":     minute,
            "duration_h": kw.get("duration_h", 0),
            "paused":     job.next_run_time is None,
            "next_run":   job.next_run_time.strftime("%Y-%m-%d %H:%M") if job.next_run_time else "Paused",
        })

    uv_status   = tuya.get_lamp_status("uv")
    heat_status = tuya.get_lamp_status("heat")

    devices = []
    for key, device_id in DEVICE_IDS.items():
        if not device_id:
            devices.append({"name": _DEVICE_LABELS.get(key, key), "id": None, "online": None})
            continue
        if key == "uv_lamp":
            online = uv_status.get("online")
        elif key == "heat_lamp":
            online = heat_status.get("online")
        else:
            online = tuya.get_sensor(key, "va_temperature" if key == "thermometer" else "va_humidity") is not None
        devices.append({"name": _DEVICE_LABELS.get(key, key), "id": device_id, "online": online})

    return templates.TemplateResponse("admin.html", {
        "request":      request,
        "temperature":  temp,
        "humidity":     hum,
        "uv_status":    uv_status,
        "heat_status":  heat_status,
        "schedules":    schedules,
        "devices":      devices,
    })
