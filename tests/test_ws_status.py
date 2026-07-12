"""WebSocket /ws/status: авторизация и broadcast."""

import base64
import json

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from starlette.websockets import WebSocketDisconnect

from geckohome.config import SECRET_KEY


@pytest.fixture(scope="module")
def client():
    import pathlib

    from geckohome import paths

    pathlib.Path(paths.STATIC_DIR).mkdir(parents=True, exist_ok=True)
    from geckohome.web.app import app

    return TestClient(app, raise_server_exceptions=False)


def _session_cookie(data: dict) -> str:
    payload = base64.b64encode(json.dumps(data).encode())
    return TimestampSigner(str(SECRET_KEY)).sign(payload).decode()


def test_ws_rejects_anonymous(client):
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect("/ws/status") as ws:
        ws.receive_text()
    assert exc.value.code == 4401


def test_ws_streams_status_to_logged_in_user(client, monkeypatch):
    from geckohome.services import tuya
    from geckohome.web import app as app_module

    monkeypatch.setattr(tuya, "get_lamp_status", lambda lamp: {"online": True, "switch": True})
    monkeypatch.setattr(app_module, "_ws_last_payload", None)

    cookie = _session_cookie({"user": "admin"})
    with client.websocket_connect("/ws/status", headers={"cookie": f"session={cookie}"}) as ws:
        data = json.loads(ws.receive_text())
    assert data["uv"] == {"online": True, "switch": True}
    assert data["heat"]["switch"] is True
