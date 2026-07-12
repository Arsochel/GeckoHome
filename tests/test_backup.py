"""Бэкап обеих SQLite-баз: retention и изоляция префиксов."""

import os
import pathlib
import shutil
import sqlite3

import pytest

from geckohome import paths
from geckohome.services.scheduler import backup as bk


@pytest.fixture(autouse=True)
def clean_backups_dir():
    shutil.rmtree(paths.BACKUPS_DIR, ignore_errors=True)
    yield
    shutil.rmtree(paths.BACKUPS_DIR, ignore_errors=True)


def _list(prefix: str) -> list[str]:
    root = pathlib.Path(paths.BACKUPS_DIR)
    if not root.is_dir():
        return []
    if prefix == "gecko":
        return sorted(
            f.name for f in root.glob("gecko_*.db") if not f.name.startswith("gecko_media_")
        )
    return sorted(f.name for f in root.glob(f"{prefix}_*.db"))


def test_backup_covers_both_databases():
    bk.backup_db()
    assert len(_list("gecko")) == 1
    assert len(_list("gecko_media")) == 1


def test_backup_result_is_valid_sqlite():
    bk.backup_db()
    for name in _list("gecko") + _list("gecko_media"):
        con = sqlite3.connect(os.path.join(paths.BACKUPS_DIR, name))
        try:
            assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            con.close()


def test_retention_keeps_media_and_main_separate():
    os.makedirs(paths.BACKUPS_DIR, exist_ok=True)
    # старые бэкапы сверх лимита: 9 основных, 4 медиа
    for i in range(9):
        pathlib.Path(paths.BACKUPS_DIR, f"gecko_2020010{i}_000000.db").touch()
    for i in range(4):
        pathlib.Path(paths.BACKUPS_DIR, f"gecko_media_2020010{i}_000000.db").touch()

    bk.backup_db()

    main, media = _list("gecko"), _list("gecko_media")
    assert len(main) == bk._KEEP_BACKUPS
    assert len(media) == bk._KEEP_MEDIA_BACKUPS
    # свежие бэкапы пережили чистку
    assert main[-1] > "gecko_20200108_000000.db"
    assert media[-1] > "gecko_media_20200103_000000.db"


def test_missing_media_db_is_not_fatal():
    os.remove(paths.MEDIA_DB_PATH)
    bk.backup_db()
    assert len(_list("gecko")) == 1
    assert _list("gecko_media") == []


# ── offsite sync (rclone) ──


def test_offsite_sync_is_noop_without_remote(monkeypatch):
    monkeypatch.setattr(bk.config, "BACKUP_RCLONE_REMOTE", "")
    monkeypatch.setattr(
        bk.subprocess, "run", lambda *a, **k: pytest.fail("rclone не должен вызываться")
    )
    bk._offsite_sync()


def test_offsite_sync_survives_missing_rclone(monkeypatch):
    monkeypatch.setattr(bk.config, "BACKUP_RCLONE_REMOTE", "r2:gecko-backups")
    monkeypatch.setattr(bk.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        bk.subprocess, "run", lambda *a, **k: pytest.fail("rclone не должен вызываться")
    )
    bk._offsite_sync()


def test_offsite_sync_runs_rclone(monkeypatch):
    calls = []
    monkeypatch.setattr(bk.config, "BACKUP_RCLONE_REMOTE", "r2:gecko-backups")
    monkeypatch.setattr(bk.shutil, "which", lambda _: "/usr/bin/rclone")

    class _Result:
        returncode = 0
        stderr = b""

    monkeypatch.setattr(bk.subprocess, "run", lambda cmd, **k: calls.append(cmd) or _Result())
    bk._offsite_sync()

    assert len(calls) == 1
    assert calls[0][:2] == ["/usr/bin/rclone", "sync"]
    assert paths.BACKUPS_DIR in calls[0]
    assert "r2:gecko-backups" in calls[0]
