"""Стрим-токены и защита эндпоинтов камеры."""

import time

import pytest
from fastapi.testclient import TestClient

from geckohome.services import stream_token as st

# ── токены ──


def test_issued_token_verifies():
    assert st.verify_stream_token(st.issue_stream_token())


def test_expired_token_rejected():
    assert not st.verify_stream_token(st.issue_stream_token(ttl=-1))


@pytest.mark.parametrize("bad", ["", "garbage", "123", "abc.def", f"{2**33}.wrongsig"])
def test_malformed_and_forged_tokens_rejected(bad):
    assert not st.verify_stream_token(bad)


def test_tampered_expiry_rejected():
    token = st.issue_stream_token()
    expires, _, sig = token.partition(".")
    # продлеваем срок жизни, подпись прежняя
    assert not st.verify_stream_token(f"{int(expires) + 9999}.{sig}")


def test_secret_is_stable_across_reload():
    token = st.issue_stream_token()
    st._secret = None  # имитируем другой процесс
    assert st.verify_stream_token(token)


# ── эндпоинты ──


@pytest.fixture(scope="module")
def client():
    import pathlib
    import shutil

    from geckohome import paths

    # тестовый PROJECT_ROOT — пустой tempdir: приложению нужны static/ и шаблон
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    pathlib.Path(paths.STATIC_DIR).mkdir(parents=True, exist_ok=True)
    templates_dir = pathlib.Path(paths.TEMPLATES_DIR)
    templates_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(repo_root / "templates" / "stream.html", templates_dir / "stream.html")

    from geckohome.web.app import app

    # без lifespan: нужен только роутинг и middleware, не камера/шедулер
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "path",
    ["/stream", "/api/stream/snapshot", "/hls/index.m3u8"],
)
def test_camera_endpoints_require_auth(client, path):
    assert client.get(path).status_code == 401


def test_camera_endpoints_reject_bad_token(client):
    assert client.get("/stream", params={"t": "1.fake"}).status_code == 401


def test_stream_page_opens_with_valid_token(client):
    r = client.get("/stream", params={"t": st.issue_stream_token()})
    assert r.status_code == 200
    assert "mjpeg" in r.text


def test_snapshot_with_token_passes_auth(client):
    # авторизация пройдена; 503 — кадра нет, монитор не запущен
    r = client.get("/api/stream/snapshot", params={"t": st.issue_stream_token()})
    assert r.status_code == 503


def test_expired_token_on_endpoint(client):
    old = st.issue_stream_token(ttl=-10)
    assert client.get("/stream", params={"t": old}).status_code == 401


def test_web_session_needs_no_token(client):
    import base64
    import json

    from itsdangerous import TimestampSigner

    from geckohome.config import SECRET_KEY

    # собираем session-cookie в формате SessionMiddleware
    payload = base64.b64encode(json.dumps({"user": "admin"}).encode())
    cookie = TimestampSigner(str(SECRET_KEY)).sign(payload).decode()
    r = client.get("/stream", cookies={"session": cookie})
    assert r.status_code == 200


def test_healthz_is_public(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_token_ttl_is_reasonable():
    token = st.issue_stream_token()
    expires = int(token.split(".")[0])
    assert expires - time.time() <= st.TOKEN_TTL + 5
