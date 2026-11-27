import os
from pathlib import Path
from dotenv import load_dotenv

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BASE_DIR.parent

# Load .env from root or backend directory if present
for env_candidate in (ROOT_DIR / ".env", BASE_DIR / ".env"):
    if env_candidate.exists():
        load_dotenv(dotenv_path=env_candidate)
        break

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = os.getenv("DB_PATH", str(DATA_DIR / "attendance.db"))
MODELS_DIR = BASE_DIR / "models"
MODELS_DIR.mkdir(exist_ok=True)

# MongoDB Configuration (Standard URI or Atlas Data API)
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "smart_attendance").strip()
MONGODB_DATA_API_URL = os.getenv("MONGODB_DATA_API_URL", "").strip().rstrip("/")
MONGODB_DATA_API_KEY = os.getenv("MONGODB_DATA_API_KEY", "").strip()
MONGODB_DATA_SOURCE = os.getenv("MONGODB_DATA_SOURCE", "Cluster0").strip()

# General-Purpose Organization / Default Company Settings
ORG_NAME = os.getenv("ORG_NAME", "Smart Attendance Hub")
DEFAULT_ORG_MODE = os.getenv("DEFAULT_ORG_MODE", "general")  # general | office | school | event
DEFAULT_COMPANY_ID = os.getenv("DEFAULT_COMPANY_ID", "DEFAULT_ORG").strip()
DEFAULT_COMPANY_NAME = os.getenv("DEFAULT_COMPANY_NAME", "Default Organization").strip()
DEFAULT_ADMIN_PIN = os.getenv("DEFAULT_ADMIN_PIN", "1234").strip()
SUPER_ADMIN_PIN = os.getenv("SUPER_ADMIN_PIN", DEFAULT_ADMIN_PIN).strip()

# ESP8266 Device Authentication & 60s OTP Window
DEVICE_SECRET_KEY = os.getenv("DEVICE_SECRET_KEY", "esp8266_classroom_key_98765")
OTP_LIFETIME_SECONDS = max(60, int(os.getenv("OTP_LIFETIME_SECONDS", "60"))) if os.getenv("OTP_LIFETIME_SECONDS") in (None, "", "30", "45") else int(os.getenv("OTP_LIFETIME_SECONDS", "60"))

# Face Recognition Configuration
# 'opencv_sface' uses lightweight YuNet + SFace (~110MB RAM, ideal for 512MB Render free tier)
# 'auto' tries InsightFace first then falls back to OpenCV SFace
FACE_BACKEND = os.getenv("FACE_BACKEND", "opencv_sface" if os.getenv("RENDER") else "auto").strip().lower()
FACE_SIMILARITY_THRESHOLD = float(os.getenv("FACE_SIMILARITY_THRESHOLD", "0.38"))
MIN_FACE_DETECTION_CONFIDENCE = float(os.getenv("MIN_FACE_DETECTION_CONFIDENCE", "0.60"))
BLUR_THRESHOLD = float(os.getenv("BLUR_THRESHOLD", "20.0"))

# Server settings (Render / Railway / VPS inject PORT automatically)
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
