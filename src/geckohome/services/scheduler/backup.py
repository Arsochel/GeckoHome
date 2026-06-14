"""Daily SQLite backup job."""

import glob
import logging
import os
import sqlite3
from datetime import datetime

from geckohome.paths import BACKUPS_DIR as _BACKUP_DIR
from geckohome.paths import DB_PATH as _DB_PATH

log = logging.getLogger(__name__)

_KEEP_BACKUPS = 7


def backup_db():
    os.makedirs(_BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(_BACKUP_DIR, f"gecko_{stamp}.db")
    src = sqlite3.connect(_DB_PATH)
    dst = sqlite3.connect(dest)
    src.backup(dst)
    src.close()
    dst.close()
    # удаляем старые бэкапы, оставляем _KEEP_BACKUPS
    files = sorted(glob.glob(os.path.join(_BACKUP_DIR, "gecko_*.db")))
    for old in files[:-_KEEP_BACKUPS]:
        os.remove(old)
    log.info("backup saved: %s (%d total, kept %d)", dest, len(files), _KEEP_BACKUPS)
