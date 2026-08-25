import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time

from geckohome.paths import TUNNEL_PID_FILE, TUNNEL_URL_FILE

log = logging.getLogger(__name__)

# Единый супервизор: restart() сигналит существующему потоку _run() через это
# событие, а не плодит новые потоки. Иначе на каждый рестарт (06:00/18:00 + ручной)
# копился лишний _run + свой cloudflared — процессы накапливались десятками.
_restart_requested = threading.Event()
_started = False
_started_lock = threading.Lock()


def _kill_group(pid: int) -> None:
    """Убивает всю группу процессов cloudflared: родитель + форкнутый им ребёнок.

    Одиночный SIGTERM родителю оставлял ребёнка живым. cloudflared запускается с
    start_new_session=True, поэтому его pid == pgid и killpg накрывает обоих.
    """
    if sys.platform != "win32":  # os.killpg — только POSIX; в контейнере Linux
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except OSError:
            pass


def _run() -> None:
    port = os.getenv("SERVER_PORT", "8000")
    delay = 60

    while True:
        try:
            try:
                os.remove(TUNNEL_URL_FILE)
            except OSError:
                pass

            # start_new_session=True → cloudflared становится лидером своей группы
            # процессов, что позволяет _kill_group() снести и форкнутого ребёнка.
            proc = subprocess.Popen(
                ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )

            with open(TUNNEL_PID_FILE, "w") as f:
                f.write(str(proc.pid))

            assert proc.stderr is not None  # stderr=PIPE выше
            for line in proc.stderr:
                line = line.decode(errors="ignore").strip()
                m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                if m:
                    url = m.group(0)
                    with open(TUNNEL_URL_FILE, "w") as f:
                        f.write(url)
                    log.info("cloudflared tunnel: %s", url)
                    delay = 60
                    break

            # Ждём либо смерти процесса (сам отвалился), либо запроса на рестарт.
            while proc.poll() is None and not _restart_requested.is_set():
                _restart_requested.wait(timeout=2)

            if _restart_requested.is_set():
                _restart_requested.clear()
                _kill_group(proc.pid)
                proc.wait()
                delay = 0  # ручной/плановый рестарт — поднимаем сразу, без backoff
            else:
                proc.wait()
        except FileNotFoundError:
            log.warning("cloudflared not found, skipping tunnel")
            return
        except Exception as e:
            log.error("cloudflared error: %s", e)

        if delay:
            time.sleep(delay)
            delay = min(delay * 2, 1800)


def _ensure_supervisor() -> bool:
    """Запускает поток _run() один раз. Возвращает True, если запустил сейчас."""
    global _started
    with _started_lock:
        if _started:
            return False
        _started = True
    threading.Thread(target=_run, daemon=True).start()
    return True


async def start() -> None:
    _ensure_supervisor()


def restart() -> None:
    # Если супервизор в этом процессе ещё не поднят — просто запускаем его.
    # Иначе сигналим уже работающему потоку, чтобы он переподнял туннель.
    if _ensure_supervisor():
        return
    try:
        os.remove(TUNNEL_URL_FILE)
    except OSError:
        pass
    _restart_requested.set()
