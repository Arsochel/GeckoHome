"""Rate limit формы логина."""

import pathlib
import shutil

import pytest
from fastapi.testclient import TestClient

from geckohome import paths
from geckohome.web.routers import auth as auth_module


@pytest.fixture(scope="module")
def client():
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    pathlib.Path(paths.STATIC_DIR).mkdir(parents=True, exist_ok=True)
    templates_dir = pathlib.Path(paths.TEMPLATES_DIR)
    templates_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(repo_root / "templates" / "login.html", templates_dir / "login.html")

    from geckohome.web.app import app

    return TestClient(app, raise_server_exceptions=False)


def _attempt(client, ip: str):
    return client.post(
        "/login",
        data={"username": "admin", "password": "wrong"},
        headers={"x-forwarded-for": ip},
    )


def test_login_blocked_after_max_failures(client):
    ip = "10.0.0.1"
    for _ in range(auth_module._LOGIN_MAX_FAILURES):
        assert _attempt(client, ip).status_code == 200  # форма с ошибкой
    assert _attempt(client, ip).status_code == 429


def test_limit_is_per_ip(client):
    ip = "10.0.0.2"
    for _ in range(auth_module._LOGIN_MAX_FAILURES + 1):
        _attempt(client, ip)
    assert _attempt(client, "10.0.0.3").status_code == 200


def test_window_expiry_unblocks(client, monkeypatch):
    ip = "10.0.0.4"
    for _ in range(auth_module._LOGIN_MAX_FAILURES + 1):
        _attempt(client, ip)
    assert _attempt(client, ip).status_code == 429
    # отматываем все зафиксированные фейлы за пределы окна
    old = [t - auth_module._LOGIN_WINDOW - 1 for t in auth_module._failed_logins[ip]]
    auth_module._failed_logins[ip].clear()
    auth_module._failed_logins[ip].extend(old)
    assert _attempt(client, ip).status_code == 200
