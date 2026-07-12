import logging
import secrets
import time
from collections import defaultdict, deque

import bcrypt
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from geckohome import paths
from geckohome.config import ADMIN_PASSWORD_HASH, ADMIN_USERNAME

log = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=paths.TEMPLATES_DIR)

# ── Rate limit логина: bcrypt медленный, но брутфорс через туннель никто
# не остановит без лимита. In-memory достаточно — процесс один. ──
_LOGIN_MAX_FAILURES = 5
_LOGIN_WINDOW = 15 * 60  # секунд
_failed_logins: dict[str, deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    # за cloudflared реальный IP в заголовке, request.client — это туннель
    return (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "?")
    )


def _login_blocked(ip: str) -> bool:
    attempts = _failed_logins[ip]
    cutoff = time.time() - _LOGIN_WINDOW
    while attempts and attempts[0] < cutoff:
        attempts.popleft()
    return len(attempts) >= _LOGIN_MAX_FAILURES


def get_current_user(request: Request) -> str:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def get_csrf_token(request: Request) -> str:
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_hex(16)
    return request.session["csrf_token"]


async def verify_csrf(request: Request):
    token = request.session.get("csrf_token", "")
    if request.method in ("POST", "PUT", "DELETE"):
        form = await request.form()
        submitted = form.get("csrf_token", "")
        if not token or submitted != token:
            raise HTTPException(status_code=403, detail="CSRF token mismatch")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = _client_ip(request)
    if _login_blocked(ip):
        log.warning("login rate-limited for %s", ip)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Слишком много попыток — подожди 15 минут"},
            status_code=429,
        )
    try:
        ok = (
            username == ADMIN_USERNAME
            and bool(ADMIN_PASSWORD_HASH)
            and bcrypt.checkpw(password.encode(), ADMIN_PASSWORD_HASH.encode())
        )
    except Exception as e:
        log.error("bcrypt.checkpw failed: %s", e)
        return templates.TemplateResponse(request, "login.html", {"error": f"Auth error: {e}"})
    if ok:
        _failed_logins.pop(ip, None)
        request.session["user"] = username
        return RedirectResponse(url="/admin", status_code=303)
    _failed_logins[ip].append(time.time())
    return templates.TemplateResponse(request, "login.html", {"error": "Invalid credentials"})


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login")
