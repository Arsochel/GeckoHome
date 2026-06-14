"""Centralized filesystem paths.

All runtime data (databases, templates, static assets, timelapse frames,
backups, tunnel state) lives at the *project root* — which is the process
working directory in every supported deployment (Docker ``WORKDIR /app`` and
local ``python -m`` from the repo root). Anchoring paths here (instead of each
module computing them from ``__file__``) keeps them stable regardless of where
the package code physically lives.

Override the root explicitly with ``GECKO_PROJECT_ROOT`` if needed (e.g. tests).
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT: Path = Path(os.getenv("GECKO_PROJECT_ROOT") or Path.cwd()).resolve()

# ── Databases ─────────────────────────────────────────────────────────────────
DB_PATH: str = str(PROJECT_ROOT / "gecko.db")
MEDIA_DB_PATH: str = str(PROJECT_ROOT / "gecko_media.db")
BACKUPS_DIR: str = str(PROJECT_ROOT / "backups")

# ── Web assets ────────────────────────────────────────────────────────────────
TEMPLATES_DIR: str = str(PROJECT_ROOT / "templates")
STATIC_DIR: str = str(PROJECT_ROOT / "static")
FAVICON_PATH: str = str(PROJECT_ROOT / "static" / "favicon.ico")

# ── Timelapse (typically a bind mount) ────────────────────────────────────────
TIMELAPSE_DIR: str = str(PROJECT_ROOT / "timelapse")
TIMELAPSE_FRAMES_DIR: str = str(PROJECT_ROOT / "timelapse" / "frames")
TIMELAPSE_VIDEOS_DIR: str = str(PROJECT_ROOT / "timelapse" / "videos")

# ── Cloudflare tunnel state ───────────────────────────────────────────────────
TUNNEL_URL_FILE: str = str(PROJECT_ROOT / "tunnel_url.txt")
TUNNEL_PID_FILE: str = str(PROJECT_ROOT / "tunnel.pid")
