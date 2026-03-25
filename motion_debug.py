"""
Motion detection debug GUI.
Run separately: python motion_debug.py
"""
import sys
import cv2
import numpy as np
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QSlider, QSpinBox, QGroupBox, QCheckBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage, QPixmap

from dotenv import load_dotenv
load_dotenv()

from config import CAMERA_RTSP_URL

# ── настройки (можно менять слайдерами) ─────────────────────────────────────
THRESHOLD  = 15
MIN_AREA   = 20
MASK_TS    = True   # маскировать timestamp камеры
# ─────────────────────────────────────────────────────────────────────────────


class MotionDebugWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Motion Debug")
        self.resize(1000, 650)

        import os, threading
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        self.cap = cv2.VideoCapture(CAMERA_RTSP_URL, cv2.CAP_FFMPEG)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.prev_gray = None
        self._latest_frame = [None]
        self._running = True
        threading.Thread(target=self._capture_loop, daemon=True).start()

        # ── layout ──────────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        # видео
        self.video_label = QLabel()
        self.video_label.setMinimumSize(720, 480)
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background:#000")
        root.addWidget(self.video_label, stretch=1)

        # панель управления
        panel = QVBoxLayout()
        panel.setAlignment(Qt.AlignmentFlag.AlignTop)
        root.addLayout(panel, stretch=0)

        # статус
        self.status_label = QLabel("—")
        self.status_label.setStyleSheet("font-size:16px;font-weight:bold;padding:8px")
        panel.addWidget(self.status_label)

        # threshold
        tg = QGroupBox("Threshold (пикселей)")
        tl = QVBoxLayout(tg)
        self.thresh_spin = QSpinBox()
        self.thresh_spin.setRange(1, 100)
        self.thresh_spin.setValue(THRESHOLD)
        self.thresh_slider = QSlider(Qt.Orientation.Horizontal)
        self.thresh_slider.setRange(1, 100)
        self.thresh_slider.setValue(THRESHOLD)
        self.thresh_slider.valueChanged.connect(self.thresh_spin.setValue)
        self.thresh_spin.valueChanged.connect(self.thresh_slider.setValue)
        tl.addWidget(self.thresh_spin)
        tl.addWidget(self.thresh_slider)
        panel.addWidget(tg)

        # min area
        ag = QGroupBox("Min Area (площадь контура)")
        al = QVBoxLayout(ag)
        self.area_spin = QSpinBox()
        self.area_spin.setRange(1, 5000)
        self.area_spin.setValue(MIN_AREA)
        self.area_slider = QSlider(Qt.Orientation.Horizontal)
        self.area_slider.setRange(1, 5000)
        self.area_slider.setValue(MIN_AREA)
        self.area_slider.valueChanged.connect(self.area_spin.setValue)
        self.area_spin.valueChanged.connect(self.area_slider.setValue)
        al.addWidget(self.area_spin)
        al.addWidget(self.area_slider)
        panel.addWidget(ag)

        # маска timestamp
        self.mask_cb = QCheckBox("Маскировать timestamp камеры")
        self.mask_cb.setChecked(MASK_TS)
        panel.addWidget(self.mask_cb)

        # макс. площадь в кадре
        self.max_area_label = QLabel("max area: —")
        self.max_area_label.setStyleSheet("font-size:13px;padding:4px")
        panel.addWidget(self.max_area_label)

        panel.addStretch()

        # таймер
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_frame)
        self.timer.start(100)  # 10 fps

    def _capture_loop(self):
        while self._running:
            ret, f = self.cap.read()
            if ret:
                self._latest_frame[0] = f

    def update_frame(self):
        frame = self._latest_frame[0]
        if frame is None:
            return
        self._latest_frame[0] = None

        threshold = self.thresh_spin.value()
        min_area  = self.area_spin.value()
        mask_ts   = self.mask_cb.isChecked()

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        if self.prev_gray is None:
            self.prev_gray = gray
            return

        diff = cv2.absdiff(self.prev_gray, gray)
        if mask_ts:
            h, w = diff.shape
            diff[int(h * 0.88):, :int(w * 0.35)] = 0

        _, thresh = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        big = [c for c in contours if cv2.contourArea(c) > min_area]
        max_area = int(max((cv2.contourArea(c) for c in contours), default=0))
        motion = len(big) > 0

        self.prev_gray = gray

        # рисуем
        debug = frame.copy()
        cv2.drawContours(debug, big, -1, (0, 255, 0), 2)
        color = (0, 255, 0) if motion else (120, 120, 120)
        text = f"MOTION area={int(max((cv2.contourArea(c) for c in big), default=0))}" if motion else f"quiet max={max_area}"
        cv2.putText(debug, text, (10, 35), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)

        # статус
        self.status_label.setText("🟢 MOTION" if motion else "⚪ quiet")
        self.status_label.setStyleSheet(
            f"font-size:16px;font-weight:bold;padding:8px;color:{'#00cc44' if motion else '#888'}"
        )
        self.max_area_label.setText(f"max area: {max_area}  |  min_area: {min_area}")

        # показываем
        debug = cv2.rotate(debug, cv2.ROTATE_90_CLOCKWISE)
        rgb = cv2.cvtColor(debug, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        self.video_label.setPixmap(
            QPixmap.fromImage(qimg).scaled(
                self.video_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def closeEvent(self, event):
        self._running = False
        self.timer.stop()
        self.cap.release()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MotionDebugWindow()
    win.show()
    sys.exit(app.exec())
