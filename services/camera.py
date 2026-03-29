import asyncio
import os
import subprocess
import tempfile

from config import CAMERA_RTSP_URL

HLS_DIR = os.path.join(tempfile.gettempdir(), "gecko_hls")
MEDIAMTX_CONFIG_PATH = os.path.join(tempfile.gettempdir(), "gecko_mediamtx.yml")
MEDIAMTX_PORT = 8889
MEDIAMTX_RTSP_PORT = 8554  # локальный RTSP-реестрим для клипов/снапшотов


def _source_url() -> str:
    """Использует локальный mediamtx если запущен, иначе прямой RTSP."""
    if _mediamtx_proc is not None and _mediamtx_proc.poll() is None:
        return f"rtsp://localhost:{MEDIAMTX_RTSP_PORT}/gecko"
    return CAMERA_RTSP_URL

_hls_proc: subprocess.Popen | None = None
_mediamtx_proc: subprocess.Popen | None = None


def is_configured() -> bool:
    return bool(CAMERA_RTSP_URL)


def _run_ffmpeg(args: list, timeout: int) -> subprocess.CompletedProcess:
    args = [args[0], "-loglevel", "error"] + args[1:]
    return subprocess.run(args, capture_output=True, timeout=timeout)


async def snapshot() -> str | None:
    if not CAMERA_RTSP_URL:
        return None

    # Берём кадр из motion monitor — не открываем лишнее RTSP соединение
    try:
        from services.motion import monitor as _monitor
        import cv2 as _cv2
        # Ждём кадра до 3 секунд если monitor запущен но кадра ещё нет
        for _ in range(30):
            frame = _monitor.get_latest_frame()
            if frame is not None:
                break
            await asyncio.sleep(0.1)
        if frame is not None:
            frame = _cv2.rotate(frame, _cv2.ROTATE_90_CLOCKWISE)
            fd, path = tempfile.mkstemp(suffix=".jpg", prefix="gecko_snap_")
            os.close(fd)
            _cv2.imwrite(path, frame)
            if os.path.getsize(path) > 0:
                return path
    except Exception:
        pass

    fd, path = tempfile.mkstemp(suffix=".jpg", prefix="gecko_snap_")
    os.close(fd)
    try:
        result = await asyncio.to_thread(_run_ffmpeg, [
            "ffmpeg", "-y", "-rtsp_transport", "tcp",
            "-i", _source_url(),
            "-frames:v", "1", "-update", "1", "-q:v", "2",
            "-vf", "transpose=1",
            path,
        ], 15)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
        print(f"[Camera] Snapshot failed:\n{result.stderr.decode()[-500:]}")
    except Exception as e:
        print(f"[Camera] Snapshot error: {e}")
    return None


async def clip(duration: int = 30) -> str | None:
    if not CAMERA_RTSP_URL:
        return None
    fd, path = tempfile.mkstemp(suffix=".mp4", prefix="gecko_clip_")
    os.close(fd)
    try:
        result = await asyncio.to_thread(_run_ffmpeg, [
            "ffmpeg", "-y", "-rtsp_transport", "tcp",
            "-i", _source_url(),
            "-t", str(duration),
            "-vf", "transpose=1,scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-an",
            "-movflags", "+faststart",
            path,
        ], duration + 40)
        if result.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 0:
            return path
        print(f"[Camera] Clip failed:\n{result.stderr.decode()[-500:]}")
    except Exception as e:
        print(f"[Camera] Clip error: {e}")
    return None


async def start_hls():
    global _hls_proc
    if not CAMERA_RTSP_URL:
        return
    os.makedirs(HLS_DIR, exist_ok=True)
    playlist = os.path.join(HLS_DIR, "stream.m3u8")
    _hls_proc = subprocess.Popen(
        [
            "ffmpeg", "-y", "-rtsp_transport", "tcp",
            "-i", CAMERA_RTSP_URL,
            "-vf", "transpose=1",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
            "-metadata:s:v:0", "rotate=0",
            "-an",
            "-f", "hls",
            "-hls_time", "1",
            "-hls_list_size", "3",
            "-hls_flags", "delete_segments+append_list",
            "-hls_segment_filename", os.path.join(HLS_DIR, "seg%03d.ts"),
            playlist,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"[Camera] HLS stream started, PID={_hls_proc.pid}")


async def stop_hls():
    global _hls_proc
    if _hls_proc:
        try:
            _hls_proc.terminate()
            _hls_proc.wait(timeout=1)
        except Exception:
            pass
        _hls_proc = None
        print("[Camera] HLS stream stopped")


def hls_ready() -> bool:
    playlist = os.path.join(HLS_DIR, "stream.m3u8")
    return os.path.exists(playlist) and os.path.getsize(playlist) > 0


def _write_mediamtx_config():
    config = (
        "logLevel: error\n"
        "api: no\n"
        "metrics: no\n"
        "pprof: no\n"
        "rtsp: yes\n"
        f"rtspAddress: :{MEDIAMTX_RTSP_PORT}\n"
        "rtmp: no\n"
        "srt: no\n"
        "hls: no\n"
        "webrtc: yes\n"
        f"webrtcAddress: :{MEDIAMTX_PORT}\n"
        "paths:\n"
        "  gecko:\n"
        f"    source: {CAMERA_RTSP_URL}\n"
    )
    with open(MEDIAMTX_CONFIG_PATH, "w") as f:
        f.write(config)


async def start_mediamtx(bin_path: str):
    global _mediamtx_proc
    if not CAMERA_RTSP_URL or not bin_path:
        return
    _write_mediamtx_config()
    _mediamtx_proc = subprocess.Popen(
        [bin_path, MEDIAMTX_CONFIG_PATH],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"[Camera] mediamtx started, PID={_mediamtx_proc.pid}")


async def stop_mediamtx():
    global _mediamtx_proc
    if _mediamtx_proc:
        try:
            _mediamtx_proc.terminate()
            _mediamtx_proc.wait(timeout=3)
        except Exception:
            try:
                _mediamtx_proc.kill()
            except Exception:
                pass
        _mediamtx_proc = None
        print("[Camera] mediamtx stopped")


def mediamtx_ready() -> bool:
    return _mediamtx_proc is not None and _mediamtx_proc.poll() is None
