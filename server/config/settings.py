import os
from pathlib import Path

# Base directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Model path
MODEL_PATH = BASE_DIR / "models" / "yolo11n-fight.pt"
MODEL_PATH1 = BASE_DIR / "models" / "car_crash_model.pt"

# Upload folder untuk file sementara
UPLOAD_FOLDER = BASE_DIR / "uploads"
UPLOAD_FOLDER_CAR = BASE_DIR / "uploads_car"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Konfigurasi Flask
class Config:
    DEBUG = True
    UPLOAD_FOLDER = str(UPLOAD_FOLDER)
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50 MB
