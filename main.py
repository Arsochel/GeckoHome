import asyncio
import json
import os
import re
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from config import SECRET_KEY, MEDIAMTX_BIN
from database import init_db, load_last_feeding
from services import tuya, camera
from services.scheduler import load_schedules, start as start_scheduler, shutdown as stop_scheduler
from services.motion import monitor as motion_monitor
from services.highlights import update_gecko_state
from routers import auth, admin, devices, schedules


_TUNNEL_URL_FILE = os.path.join(os.path.dirname(__file__), "tunnel_url.txt")

def _run_cloudflared():
    import subprocess
    port = os.getenv("SERVER_PORT", "8000")
    try:
        proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        for line in proc.stdout:
            text = line.decode(errors="ignore")
            m = re.search(r"https://[\w-]+\.trycloudflare\.com", text)
            if m:
                url = m.group(0)
                with open(_TUNNEL_URL_FILE, "w") as f:
                    f.write(url)
                print(f"[Cloudflare] {url}")
        proc.wait()
    except FileNotFoundError:
        print("[Cloudflare] cloudflared not found, skipping")
    except Exception as e:
        print(f"[Cloudflare] error: {type(e).__name__}: {e}")


async def _start_cloudflared():
    await asyncio.to_thread(_run_cloudflared)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    await load_last_feeding()
    await load_schedules()
    start_scheduler()
    await motion_monitor.start()
    async def _initial_state_check():
        await asyncio.sleep(10)
        await update_gecko_state(force=True)
    asyncio.create_task(_initial_state_check())
    if camera.is_configured():
        try:
            await camera.start_hls()
        except Exception as e:
            print(f"Camera HLS failed: {e}")
        try:
            await camera.start_mediamtx(MEDIAMTX_BIN)
        except Exception as e:
            print(f"Camera mediamtx failed: {e}")
    asyncio.create_task(_start_cloudflared())
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

import os
import httpx as _httpx
from fastapi import Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.templating import Jinja2Templates

os.makedirs(camera.HLS_DIR, exist_ok=True)
_templates = Jinja2Templates(directory="templates")


@app.get("/stream", response_class=HTMLResponse)
async def stream_page(request: Request):
    return _templates.TemplateResponse("stream.html", {"request": request})


@app.post("/api/stream/view")
async def stream_view(request: Request):
    """Логирует кто открыл стрим через Telegram WebApp."""
    import urllib.parse
    body = await request.json()
    init_data = body.get("initData", "")
    if not init_data:
        return {"ok": False}
    try:
        params = dict(urllib.parse.parse_qsl(init_data, strict_parsing=True))
        user_str = params.get("user", "{}")
        import json as _json
        user = _json.loads(user_str)
        name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")]))
        username = user.get("username", "")
        uid = user.get("id", "?")
        msg = f"[Bot] [{datetime.now().strftime('%H:%M:%S')}] Stream opened by @{username or name} ({uid})"
        try:
            async with _httpx.AsyncClient() as c:
                await c.post(
                    "http://127.0.0.1:8765",
                    content=f'{{"msg": "{msg}"}}'.encode(),
                    headers={"Content-Type": "application/json"},
                    timeout=1,
                )
        except Exception:
            pass
    except Exception as e:
        print(f"[Stream] view log error: {e}")
    return {"ok": True}


@app.post("/api/stream/whep")
async def stream_whep_public(request: Request):
    """Публичный WHEP для страницы стрима (без сессионной авторизации)."""
    if not camera.mediamtx_ready():
        raise HTTPException(status_code=503, detail="Stream not available")
    body = await request.body()
    url = f"http://127.0.0.1:{camera.MEDIAMTX_PORT}/gecko/whep"
    async with _httpx.AsyncClient() as client:
        resp = await client.post(url, content=body,
                                 headers={"Content-Type": "application/sdp"}, timeout=10)
    headers = {}
    if "Location" in resp.headers:
        headers["Location"] = resp.headers["Location"]
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type="application/sdp", headers=headers)


@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    await websocket.accept()
    print("[WS] client connected")
    try:
        while True:
            uv, heat = await asyncio.gather(
                asyncio.to_thread(tuya.get_lamp_status, "uv"),
                asyncio.to_thread(tuya.get_lamp_status, "heat"),
            )
            await websocket.send_text(json.dumps({"uv": uv, "heat": heat}))
            await asyncio.sleep(1)
    except Exception:
        pass
    print("[WS] client disconnected")


@app.get("/hls/{filename}")
async def serve_hls(filename: str):
    path = os.path.join(camera.HLS_DIR, filename)
    if not os.path.exists(path):
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    if filename.endswith(".m3u8"):
        return FileResponse(path, media_type="application/vnd.apple.mpegurl")
    return FileResponse(path, media_type="video/mp2t")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
