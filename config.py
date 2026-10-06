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
# Candidate clip window geometry. These are the SINGLE source of truth: both the web GUI
# (app.py) and the CLI (clipper.py) must generate the same windows for the same video, so
# clip start times and scores are reproducible regardless of entry point.
MIN_CLIP_DURATION = 35.0  # seconds
MAX_CLIP_DURATION = 58.5  # seconds (stays under the 60s Shorts limit; sweet spot 42-52s)
CLIP_WINDOW_STEP = 20.0    # seconds between consecutive window start points
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920

# Pexels AI B-Roll Configuration (Optional)
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "").strip()

def get_pexels_api_key() -> str:
    """Returns Pexels API key for automatic B-roll video fetching."""
    return os.environ.get("PEXELS_API_KEY", PEXELS_API_KEY).strip()
