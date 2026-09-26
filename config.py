import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
TEMP_DIR = BASE_DIR / "temp"

# Ensure directories exist
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# API Configuration
KEY_FILE = Path.home() / ".gemini" / "config" / "jev_key.txt"

def get_jev_api_key() -> str:
    """Reads Jev key securely from environment variable or local config."""
    env_key = os.environ.get("JEV_API_KEY", "").strip()
    if env_key:
        return env_key
    if KEY_FILE.exists():
        try:
            with open(KEY_FILE, "r", encoding="utf-8") as f:
                key = f.read().strip()
                if key:
                    return key
        except Exception:
            pass
    return ""

JEV_ENDPOINT = os.environ.get("JEV_ENDPOINT", "https://www.jevai.org/api/v1/decisions").strip()

# Video Configuration
MIN_CLIP_DURATION = 25.0  # seconds
MAX_CLIP_DURATION = 60.0  # seconds
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920

# Pexels AI B-Roll Configuration (Optional)
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "").strip()

def get_pexels_api_key() -> str:
    """Returns Pexels API key for automatic B-roll video fetching."""
    return os.environ.get("PEXELS_API_KEY", PEXELS_API_KEY).strip()
