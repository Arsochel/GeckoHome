"""Shared test fixtures.

The project root (and therefore every data path) is redirected to a throwaway
temp directory *before* any ``geckohome`` module is imported, so tests never
touch real databases or .env-driven config.
"""

import os
import tempfile

# Must be set before importing geckohome.paths / geckohome.config.
os.environ["GECKO_PROJECT_ROOT"] = tempfile.mkdtemp(prefix="geckohome_test_")
os.environ.setdefault("TELEGRAM_SUPER_ADMIN", "111,222")
os.environ.setdefault("TELEGRAM_ADMIN", "333")

import pathlib  # noqa: E402

import pytest_asyncio  # noqa: E402

from geckohome import paths  # noqa: E402
from geckohome.database import _init_media_db, init_db  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def fresh_db():
    """Give every test an empty, freshly-migrated pair of SQLite databases."""
    root = pathlib.Path(paths.PROJECT_ROOT)
    for name in ("gecko.db", "gecko_media.db"):
        (root / name).unlink(missing_ok=True)
    await init_db()
    await _init_media_db()
    yield
