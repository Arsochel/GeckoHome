# Timelapse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ежедневный таймлапс террариума — кадры каждые 2 минуты, генерация и отправка в Telegram в 12:00.

**Architecture:** Новый модуль `services/timelapse.py` с двумя функциями: `capture_timelapse_frame` (сохраняет кадр из motion monitor) и `generate_and_send_timelapse` (собирает видео через ffmpeg, отправляет в Telegram). Обе регистрируются как APScheduler джобы в `services/scheduler.py`.

**Tech Stack:** OpenCV (cv2), ffmpeg (subprocess), httpx, APScheduler (уже используется)

---

## Структура файлов

| Файл | Действие |
|---|---|
| `services/timelapse.py` | Создать — весь код захвата и генерации |
| `services/scheduler.py` | Изменить — добавить 2 джоба |
| `.gitignore` | Изменить — добавить `timelapse/` |

Кадры хранятся в `timelapse/frames/YYYY-MM-DD/HHMMss.jpg` рядом с проектом.

---

### Task 1: `services/timelapse.py` — захват кадров

**Files:**
- Create: `services/timelapse.py`

- [ ] **Шаг 1: Создать файл с функцией захвата кадра**

```python
# services/timelapse.py
import os
import cv2
from datetime import datetime

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_SUPER_ADMINS

_BASE_DIR = os.path.dirname(os.path.dirname(__file__))
TIMELAPSE_FRAMES_DIR = os.path.join(_BASE_DIR, "timelapse", "frames")


def capture_timelapse_frame():
    from services.motion import monitor as motion_monitor
    frame = motion_monitor.get_latest_frame()
    if frame is None:
        return
    frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    today = datetime.now().strftime("%Y-%m-%d")
    stamp = datetime.now().strftime("%H%M%S")
    folder = os.path.join(TIMELAPSE_FRAMES_DIR, today)
    os.makedirs(folder, exist_ok=True)
    cv2.imwrite(os.path.join(folder, f"{stamp}.jpg"), frame)
```

- [ ] **Шаг 2: Проверить вручную**

Запустить в Python shell (при запущенном сервере):
```python
from services.timelapse import capture_timelapse_frame
capture_timelapse_frame()
import os; print(os.listdir("timelapse/frames"))
```
Ожидание: в `timelapse/frames/YYYY-MM-DD/` появился один `.jpg` файл.

- [ ] **Шаг 3: Добавить `timelapse/` в `.gitignore`**

Дописать в конец `.gitignore`:
```
timelapse/
```

- [ ] **Шаг 4: Коммит**

```bash
git add services/timelapse.py .gitignore
git commit -m "feat: timelapse frame capture"
```

---

### Task 2: Генерация видео через ffmpeg

**Files:**
- Modify: `services/timelapse.py`

- [ ] **Шаг 1: Добавить вспомогательную функцию сборки видео**

Добавить в `services/timelapse.py` после `capture_timelapse_frame`:

```python
import subprocess
import tempfile


def _compile_timelapse(frames_dir: str, fps: int, output_path: str) -> bool:
    """Собирает видео из папки с кадрами. Возвращает True при успехе."""
    frames = sorted(f for f in os.listdir(frames_dir) if f.endswith(".jpg"))
    if len(frames) < 10:
        return False

    # Пишем filelist.txt для concat demuxer
    fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="timelapse_")
    try:
        with os.fdopen(fd, "w") as f:
            for name in frames:
                f.write(f"file '{os.path.join(frames_dir, name)}'\n")
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-r", str(fps),
                "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,"
                       "pad=720:1280:(ow-iw)/2:(oh-ih)/2",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                output_path,
            ],
            capture_output=True, timeout=300,
        )
        return result.returncode == 0
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass
```

- [ ] **Шаг 2: Проверить вручную**

```python
from services.timelapse import _compile_timelapse
import tempfile, os
# используем вчерашнюю (или сегодняшнюю) папку с кадрами
frames_dir = "timelapse/frames/2026-04-02"  # подставить реальную дату
fd, out = tempfile.mkstemp(suffix=".mp4")
os.close(fd)
ok = _compile_timelapse(frames_dir, 24, out)
print(ok, os.path.getsize(out))
```
Ожидание: `True` и ненулевой размер файла.

- [ ] **Шаг 3: Коммит**

```bash
git add services/timelapse.py
git commit -m "feat: timelapse ffmpeg compilation"
```

---

### Task 3: Отправка в Telegram и основной джоб

**Files:**
- Modify: `services/timelapse.py`

- [ ] **Шаг 1: Добавить функцию отправки видео**

Добавить в `services/timelapse.py`:

