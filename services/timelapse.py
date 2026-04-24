import asyncio
import logging
import os
import pathlib
import subprocess
import tempfile
from datetime import date, datetime, timedelta

log = logging.getLogger(__name__)

import cv2
import httpx
from PIL import Image, ImageOps

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_SUPER_ADMINS, TIMELAPSE_OWNER_ID
from database import get_blocked_user_ids, set_user_blocked

_BASE_DIR = os.path.dirname(os.path.dirname(__file__))
TIMELAPSE_FRAMES_DIR = os.path.join(_BASE_DIR, "timelapse", "frames")
TIMELAPSE_VIDEOS_DIR = os.path.join(_BASE_DIR, "timelapse", "videos")
TIMELAPSE_VIDEO_RETENTION_DAYS = 7


TIMELAPSE_MOTION_WINDOW = 60  # секунд с последнего движения — кадр сохраняется


def _strip_exif(path: str):
    """Применяет EXIF orientation к пикселям и убирает EXIF метаданные."""
    try:
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)
        img.save(path, quality=95, exif=b"")
    except Exception:
        pass


def capture_timelapse_frame():
    from services.motion import monitor as motion_monitor, get_last_motion_time
    last_motion = get_last_motion_time()
    if last_motion is None:
        return
    if (datetime.now() - last_motion).total_seconds() > TIMELAPSE_MOTION_WINDOW:
        return
    frame = motion_monitor.get_latest_frame()
    if frame is None:
        return
    frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%H%M%S_%f")[:13]  # до миллисекунд чтобы не перезаписывать
    folder = os.path.join(TIMELAPSE_FRAMES_DIR, today)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{stamp}.jpg")
    if not cv2.imwrite(path, frame):
        log.error("failed to write %s", path)
        return
    _strip_exif(path)


TIMELAPSE_DIFF_THRESHOLD = 5.5  # % значимых пикселей между кадрами
TIMELAPSE_DIFF_PIXEL_MIN = 12   # минимальный diff пикселя чтобы считаться значимым
YOLO_MODEL_PATH = r"C:\Users\artem\runs\detect\train6\weights\best.pt"
YOLO_CONF = 0.7

_yolo_model = None


def _get_yolo():
    global _yolo_model
    if _yolo_model is None:
        from ultralytics import YOLO
        _yolo_model = YOLO(YOLO_MODEL_PATH)
    return _yolo_model


def _detect_gecko(bgr):
    """Возвращает список bbox [(x1,y1,x2,y2,conf)] или []."""
    try:
        model = _get_yolo()
        results = model(bgr, conf=YOLO_CONF, verbose=False)
        boxes = []
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                boxes.append((x1, y1, x2, y2, float(box.conf[0])))
        return boxes
    except Exception as e:
        log.error("YOLO error: %s", e)
        return []


def _boxes_to_mask(shape, boxes):
    import numpy as np
    mask = np.zeros(shape[:2], dtype=np.uint8)
    for (x1, y1, x2, y2, _) in boxes:
        cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
    return mask


def _compute_diff(gray1, gray2, mask=None):
    """Возвращает % значимых пикселей (по маске если есть)."""
    import numpy as np
    b1 = cv2.GaussianBlur(gray1, (5, 5), 0)
    b2 = cv2.GaussianBlur(gray2, (5, 5), 0)
    diff = cv2.absdiff(b1, b2)
    significant = diff >= TIMELAPSE_DIFF_PIXEL_MIN
    if mask is not None:
        total = float((mask > 0).sum())
        if total == 0:
            return 0.0
        return float(significant[mask > 0].sum()) / total * 100
    return float(significant.sum()) / diff.size * 100


def _filter_similar_frames(frames_dir: str, frame_names: list[str]) -> list[str]:
    """Убирает кадры слишком похожие на предыдущий. Использует YOLO-маску если геккон виден."""
    import numpy as np
    result = []
    prev_gray = None
    prev_bgr = None
    skipped = 0
    for name in frame_names:
        path = os.path.join(frames_dir, name)
        bgr = cv2.imread(path)
        if bgr is None:
            continue
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        if prev_gray is None:
            result.append(name)
            prev_gray, prev_bgr = gray, bgr
            continue
        if gray.shape != prev_gray.shape:
            gray = cv2.resize(gray, (prev_gray.shape[1], prev_gray.shape[0]))
        # YOLO-маска из обоих кадров
        boxes = _detect_gecko(prev_bgr) + _detect_gecko(bgr)
        mask = _boxes_to_mask(gray.shape, boxes) if boxes else None
        score = _compute_diff(prev_gray, gray, mask=mask)
        if score >= TIMELAPSE_DIFF_THRESHOLD:
            result.append(name)
            prev_gray, prev_bgr = gray, bgr
        else:
            skipped += 1
    log.info("filtered %d/%d frames (threshold=%.1f%%)", skipped, len(frame_names), TIMELAPSE_DIFF_THRESHOLD)
    return result


