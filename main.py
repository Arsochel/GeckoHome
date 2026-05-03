import asyncio
import json
import logging
import os
from datetime import datetime
from contextlib import asynccontextmanager

from logging_config import setup_logging
setup_logging(enable_debug_buffer=True)

log = logging.getLogger(__name__)

import cv2

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from config import SECRET_KEY, MEDIAMTX_BIN
from database import init_db, load_last_feeding
from services import tuya, camera, tunnel
from services.scheduler import load_schedules, start as start_scheduler, shutdown as stop_scheduler
from services.motion import monitor as motion_monitor
from services.highlights import update_gecko_state
from routers import auth, admin, devices, schedules, debug


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    await load_last_feeding()
    await load_schedules()
    start_scheduler()
    tuya.start_listener()
    await motion_monitor.start()
    async def _initial_state_check():
        await asyncio.sleep(10)
        await update_gecko_state()
    asyncio.create_task(_initial_state_check())
    if camera.is_configured():
        try:
            await camera.start_hls()
        except Exception as e:
            log.error("Camera HLS failed: %s", e)
        try:
            await camera.start_mediamtx(MEDIAMTX_BIN)
        except Exception as e:
            log.error("Camera mediamtx failed: %s", e)
        log.info("camera ready")
    asyncio.create_task(tunnel.start())
    yield
    stop_scheduler()
    await motion_monitor.stop()
    await camera.stop_hls()
    await camera.stop_mediamtx()


app = FastAPI(title="Gecko Home", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(devices.router)
app.include_router(schedules.router)
app.include_router(debug.router)

import httpx as _httpx
from fastapi import Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

os.makedirs(camera.HLS_DIR, exist_ok=True)
_templates = Jinja2Templates(directory="templates")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("static/favicon.ico", media_type="image/x-icon")


@app.get("/stream", response_class=HTMLResponse)
async def stream_page(request: Request):
    return _templates.TemplateResponse(request, "stream.html")


@app.get("/api/stream/live.mjpeg")
async def stream_live_mjpeg():

    async def _generate():
        while True:
            frame = motion_monitor.get_latest_frame()
            if frame is None:
                await asyncio.sleep(0.1)
                continue
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
            _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
            await asyncio.sleep(0.033)

    return StreamingResponse(
        _generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    await websocket.accept()
    log.debug("WS client connected")
    try:
        while True:
            uv, heat = await asyncio.gather(
                asyncio.to_thread(tuya.get_lamp_status, "uv"),
                asyncio.to_thread(tuya.get_lamp_status, "heat"),
            )
            await websocket.send_text(json.dumps({"uv": uv, "heat": heat}))
            await asyncio.sleep(5)
    except Exception:
        pass
    log.debug("WS client disconnected")


@app.get("/hls/{filename}")
async def serve_hls(filename: str):
    path = os.path.realpath(os.path.join(camera.HLS_DIR, filename))
    hls_dir = os.path.realpath(camera.HLS_DIR)
    if not path.startswith(hls_dir + os.sep) and path != hls_dir:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not os.path.exists(path):
        raise HTTPException(status_code=404)
    if filename.endswith(".m3u8"):
        return FileResponse(path, media_type="application/vnd.apple.mpegurl")
    return FileResponse(path, media_type="video/mp2t")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("SERVER_PORT", "8000")),
        log_config=None,  # не перезаписывать наш logging setup
    )
