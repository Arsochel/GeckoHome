"""Daily SQLite backup job."""

import glob
import logging
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime

from geckohome import config
from geckohome.paths import BACKUPS_DIR as _BACKUP_DIR
from geckohome.paths import DB_PATH as _DB_PATH
from geckohome.paths import MEDIA_DB_PATH as _MEDIA_DB_PATH

log = logging.getLogger(__name__)

_KEEP_BACKUPS = 7
# Медиа-база — фото-BLOB'ы, сотни мегабайт: храним меньше копий.
_KEEP_MEDIA_BACKUPS = 2


def _backup_one(src_path: str, prefix: str, keep: int, stamp: str):
    dest = os.path.join(_BACKUP_DIR, f"{prefix}_{stamp}.db")
    src = sqlite3.connect(src_path)
    src.execute("PRAGMA busy_timeout=5000")  # база живая, WAL — не ждать locked
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    # удаляем старые бэкапы, оставляем keep штук
    files = sorted(
        f
        for f in glob.glob(os.path.join(_BACKUP_DIR, f"{prefix}_*.db"))
        # gecko_* не должен захватывать gecko_media_*
        if prefix != "gecko" or not os.path.basename(f).startswith("gecko_media_")
    )
    for old in files[:-keep]:
        os.remove(old)
    log.info("backup saved: %s (%d total, kept %d)", dest, len(files), keep)


def _offsite_sync():
    """Синк backups/ в облако через rclone, если задан BACKUP_RCLONE_REMOTE.

    Бэкапы на том же диске, что и оригиналы — от сдохшего диска не спасают.
    """
    remote = config.BACKUP_RCLONE_REMOTE
    if not remote:
        return
    rclone = shutil.which("rclone")
    if rclone is None:
        log.warning("BACKUP_RCLONE_REMOTE задан, но rclone не найден в PATH")
        return
    result = subprocess.run(
        [rclone, "sync", _BACKUP_DIR, remote, "--transfers", "2"],
        capture_output=True,
        timeout=1800,
    )
    if result.returncode != 0:
        log.error("rclone sync failed:\n%s", result.stderr.decode(errors="replace")[-500:])
    else:
        log.info("offsite backup synced to %s", remote)


def backup_db():
    os.makedirs(_BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _backup_one(_DB_PATH, "gecko", _KEEP_BACKUPS, stamp)
    if os.path.exists(_MEDIA_DB_PATH):
        try:
            _backup_one(_MEDIA_DB_PATH, "gecko_media", _KEEP_MEDIA_BACKUPS, stamp)
        except sqlite3.Error as e:
            # медиа-бэкап не должен ронять бэкап основной базы
            log.error("media db backup failed: %s", e)
    try:
        _offsite_sync()
    except Exception as e:
        log.error("offsite sync failed: %s", e)
