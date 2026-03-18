import asyncio
import os
import tempfile

from config import CAMERA_RTSP_URL

SNAP_PATH = os.path.join(tempfile.gettempdir(), "gecko_snap.jpg")
CLIP_PATH = os.path.join(tempfile.gettempdir(), "gecko_clip.mp4")


def is_configured() -> bool:
    return bool(CAMERA_RTSP_URL)


async def snapshot() -> str | None:
    if not CAMERA_RTSP_URL:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-rtsp_transport", "tcp",
            "-i", CAMERA_RTSP_URL,
            "-frames:v", "1", "-q:v", "2",
            SNAP_PATH,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
        if os.path.exists(SNAP_PATH) and os.path.getsize(SNAP_PATH) > 0:
            return SNAP_PATH
    except Exception as e:
        print(f"[Camera] Snapshot error: {e}")
    return None


async def clip(duration: int = 15) -> str | None:
    if not CAMERA_RTSP_URL:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-rtsp_transport", "tcp",
            "-i", CAMERA_RTSP_URL,
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            CLIP_PATH,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=duration + 15)
        if os.path.exists(CLIP_PATH) and os.path.getsize(CLIP_PATH) > 0:
            return CLIP_PATH
    except Exception as e:
        print(f"[Camera] Clip error: {e}")
    return None
