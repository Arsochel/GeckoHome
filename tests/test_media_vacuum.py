"""VACUUM медиа-базы: файл ужимается после удаления фото."""

import os

from geckohome import paths
from geckohome.database import delete_photo, save_photo, vacuum_media_db


async def test_vacuum_reclaims_space_after_delete():
    blob = os.urandom(512 * 1024)  # 512 KB несжимаемого
    ids = [await save_photo(blob) for _ in range(4)]
    size_full = os.path.getsize(paths.MEDIA_DB_PATH)
    assert size_full > 4 * 512 * 1024

    for pid in ids:
        await delete_photo(pid)
    # после DELETE файл не ужался — место в freelist
    assert os.path.getsize(paths.MEDIA_DB_PATH) == size_full

    await vacuum_media_db()
    assert os.path.getsize(paths.MEDIA_DB_PATH) < size_full // 10


async def test_vacuum_on_empty_db_is_noop():
    await vacuum_media_db()  # не должен упасть
