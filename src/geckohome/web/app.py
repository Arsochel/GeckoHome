import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from geckohome.logging_config import setup_logging

setup_logging(enable_debug_buffer=True)

log = logging.getLogger(__name__)


import cv2
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from geckohome import paths
from geckohome.config import MEDIAMTX_BIN, SECRET_KEY
from geckohome.database import init_db, load_last_feeding
from geckohome.services import camera, tunnel, tuya
from geckohome.services.highlights import update_gecko_state
from geckohome.services.motion import monitor as motion_monitor
from geckohome.services.scheduler import load_schedules
from geckohome.services.scheduler import shutdown as stop_scheduler
from geckohome.services.scheduler import start as start_scheduler
from geckohome.services.stream_token import verify_stream_token
from geckohome.web.routers import admin, auth, debug, devices, ingest, schedules, stats


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
app.mount("/static", StaticFiles(directory=paths.STATIC_DIR), name="static")

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(devices.router)
app.include_router(schedules.router)
app.include_router(debug.router)
app.include_router(stats.router)
app.include_router(ingest.router)

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

os.makedirs(camera.HLS_DIR, exist_ok=True)
_templates = Jinja2Templates(directory=paths.TEMPLATES_DIR)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(paths.FAVICON_PATH, media_type="image/x-icon")


@app.get("/healthz", include_in_schema=False)
async def healthz():
    """Liveness для docker healthcheck: процесс жив и event loop не задедлочен.
    Без авторизации — отдаёт только ok, никаких данных."""
    return {"ok": True}


def _require_stream_access(request: Request, t: str = ""):
    """Стрим доступен по веб-сессии ИЛИ по подписанному токену из бота.

    Без этого камера в квартире торчит наружу через Cloudflare-туннель
    без какой-либо авторизации.
    """
    if request.session.get("user"):
        return
    if t and verify_stream_token(t):
        return
    raise HTTPException(status_code=401, detail="stream token required")


@app.get("/stream", response_class=HTMLResponse)
async def stream_page(request: Request, t: str = ""):
    _require_stream_access(request, t)
    return _templates.TemplateResponse(request, "stream.html", {"stream_token": t})


@app.get("/api/stream/snapshot")
async def stream_snapshot_internal(request: Request, t: str = ""):
    _require_stream_access(request, t)
    frame = motion_monitor.get_latest_frame()
    from fastapi import HTTPException
    from fastapi.responses import Response

    if frame is None:
        raise HTTPException(status_code=503, detail="No frame available")
    frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return Response(content=buf.tobytes(), media_type="image/jpeg")


@app.get("/api/stream/live.mjpeg")
async def stream_live_mjpeg(request: Request, t: str = ""):
    _require_stream_access(request, t)

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


_ws_clients: set[WebSocket] = set()
_ws_broadcaster: asyncio.Task | None = None
_ws_last_payload: str | None = None


async def _ws_broadcast_loop():
    """Один tuya-опрос на всех клиентов: раньше каждый WS-клиент крутил свой
    цикл, и N соединений давали 2N tuya-вызовов каждые 5 секунд."""
    global _ws_broadcaster, _ws_last_payload
    try:
        while _ws_clients:
            uv, heat = await asyncio.gather(
                asyncio.to_thread(tuya.get_lamp_status, "uv"),
                asyncio.to_thread(tuya.get_lamp_status, "heat"),
            )
            _ws_last_payload = json.dumps({"uv": uv, "heat": heat})
            for ws in list(_ws_clients):
                try:
                    await ws.send_text(_ws_last_payload)
                except Exception:
                    _ws_clients.discard(ws)
            await asyncio.sleep(5)
    finally:
        _ws_broadcaster = None


@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    global _ws_broadcaster
    if not websocket.session.get("user"):
        await websocket.close(code=4401, reason="not authenticated")
        return
    await websocket.accept()
    log.debug("WS client connected (%d total)", len(_ws_clients) + 1)
    if _ws_last_payload:
        await websocket.send_text(_ws_last_payload)
    _ws_clients.add(websocket)
    if _ws_broadcaster is None:
        _ws_broadcaster = asyncio.create_task(_ws_broadcast_loop())
    try:
        while True:
            await websocket.receive_text()  # держим соединение, ловим disconnect
    except Exception:
        pass
    finally:
        _ws_clients.discard(websocket)
    log.debug("WS client disconnected (%d left)", len(_ws_clients))


@app.get("/hls/{filename}")
async def serve_hls(request: Request, filename: str, t: str = ""):
    _require_stream_access(request, t)
    path = os.path.realpath(os.path.join(camera.HLS_DIR, filename))
    hls_dir = os.path.realpath(camera.HLS_DIR)
    if not path.startswith(hls_dir + os.sep) and path != hls_dir:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not os.path.exists(path):
        raise HTTPException(status_code=404)
    if filename.endswith(".m3u8"):
        return FileResponse(path, media_type="application/vnd.apple.mpegurl")
    return FileResponse(path, media_type="video/mp2t")


def run() -> None:
    """Console-script entry point (``geckohome-web``)."""
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("SERVER_PORT", "8000")),
        log_config=None,  # не перезаписывать наш logging setup
    )


if __name__ == "__main__":
    run()