```python
import httpx


async def _send_video(path: str, caption: str, recipients: set[int]):
    if not TELEGRAM_BOT_TOKEN or not recipients:
        return
    async with httpx.AsyncClient(timeout=120) as client:
        for uid in recipients:
            try:
                with open(path, "rb") as f:
                    await client.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo",
                        data={"chat_id": str(uid), "caption": caption},
                        files={"video": ("timelapse.mp4", f, "video/mp4")},
                    )
            except Exception as e:
                print(f"[Timelapse] send error to {uid}: {e}")
```

- [ ] **Шаг 2: Добавить главный джоб генерации**

Добавить в `services/timelapse.py`:

```python
import shutil
from datetime import date, timedelta


# На период тестирования отправляем только супер-админам.
# После выбора скорости: заменить на get_allowed_users() + TELEGRAM_SUPER_ADMINS.
async def generate_and_send_timelapse():
    from database import get_allowed_users
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    frames_dir = os.path.join(TIMELAPSE_FRAMES_DIR, yesterday)

    if not os.path.isdir(frames_dir):
        print(f"[Timelapse] no frames for {yesterday}, skipping")
        return

    frame_count = sum(1 for f in os.listdir(frames_dir) if f.endswith(".jpg"))
    if frame_count < 10:
        print(f"[Timelapse] only {frame_count} frames for {yesterday}, skipping")
        shutil.rmtree(frames_dir, ignore_errors=True)
        return

    print(f"[Timelapse] generating from {frame_count} frames for {yesterday}")

    # Фаза тестирования: 3 варианта скорости → только супер-админам
    fps_variants = [15, 24, 30]
    recipients = TELEGRAM_SUPER_ADMINS

    tmp_files = []
    try:
        for fps in fps_variants:
            fd, out_path = tempfile.mkstemp(suffix=".mp4", prefix=f"timelapse_{fps}fps_")
            os.close(fd)
            tmp_files.append(out_path)
            ok = await asyncio.to_thread(_compile_timelapse, frames_dir, fps, out_path)
            if ok:
                caption = f"🎬 Таймлапс {yesterday} • {fps}fps ({frame_count} кадров)"
                await _send_video(out_path, caption, recipients)
                print(f"[Timelapse] sent {fps}fps to {len(recipients)} recipients")
            else:
                print(f"[Timelapse] ffmpeg failed for {fps}fps")
    finally:
        for p in tmp_files:
            try:
                os.unlink(p)
            except OSError:
                pass
        shutil.rmtree(frames_dir, ignore_errors=True)
```

Добавить `import asyncio` в начало файла (если ещё нет).

- [ ] **Шаг 3: Проверить импорты — полный список в начале файла**

Убедиться, что в начале `services/timelapse.py` есть:
```python
import asyncio
import os
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta

import cv2
import httpx

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_SUPER_ADMINS
```

- [ ] **Шаг 4: Коммит**

```bash
git add services/timelapse.py
git commit -m "feat: timelapse generation and telegram delivery"
```

---

### Task 4: Регистрация джобов в планировщике

**Files:**
- Modify: `services/scheduler.py`

- [ ] **Шаг 1: Добавить импорт**

В `services/scheduler.py` добавить в блок импортов:

```python
from services.timelapse import capture_timelapse_frame, generate_and_send_timelapse
```

- [ ] **Шаг 2: Добавить джобы в `load_schedules`**

В конце функции `load_schedules()`, после строки с `purge_photos`:

```python
    scheduler.add_job(capture_timelapse_frame, "interval", minutes=2, id="timelapse_capture")
    scheduler.add_job(generate_and_send_timelapse, "cron", hour=12, minute=0, id="timelapse_generate")
```

- [ ] **Шаг 3: Запустить сервер и убедиться что джобы стартуют**

```bash
python main.py
```
В логах не должно быть ошибок импорта. Через 2 минуты проверить:
```bash
python -c "import os; print(os.listdir('timelapse/frames'))"
```
Ожидание: папка с сегодняшней датой и хотя бы один `.jpg` файл.

- [ ] **Шаг 4: Коммит и пуш**

```bash
git add services/scheduler.py
git commit -m "feat: register timelapse jobs in scheduler"
git push
```

---

## После выбора скорости

Когда придёт таймлапс с тремя вариантами и выберем один fps, в `generate_and_send_timelapse` нужно:

1. Заменить `fps_variants = [15, 24, 30]` на `fps_variants = [ВЫБРАННЫЙ_FPS]`
2. Поменять получателей:
```python
# было:
recipients = TELEGRAM_SUPER_ADMINS
# стало:
users = await get_allowed_users()
recipients = {u["user_id"] for u in users} | TELEGRAM_SUPER_ADMINS
```
