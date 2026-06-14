import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta

import cv2
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from geckohome import paths
from geckohome.database import validate_debug_token, get_motion_events_24h_count, get_recent_motion_events
from geckohome.services import camera, motion as motion_module, debug_log
from geckohome.services.motion import monitor as motion_monitor
from geckohome.services.yolo import get_model as get_yolo_model

log = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=paths.TEMPLATES_DIR)

_START_TS = time.monotonic()


def _require_debug(request: Request) -> int:
    user_id = request.session.get("debug_user")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return int(user_id)


@router.get("/debug", response_class=HTMLResponse)
async def debug_page(request: Request, token: str | None = None):
    if token:
        user_id = await validate_debug_token(token)
        if not user_id:
            raise HTTPException(status_code=403, detail="Invalid or expired token")
        request.session["debug_user"] = user_id
        return RedirectResponse(url="/debug", status_code=303)
    _require_debug(request)
    return templates.TemplateResponse(request, "debug.html")


@router.get("/debug/stream/raw")
async def debug_stream_raw(request: Request):
    _require_debug(request)

    async def _gen():
        while True:
            frame = motion_monitor.get_debug_frame()
            if frame is None:
                await asyncio.sleep(0.1)
                continue
            rotated = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
            ok, buf = cv2.imencode(".jpg", rotated, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if not ok:
                await asyncio.sleep(0.1)
                continue
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
            await asyncio.sleep(0.1)

    return StreamingResponse(_gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@router.get("/debug/stream/yolo")
async def debug_stream_yolo(request: Request):
    _require_debug(request)

    async def _gen():
        last_inference = 0.0
        last_boxes: list[tuple[int, int, int, int, float]] = []
        while True:
            frame = motion_monitor.get_latest_frame()
            if frame is None:
                await asyncio.sleep(0.1)
                continue
            rotated = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
            now = time.monotonic()
            if now - last_inference >= 1.0:
                last_inference = now
                model = get_yolo_model()
                if model is not None:
                    try:
                        conf = motion_module.yolo_debug_conf
                        results = await asyncio.to_thread(model, rotated, verbose=False, conf=conf)
                        boxes = []
                        if results:
                            for box in results[0].boxes:
                                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                                boxes.append((x1, y1, x2, y2, float(box.conf[0])))
                        last_boxes = boxes
                    except Exception as e:
                        log.warning("YOLO debug inference failed: %s", e)
            for (x1, y1, x2, y2, conf) in last_boxes:
                cv2.rectangle(rotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(rotated, f"{conf:.2f}", (x1, max(20, y1 - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            ok, buf = cv2.imencode(".jpg", rotated, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if not ok:
                await asyncio.sleep(0.1)
                continue
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
            await asyncio.sleep(0.1)

    return StreamingResponse(_gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@router.get("/debug/logs/recent")
async def debug_logs_recent(request: Request, service: str = "all", limit: int = 200):
    _require_debug(request)
    limit = max(1, min(limit, 500))
    return debug_log.get_recent(service=service, limit=limit)


@router.get("/debug/logs/stream")
async def debug_logs_stream(request: Request, service: str = "all"):
    _require_debug(request)

    async def _gen():
        q = debug_log.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    record = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if service != "all":
                    expected = f"services.{service}"
                    if record.get("logger") != expected:
                        continue
                yield f"data: {json.dumps(record)}\n\n"
        finally:
            debug_log.unsubscribe(q)

    return StreamingResponse(_gen(), media_type="text/event-stream")


def _disk_size_mb(path: str) -> float:
    if not os.path.exists(path):
        return 0.0
    if os.path.isfile(path):
        return os.path.getsize(path) / (1024 * 1024)
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / (1024 * 1024)


@router.get("/debug/metrics")
async def debug_metrics(request: Request):
    _require_debug(request)

    cpu_percent = 0.0
    ram_mb = 0.0
    try:
        import psutil
        proc = psutil.Process()
        cpu_percent = proc.cpu_percent(interval=None)
        ram_mb = proc.memory_info().rss / (1024 * 1024)
    except Exception:
        pass

    timelapse_mb = await asyncio.to_thread(_disk_size_mb, paths.TIMELAPSE_DIR)
    db_mb = await asyncio.to_thread(_disk_size_mb, paths.DB_PATH)

    last_motion = motion_module.get_last_motion_time()
    events_24h = await get_motion_events_24h_count()

    from geckohome.services.scheduler import scheduler as aps
    jobs = []
    for j in aps.get_jobs():
        jobs.append({
            "id": j.id,
            "next_run": j.next_run_time.isoformat() if j.next_run_time else None,
            "paused": j.next_run_time is None,
        })

    hls_proc = camera._hls_proc
    mtx_proc = camera._mediamtx_proc

    return {
        "uptime_sec": time.monotonic() - _START_TS,
        "cpu_percent": cpu_percent,
        "ram_mb": ram_mb,
        "disk": {"timelapse_mb": timelapse_mb, "db_mb": db_mb},
        "motion": {
            "running": motion_monitor.is_running(),
            "last_motion": last_motion.isoformat() if last_motion else None,
            "warmup_done": motion_monitor.get_warmup_done(),
            "events_24h": events_24h,
            "yolo_conf": motion_module.yolo_debug_conf,
        },
        "camera": {
            "hls_running": hls_proc is not None and hls_proc.poll() is None,
            "hls_pid": hls_proc.pid if hls_proc else None,
            "mediamtx_running": camera.mediamtx_ready(),
            "mediamtx_pid": mtx_proc.pid if mtx_proc else None,
            "source_url": camera._source_url() if camera.is_configured() else None,
        },
        "scheduler": {"jobs": jobs},
    }


@router.post("/debug/yolo-conf")
async def debug_yolo_conf(request: Request):
    _require_debug(request)
    body = await request.json()
    conf = float(body.get("conf", 0.4))
    conf = max(0.05, min(0.99, conf))
    motion_module.yolo_debug_conf = conf
    return {"ok": True, "conf": conf}


@router.get("/debug/motion-events")
async def debug_motion_events(request: Request, limit: int = 20):
    _require_debug(request)
    limit = max(1, min(limit, 100))
    return await get_recent_motion_events(limit=limit)


@router.get("/api/health")
async def health():
    hls_proc = camera._hls_proc
    return JSONResponse({
        "ok": True,
        "camera": "running" if (hls_proc is not None and hls_proc.poll() is None) else ("stopped" if camera.is_configured() else "na"),
        "motion": "running" if motion_monitor.is_running() else "stopped",
        "scheduler": "running",
        "uptime_sec": time.monotonic() - _START_TS,
    })
