import asyncio
import logging
from collections import deque

QUIET_LOGGERS = {"services.motion", "services.scheduler", "services.tuya", "services.camera"}
_BUFFER_SIZE = 500

_buffers: dict[str, deque] = {}
_subscribers: list[asyncio.Queue] = []


def _record_to_dict(r: logging.LogRecord) -> dict:
    return {
        "ts": r.created,
        "level": r.levelname,
        "logger": r.name,
        "msg": r.getMessage(),
    }


class RingBufferHandler(logging.Handler):
    def emit(self, record):
        try:
            d = _record_to_dict(record)
            buf = _buffers.setdefault(record.name, deque(maxlen=_BUFFER_SIZE))
            buf.append(d)
            for q in list(_subscribers):
                try:
                    q.put_nowait(d)
                except asyncio.QueueFull:
                    pass
        except Exception:
            pass


class QuietServiceFilter(logging.Filter):
    """Drops INFO/DEBUG from QUIET_LOGGERS so stdout stays clean."""

    def filter(self, record):
        return not (record.name in QUIET_LOGGERS and record.levelno < logging.WARNING)


def attach(stdout_handler: logging.Handler):
    """Call from main.py after setup_logging(). Adds ring buffer + stdout filter."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    if stdout_handler is not None:
        stdout_handler.setLevel(logging.INFO)
        stdout_handler.addFilter(QuietServiceFilter())
    rb = RingBufferHandler()
    rb.setLevel(logging.DEBUG)
    root.addHandler(rb)


def get_recent(service: str | None = None, limit: int = 200) -> list[dict]:
    if service and service != "all":
        key = f"services.{service}"
        return list(_buffers.get(key, deque()))[-limit:]
    out = []
    for buf in _buffers.values():
        out.extend(buf)
    out.sort(key=lambda r: r["ts"])
    return out[-limit:]


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue):
    try:
        _subscribers.remove(q)
    except ValueError:
        pass