def _collect_frames(from_dt: datetime, to_dt: datetime) -> list[tuple[str, str]]:
    """Возвращает список (frames_dir, filename) за период [from_dt, to_dt)."""
    result = []
    # Перебираем все папки с датами которые могут пересекаться с периодом
    if not os.path.isdir(TIMELAPSE_FRAMES_DIR):
        return result
    for day_name in sorted(os.listdir(TIMELAPSE_FRAMES_DIR)):
        day_dir = os.path.join(TIMELAPSE_FRAMES_DIR, day_name)
        if not os.path.isdir(day_dir):
            continue
        try:
            day_date = datetime.strptime(day_name, "%Y-%m-%d").date()
        except ValueError:
            continue
        # Пропускаем папки которые точно вне диапазона
        if day_date < from_dt.date() or day_date > to_dt.date():
            continue
        for name in sorted(f for f in os.listdir(day_dir) if f.endswith(".jpg")):
            # Имя формата HHMMSS_ms.jpg
            try:
                t = datetime.strptime(f"{day_name} {name[:6]}", "%Y-%m-%d %H%M%S")
            except ValueError:
                continue
            if from_dt <= t < to_dt:
                result.append((day_dir, name))
    return result


def _prune_frames(frame_pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Удаляет кадры не прошедшие фильтр схожести, возвращает оставшиеся."""
    # Группируем по папкам для фильтрации
    by_dir: dict[str, list[str]] = {}
    for d, n in frame_pairs:
        by_dir.setdefault(d, []).append(n)

    # Строим единый отфильтрованный список сохраняя порядок
    keep_set: set[tuple[str, str]] = set()
    all_names = [(d, n) for d, n in frame_pairs]
    # Фильтруем как единую последовательность
    import numpy as np
    result = []
    prev_gray = None
    prev_bgr = None
    skipped = 0
    for (d, name) in all_names:
        path = os.path.join(d, name)
        bgr = cv2.imread(path)
        if bgr is None:
            continue
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        if prev_gray is None:
            result.append((d, name))
            prev_gray, prev_bgr = gray, bgr
            continue
        if gray.shape != prev_gray.shape:
            gray = cv2.resize(gray, (prev_gray.shape[1], prev_gray.shape[0]))
        boxes = _detect_gecko(prev_bgr) + _detect_gecko(bgr)
        mask = _boxes_to_mask(gray.shape, boxes) if boxes else None
        score = _compute_diff(prev_gray, gray, mask=mask)
        if score >= TIMELAPSE_DIFF_THRESHOLD:
            result.append((d, name))
            prev_gray, prev_bgr = gray, bgr
        else:
            skipped += 1

    # Удаляем файлы не вошедшие в результат
    keep_set = set(result)
    deleted = 0
    for (d, name) in all_names:
        if (d, name) not in keep_set:
            try:
                os.unlink(os.path.join(d, name))
                deleted += 1
            except OSError:
                pass
    log.info("pruned %d/%d frames, kept %d", deleted, len(all_names), len(result))
    return result


def _normalize_frames(frame_pairs: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """Копирует кадры во временную папку, применяя EXIF orientation к пикселям.
    Возвращает (tmp_dir, list_of_filenames)."""
    tmp_dir = tempfile.mkdtemp(prefix="timelapse_norm_")
    names = []
    for i, (d, name) in enumerate(frame_pairs):
        src = os.path.join(d, name)
        dst_name = f"{i:06d}.jpg"
        dst = os.path.join(tmp_dir, dst_name)
        try:
            img = Image.open(src)
            img = ImageOps.exif_transpose(img)
            img.save(dst, quality=95, exif=b"")
            names.append(dst_name)
        except Exception:
            pass
    return tmp_dir, names


def _compile_timelapse(frame_pairs: list[tuple[str, str]], fps: int, output_path: str) -> bool:
    """Собирает видео из списка (dir, filename). Возвращает True при успехе."""
    if len(frame_pairs) < 10:
        return False

    # Нормализуем кадры — применяем EXIF к пикселям, убираем метаданные
    tmp_dir, norm_names = _normalize_frames(frame_pairs)
    if len(norm_names) < 10:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False

    # Пишем filelist.txt для concat demuxer
    fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="timelapse_")
    try:
        with os.fdopen(fd, "w") as f:
            for name in norm_names:
                f.write(f"file '{pathlib.Path(tmp_dir, name).as_posix()}'\n")
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-r", str(fps),
                "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-vf", "scale=720:-2",
                "-pix_fmt", "yuv420p",
                "-c:v", "libx264", "-preset", "fast", "-crf", "28",
                output_path,
            ],
            capture_output=True, timeout=300,
        )
        if result.returncode != 0:
            log.error("ffmpeg error:\n%s", result.stderr.decode(errors="replace")[-500:])
            return False
        return True
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _send_video(path: str, caption: str, recipients: set[int], reply_markup: dict | None = None):
    """Отправляет видео в Telegram."""
    if not TELEGRAM_BOT_TOKEN or not recipients:
        return
    blocked = await get_blocked_user_ids()
    recipients = recipients - blocked
    if not recipients:
        return
    async with httpx.AsyncClient(timeout=120) as client:
        for uid in recipients:
            try:
                data: dict = {"chat_id": str(uid), "caption": caption}
                if reply_markup:
                    import json
                    data["reply_markup"] = json.dumps(reply_markup)
                with open(path, "rb") as f:
                    resp = await client.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo",
                        data=data,
                        files={"video": ("timelapse.mp4", f, "video/mp4")},
                    )
                resp_data = resp.json()
                if not resp_data.get("ok"):
                    desc = resp_data.get("description", "")
                    log.warning("sendVideo failed for %s: %s", uid, desc)
                    if "blocked" in desc.lower():
                        await set_user_blocked(uid, True)
            except Exception as e:
                log.error("send error to %s: %s", uid, e)


def _cleanup_old_videos():
    cutoff = datetime.now().timestamp() - TIMELAPSE_VIDEO_RETENTION_DAYS * 86400
    for name in os.listdir(TIMELAPSE_VIDEOS_DIR):
        if not name.endswith(".mp4"):
            continue
        path = os.path.join(TIMELAPSE_VIDEOS_DIR, name)
        if os.path.getmtime(path) < cutoff:
            try:
                os.unlink(path)
                log.info("deleted old video: %s", name)
            except OSError:
                pass


TIMELAPSE_DAY_START_HOUR = 12  # "сутки" с 12:00 до 12:00


async def _generate_and_send(from_dt: datetime, to_dt: datetime, label: str):
    """Генерирует таймлапс за период [from_dt, to_dt) и отправляет владельцу."""
    frame_pairs = await asyncio.to_thread(_collect_frames, from_dt, to_dt)
    if len(frame_pairs) < 10:
        log.warning("only %d frames for %s, skipping", len(frame_pairs), label)
        return

    log.info("generating %s from %d frames (%s — %s)", label, len(frame_pairs), from_dt.strftime("%Y-%m-%d %H:%M"), to_dt.strftime("%Y-%m-%d %H:%M"))

    os.makedirs(TIMELAPSE_VIDEOS_DIR, exist_ok=True)
    _cleanup_old_videos()

    frame_pairs = await asyncio.to_thread(_prune_frames, frame_pairs)

    day_label = from_dt.strftime("%Y-%m-%d")
    out_path = os.path.join(TIMELAPSE_VIDEOS_DIR, f"timelapse_{day_label}_15fps.mp4")
    ok = await asyncio.to_thread(_compile_timelapse, frame_pairs, 15, out_path)
    if ok:
        caption = f"🎬 {label} {day_label}"
        publish_markup = {
            "inline_keyboard": [[
                {"text": "📢 Опубликовать всем", "callback_data": f"timelapse_publish_{day_label}"}
            ]]
        }
        await _send_video(out_path, caption, {TIMELAPSE_OWNER_ID}, reply_markup=publish_markup)
        log.info("sent to owner %s", TIMELAPSE_OWNER_ID)
    else:
        log.error("ffmpeg failed, timelapse not generated")


async def generate_and_send_timelapse_for(day: str, label: str):
    """Генерирует таймлапс за сутки начиная с 12:00 указанного дня."""
    from_dt = datetime.strptime(day, "%Y-%m-%d").replace(hour=TIMELAPSE_DAY_START_HOUR, minute=0, second=0)
    to_dt = from_dt + timedelta(days=1)
    await _generate_and_send(from_dt, to_dt, label)


async def generate_and_send_timelapse():
    """Генерирует таймлапс за прошедшие сутки (12:00 вчера — 12:00 сегодня)."""
    today = date.today()
    to_dt = datetime.combine(today, datetime.min.time()).replace(hour=TIMELAPSE_DAY_START_HOUR)
    from_dt = to_dt - timedelta(days=1)
    await _generate_and_send(from_dt, to_dt, "Таймлапс")


async def generate_and_send_timelapse_preview():
    """Превью текущего дня (12:00 вчера — сейчас)."""
    today = date.today()
    from_dt = datetime.combine(today, datetime.min.time()).replace(hour=TIMELAPSE_DAY_START_HOUR)
    if datetime.now().hour < TIMELAPSE_DAY_START_HOUR:
        from_dt -= timedelta(days=1)
    to_dt = datetime.now()
    await _generate_and_send(from_dt, to_dt, "Превью")
