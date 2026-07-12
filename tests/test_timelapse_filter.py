"""Фильтрация кадров таймлапса: diff-метрика, сбор за период, prune дублей."""

import os
import shutil
from datetime import datetime

import cv2
import numpy as np
import pytest

from geckohome import paths
from geckohome.services import timelapse as tl


@pytest.fixture(autouse=True)
def clean_frames_dir():
    shutil.rmtree(paths.TIMELAPSE_FRAMES_DIR, ignore_errors=True)
    yield
    shutil.rmtree(paths.TIMELAPSE_FRAMES_DIR, ignore_errors=True)


def _write_frame(day: str, name: str, brightness: int = 0) -> str:
    folder = os.path.join(paths.TIMELAPSE_FRAMES_DIR, day)
    os.makedirs(folder, exist_ok=True)
    img = np.full((120, 90, 3), brightness, dtype=np.uint8)
    path = os.path.join(folder, name)
    assert cv2.imwrite(path, img)
    return path


# ── _compute_diff ──


def test_identical_frames_have_zero_diff():
    gray = np.zeros((100, 100), dtype=np.uint8)
    assert tl._compute_diff(gray, gray.copy()) == 0.0


def test_changed_region_is_measured_as_percentage():
    g1 = np.zeros((100, 100), dtype=np.uint8)
    g2 = g1.copy()
    g2[:10, :] = 200  # 10% кадра изменилось сильно
    score = tl._compute_diff(g1, g2)
    assert 8 <= score <= 14  # ~10% с поправкой на GaussianBlur по краям


def test_diff_respects_mask():
    g1 = np.zeros((100, 100), dtype=np.uint8)
    g2 = g1.copy()
    g2[:10, :] = 200
    # маска на неизменной области → изменений не видно
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[50:, :] = 255
    assert tl._compute_diff(g1, g2, mask=mask) == 0.0


def test_empty_mask_means_zero_diff():
    g1 = np.zeros((100, 100), dtype=np.uint8)
    g2 = np.full((100, 100), 255, dtype=np.uint8)
    mask = np.zeros((100, 100), dtype=np.uint8)
    assert tl._compute_diff(g1, g2, mask=mask) == 0.0


# ── _collect_frames ──


def test_collect_frames_honors_half_open_window():
    _write_frame("2026-07-10", "115959_00.jpg")  # до окна
    _write_frame("2026-07-10", "120000_00.jpg")  # начало окна — включён
    _write_frame("2026-07-11", "115959_00.jpg")  # внутри
    _write_frame("2026-07-11", "120000_00.jpg")  # конец окна — исключён
    frames = tl._collect_frames(datetime(2026, 7, 10, 12), datetime(2026, 7, 11, 12))
    names = [name for _, name in frames]
    assert names == ["120000_00.jpg", "115959_00.jpg"]


def test_collect_frames_skips_garbage():
    _write_frame("2026-07-10", "130000_00.jpg")
    _write_frame("2026-07-10", "garbage.jpg")  # имя не парсится
    _write_frame("not-a-date", "130000_00.jpg")  # папка не парсится
    frames = tl._collect_frames(datetime(2026, 7, 10, 12), datetime(2026, 7, 11, 12))
    assert [name for _, name in frames] == ["130000_00.jpg"]


def test_collect_frames_without_dir_returns_empty():
    assert tl._collect_frames(datetime(2026, 7, 10), datetime(2026, 7, 11)) == []


# ── _select_changing_frames / _prune_frames ──
# YOLO_MODEL_PATH в тестах не задан → get_model() возвращает None → без маски.


def test_duplicate_frames_are_dropped():
    day = "2026-07-10"
    _write_frame(day, "120000_00.jpg", brightness=0)
    _write_frame(day, "120005_00.jpg", brightness=0)  # дубль
    _write_frame(day, "120010_00.jpg", brightness=255)  # реально новый кадр
    folder = os.path.join(paths.TIMELAPSE_FRAMES_DIR, day)
    pairs = [(folder, n) for n in ("120000_00.jpg", "120005_00.jpg", "120010_00.jpg")]

    kept = tl._select_changing_frames(pairs)

    assert [n for _, n in kept] == ["120000_00.jpg", "120010_00.jpg"]


def test_prune_deletes_dropped_frames_from_disk():
    day = "2026-07-10"
    _write_frame(day, "120000_00.jpg", brightness=0)
    dup = _write_frame(day, "120005_00.jpg", brightness=0)
    keep = _write_frame(day, "120010_00.jpg", brightness=255)
    folder = os.path.join(paths.TIMELAPSE_FRAMES_DIR, day)
    pairs = [(folder, n) for n in ("120000_00.jpg", "120005_00.jpg", "120010_00.jpg")]

    kept = tl._prune_frames(pairs)

    assert len(kept) == 2
    assert not os.path.exists(dup)
    assert os.path.exists(keep)
