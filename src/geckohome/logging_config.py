import logging
import sys

_RESET = "\033[0m"
_GREY = "\033[38;5;240m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_BRED = "\033[1;31m"
_DIM = "\033[2m"

_LEVEL_COLOR = {
    logging.DEBUG: _GREY,
    logging.INFO: _GREEN,
    logging.WARNING: _YELLOW,
    logging.ERROR: _RED,
    logging.CRITICAL: _BRED,
}


class _ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        lc = _LEVEL_COLOR.get(record.levelno, _RESET)
        time = self.formatTime(record, "%H:%M:%S")
        name = record.name[:20]
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        return (
            f"{_DIM}{time}{_RESET}  "
            f"{lc}{record.levelname:<8}{_RESET}  "
            f"{_CYAN}{name:<20}{_RESET}  "
            f"{msg}"
        )


def setup_logging(debug: bool = False, enable_debug_buffer: bool = False) -> logging.Handler:
    level = logging.DEBUG if debug else logging.INFO

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_ColorFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # uvicorn — убираем его хэндлеры, пускаем через наш форматтер
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True

    # шум от сторонних библиотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext._updater").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Updater").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("tinytuya").setLevel(logging.WARNING)

    # транзиентные сетевые ошибки Telegram (Bad Gateway, Service Unavailable и т.п.)
    # не наша вина, PTB сам ретраит — логируем как warning, не error
    class _TgNetworkFilter(logging.Filter):
        _TRANSIENT = ("Bad Gateway", "Service Unavailable", "Gateway Timeout")

        def filter(self, record: logging.LogRecord) -> bool:
            if record.levelno >= logging.ERROR:
                msg = record.getMessage()
                if any(t in msg for t in self._TRANSIENT):
                    record.levelno = logging.WARNING
                    record.levelname = "WARNING"
            return True

    logging.getLogger("telegram.ext.Updater").addFilter(_TgNetworkFilter())

    if enable_debug_buffer:
        from geckohome.services.debug_log import attach

        attach(handler)

    return handler
