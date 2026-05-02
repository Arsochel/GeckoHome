import asyncio
import logging
import os
import re
import subprocess
import threading
import time

log = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(__file__))
TUNNEL_URL_FILE = os.path.join(_BASE_DIR, "tunnel_url.txt")
TUNNEL_PID_FILE = os.path.join(_BASE_DIR, "tunnel.pid")


def _run():
    port  = os.getenv("SERVER_PORT", "8000")
    delay = 60

    while True:
        try:
            try:
                os.remove(TUNNEL_URL_FILE)
            except OSError:
                pass
            proc = subprocess.Popen(
                ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )

            with open(TUNNEL_PID_FILE, "w") as f:
                f.write(str(proc.pid))

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

            proc.wait()
        except FileNotFoundError:
            log.warning("cloudflared not found, skipping tunnel")
            return
        except Exception as e:
            log.error("cloudflared error: %s", e)
        time.sleep(delay)
        delay = min(delay * 2, 1800)


async def start():
    t = threading.Thread(target=_run, daemon=True)
    t.start()


def restart():
    try:
        with open(TUNNEL_PID_FILE) as f:
            pid = int(f.read().strip())
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    except Exception:
        pass
    try:
        os.remove(TUNNEL_URL_FILE)
    except OSError:
        pass
    try:
        os.remove(TUNNEL_PID_FILE)
    except OSError:
        pass
    t = threading.Thread(target=_run, daemon=True)
    t.start()
