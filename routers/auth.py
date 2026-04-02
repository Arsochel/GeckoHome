import secrets

import bcrypt

from fastapi import APIRouter, HTTPException, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import ADMIN_USERNAME, ADMIN_PASSWORD_HASH

router = APIRouter()
templates = Jinja2Templates(directory="templates")


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
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == ADMIN_USERNAME and ADMIN_PASSWORD_HASH and bcrypt.checkpw(password.encode(), ADMIN_PASSWORD_HASH.encode()):
        request.session["user"] = username
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login")
