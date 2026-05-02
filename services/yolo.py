import logging
import os
import threading

from config import YOLO_MODEL_PATH

log = logging.getLogger(__name__)

_model = None
_lock = threading.Lock()


def get_model():
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is not None:
            return _model
        if not YOLO_MODEL_PATH or not os.path.exists(YOLO_MODEL_PATH):
            return None
        from ultralytics import YOLO
        _model = YOLO(YOLO_MODEL_PATH)
        log.info("YOLO loaded: %s", YOLO_MODEL_PATH)
    return _model
