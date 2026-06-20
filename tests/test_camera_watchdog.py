"""Camera watchdog: restart HLS/mediamtx when dead or stalled, else leave alone."""

import geckohome.services.camera as cam


class _Alive:
    def poll(self):
        return None  # running


def _record(calls, name, is_async=True):
    async def _afn(*a):
        calls.append((name, *a))

    def _fn(*a):
        calls.append((name, *a))

    return _afn if is_async else _fn


async def test_restarts_when_processes_dead(monkeypatch):
    monkeypatch.setattr(cam, "CAMERA_RTSP_URL", "rtsp://x")
    monkeypatch.setattr(cam, "_hls_proc", None)
    monkeypatch.setattr(cam, "_mediamtx_proc", None)
    calls = []
    for n in ("stop_hls", "start_hls", "stop_mediamtx", "start_mediamtx"):
        monkeypatch.setattr(cam, n, _record(calls, n))
    await cam.ensure_alive("/usr/local/bin/mediamtx")
    names = [c[0] for c in calls]
    assert "start_hls" in names
    assert "start_mediamtx" in names


async def test_restarts_when_hls_stalled(monkeypatch):
    monkeypatch.setattr(cam, "CAMERA_RTSP_URL", "rtsp://x")
    monkeypatch.setattr(cam, "_hls_proc", _Alive())  # alive but...
    monkeypatch.setattr(cam, "_hls_stalled", lambda: True)  # ...not advancing
    monkeypatch.setattr(cam, "_mediamtx_proc", _Alive())
    calls = []
    for n in ("stop_hls", "start_hls"):
        monkeypatch.setattr(cam, n, _record(calls, n))
    await cam.ensure_alive("")  # no mediamtx bin -> skip it
    assert ("start_hls",) in calls


async def test_leaves_healthy_alone(monkeypatch):
    monkeypatch.setattr(cam, "CAMERA_RTSP_URL", "rtsp://x")
    monkeypatch.setattr(cam, "_hls_proc", _Alive())
    monkeypatch.setattr(cam, "_hls_stalled", lambda: False)
    monkeypatch.setattr(cam, "_mediamtx_proc", _Alive())
    calls = []
    for n in ("stop_hls", "start_hls", "stop_mediamtx", "start_mediamtx"):
        monkeypatch.setattr(cam, n, _record(calls, n))
    await cam.ensure_alive("/usr/local/bin/mediamtx")
    assert calls == []


async def test_noop_when_camera_unconfigured(monkeypatch):
    monkeypatch.setattr(cam, "CAMERA_RTSP_URL", "")
    calls = []
    monkeypatch.setattr(cam, "start_hls", _record(calls, "start_hls"))
    await cam.ensure_alive("/usr/local/bin/mediamtx")
    assert calls == []
