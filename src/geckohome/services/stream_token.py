"""Короткоживущие HMAC-токены для доступа к стриму.

Стрим открывается из Telegram WebApp, где нет веб-сессии, поэтому эндпоинты
камеры не могут требовать логин. Вместо этого бот подписывает URL токеном
``{expires_ts}.{hmac}``, а веб-сервер его проверяет. Туннель и так меняет
URL раз в сутки, так что TTL в 48 часов ничего не ослабляет.

Секрет общий для процессов бота и веба: берётся ``SECRET_KEY`` из ``.env``,
а если он не задан — генерируется один раз и хранится в ``stream_secret.txt``
рядом с базами (оба процесса видят один файл).
"""

import hmac
import logging
import os
import secrets
import time
from hashlib import sha256

from geckohome.config import settings
from geckohome.paths import STREAM_SECRET_FILE

log = logging.getLogger(__name__)

TOKEN_TTL = 48 * 3600  # секунд; кнопки в чате и так протухают с URL туннеля

_secret: bytes | None = None


def _load_secret() -> bytes:
    global _secret
    if _secret is not None:
        return _secret
    if settings.secret_key:
        _secret = settings.secret_key.encode()
        return _secret
    # SECRET_KEY не задан — персистентный секрет в файле, общий для процессов
    try:
        with open(STREAM_SECRET_FILE) as f:
            value = f.read().strip()
        if value:
            _secret = value.encode()
            return _secret
    except OSError:
        pass
    value = secrets.token_hex(32)
    try:
        fd = os.open(STREAM_SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, value.encode())
        finally:
            os.close(fd)
        log.info("generated stream secret: %s", STREAM_SECRET_FILE)
    except FileExistsError:
        # другой процесс успел первым — берём его секрет
        with open(STREAM_SECRET_FILE) as f:
            value = f.read().strip()
    _secret = value.encode()
    return _secret


def _sign(expires: int) -> str:
    return hmac.new(_load_secret(), str(expires).encode(), sha256).hexdigest()[:32]


def issue_stream_token(ttl: int = TOKEN_TTL) -> str:
    expires = int(time.time()) + ttl
    return f"{expires}.{_sign(expires)}"


def verify_stream_token(token: str) -> bool:
    expires_str, _, sig = token.partition(".")
    try:
        expires = int(expires_str)
    except ValueError:
        return False
    if expires < time.time():
        return False
    return hmac.compare_digest(sig, _sign(expires))
