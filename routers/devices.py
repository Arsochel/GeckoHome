import asyncio
import httpx
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import Response

from services import tuya, camera
from database import log_lamp_event, save_photo, get_photos, get_photo_data, delete_photo
from routers.auth import get_current_user

router = APIRouter(prefix="/api")

_auth = Depends(get_current_user)


@router.get("/status")
async def get_status(_=_auth):
    return {
        "uv":   tuya.get_lamp_status("uv"),
        "heat": tuya.get_lamp_status("heat"),
        "temperature": tuya.get_sensor("thermometer", "va_temperature"),
        "humidity":    tuya.get_sensor("humidifier",  "va_humidity"),
    }


@router.post("/lamp/{lamp_type}/{action}")
async def control_lamp(lamp_type: str, action: str, _=_auth):
    print(f"[lamp] type={lamp_type!r} action={action!r}")
    if lamp_type not in ("uv", "heat") or action not in ("on", "off"):
        raise HTTPException(status_code=400, detail="Invalid request")
    on = action == "on"
    result = await asyncio.to_thread(tuya.switch_lamp, lamp_type, on)
    print(f"[lamp] switch_lamp result: {result}")
    if not result:
        raise HTTPException(status_code=400, detail="Failed to control lamp")
    await log_lamp_event(lamp_type, action, "web")
    return {"ok": True}


@router.get("/camera/snapshot")
async def get_snapshot(_=_auth):
    if not camera.is_configured():
        raise HTTPException(status_code=404, detail="Camera not configured")
    path = await camera.snapshot()
    if not path:
        raise HTTPException(status_code=503, detail="Snapshot failed")
    with open(path, "rb") as f:
        data = f.read()
    await save_photo(data, source="web")
    return Response(content=data, media_type="image/jpeg")


@router.get("/camera/clip")
async def get_clip(_=_auth):
    return await _clip_response(30)

@router.get("/camera/clip3")
async def get_clip3(_=_auth):
    return await _clip_response(180)

async def _clip_response(duration: int):
    if not camera.is_configured():
        raise HTTPException(status_code=404, detail="Camera not configured")
    path = await camera.clip(duration)
    if not path:
        raise HTTPException(status_code=503, detail="Clip failed")
    with open(path, "rb") as f:
        data = f.read()
    return Response(content=data, media_type="video/mp4",
                    headers={"Content-Disposition": "inline; filename=gecko_clip.mp4"})


@router.get("/camera/gallery")
async def gallery_list(limit: int = 20, offset: int = 0, _=_auth):
    return await get_photos(limit=limit, offset=offset)


@router.get("/camera/photos/{photo_id}")
async def gallery_photo(photo_id: int, _=_auth):
    data = await get_photo_data(photo_id)
    if not data:
        raise HTTPException(status_code=404)
    return Response(content=data, media_type="image/jpeg")


@router.delete("/camera/photos/{photo_id}")
async def gallery_delete(photo_id: int, _=_auth):
    await delete_photo(photo_id)
    return {"ok": True}



@router.post("/camera/whep")
async def whep_offer(request: Request, _=_auth):
    if not camera.mediamtx_ready():
        raise HTTPException(status_code=503, detail="Stream not available")
    body = await request.body()
    url = f"http://127.0.0.1:{camera.MEDIAMTX_PORT}/gecko/whep"
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, content=body,
                                 headers={"Content-Type": "application/sdp"}, timeout=10)
    headers = {}
    if "Location" in resp.headers:
        headers["Location"] = resp.headers["Location"]
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/sdp", headers=headers)
