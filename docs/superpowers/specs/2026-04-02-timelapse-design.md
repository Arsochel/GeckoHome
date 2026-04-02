# Timelapse Feature Design

**Date:** 2026-04-02  
**Status:** Approved

## Overview

Daily timelapse of the gecko terrarium sent to Telegram at 12:00. Frames captured every 2 minutes from the existing motion monitor stream. Three speed variants sent on the first day to pick the best one, then the unused variants are removed.

## Frame Capture

- **Job:** `capture_timelapse_frame` — APScheduler interval, every 2 minutes
- **Source:** `motion_monitor.get_latest_frame()` (no new RTSP connection)
- **Transform:** `cv2.ROTATE_90_CLOCKWISE` (same as MJPEG stream)
- **Output:** `timelapse/frames/YYYY-MM-DD/HHMMss.jpg`
- **Skip condition:** frame is None (monitor not running) — silent skip, no error

Approximate yield: ~720 frames/day.

## Generation & Delivery

- **Job:** `generate_and_send_timelapse` — APScheduler cron, daily at 12:00
- **Source:** frames from **yesterday's** folder (`date.today() - timedelta(days=1)`)
- **Skip condition:** fewer than 10 frames (camera was down)
- **Variants (first day only):** 3 videos at 15fps (~48s), 24fps (~30s), 30fps (~24s)
- **After variant selection:** single video at chosen fps
- **Telegram caption format:** `"🎬 Таймлапс 2026-04-01 • 15fps"`
- **Cleanup:** delete frame folder and temp video files after sending

### ffmpeg command

```
ffmpeg -y -framerate {fps} -pattern_type glob -i "*.jpg"
  -vf "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2"
  -c:v libx264 -preset fast -crf 23
  output.mp4
```

## Recipients

- **Testing phase:** hardcoded `TIMELAPSE_TEST_RECIPIENTS` in `config.py` — your ID only
- **After variant selection:** switch to `TELEGRAM_SUPER_ADMINS`

## File Structure

```
GeckoHome/
└── timelapse/
    └── frames/
        └── 2026-04-02/
            ├── 080000.jpg
            ├── 080200.jpg
            └── ...
```

`timelapse/` added to `.gitignore`.

## New Files / Changes

| File | Change |
|---|---|
| `services/timelapse.py` | New — capture + generate logic |
| `services/scheduler.py` | Add 2 new jobs: `capture_timelapse_frame`, `generate_and_send_timelapse` |
| `config.py` | Add `TIMELAPSE_TEST_RECIPIENTS: list[int]` |
| `.gitignore` | Add `timelapse/` |

## Speed Variant Workflow

1. First run sends all 3 variants (15/24/30fps)
2. User picks one
3. Remove the multi-variant logic, hardcode chosen fps
4. Switch recipients from test ID to `TELEGRAM_SUPER_ADMINS`
