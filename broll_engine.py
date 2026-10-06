import os
import re
import shutil
import subprocess
import urllib.request
import urllib.parse
import json
import hashlib
import uuid
from pathlib import Path
from typing import List, Dict, Optional
from config import TEMP_DIR, get_pexels_api_key

BROLL_CACHE_DIR = TEMP_DIR / "broll"
BROLL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Curated list of high-value visual concept keywords to trigger stock B-roll
VISUAL_KEYWORDS = {
    # Nature & Outdoors
    "mountain", "mountains", "snow", "cave", "tent", "winter", "forest", "trees", 
    "river", "ocean", "beach", "lake", "camping", "hiking", "alaska", "storm",
    "ice", "cold", "frozen", "sunrise", "sunset", "fire", "campfire", "nature",
    
    # Animals & Wildlife
    "bear", "bears", "wolf", "wolves", "dog", "dogs", "lion", "tiger", "shark", "eagle",
    
    # Business, Finance & Tech
    "money", "cash", "dollar", "wealth", "rich", "crypto", "bitcoin", "stocks",
    "trading", "office", "computer", "coding", "robot", "artificial intelligence",
    "phone", "smartphone", "success", "luxury", "airplane", "private jet",
    
    # Fitness, Health & Sports
    "gym", "workout", "running", "boxing", "training", "fitness", "muscle", "sports",
    
    # Daily Life & Emotion
    "coffee", "cooking", "food", "car", "driving", "city", "traffic", "night",
    "danger", "party", "travel", "adventure"
}

def clean_token(token: str) -> str:
    return re.sub(r"[^a-zA-Z]", "", token).lower()

def search_pexels_video(query: str, api_key: str = None) -> Optional[str]:
    """Queries Pexels Video API for a vertical portrait HD video matching query."""
    key = api_key or get_pexels_api_key()
    if not key:
        return None
        
    encoded = urllib.parse.quote_plus(query)
    url = f"https://api.pexels.com/videos/search?query={encoded}&orientation=portrait&size=medium&per_page=5"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": key,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            videos = data.get("videos", [])
            if not videos:
                return None
                
            # Pick best portrait video
            best_link = None
            for v in videos:
                files = v.get("video_files", [])
                # Look for 1080x1920 or 720x1280
                for f in files:
                    h = f.get("height") or 0
                    w = f.get("width") or 0
                    if h > w and h >= 720:
                        best_link = f.get("link")
                        if h >= 1080:
                            return best_link
                if best_link:
                    return best_link
            return best_link
    except Exception as e:
        print(f"  [B-Roll Engine] Pexels search error for '{query}': {e}")
        return None

def is_decodable_video(path: Path) -> bool:
    """
    Validates that a media file actually decodes, using ffprobe.

    A size check alone cannot tell a good file from a truncated-but-complete one, and the
    cache key is a hash of the remote URL, so a poisoned entry would otherwise be served
    forever and every render touching that keyword would fail.
    """
    try:
        if not path.exists() or path.stat().st_size <= 50000:
            return False
    except OSError:
        return False

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        # No probe available: fall back to a size sanity check rather than blocking the render.
        return True

    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type", "-of", "default=noprint_wrappers=1:nokey=1",
             str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20
        )
        return proc.returncode == 0 and "video" in (proc.stdout or "").lower()
    except Exception:
        return False


def download_broll(url: str, dest_path: Path) -> bool:
    """Downloads B-roll video to local destination atomically and safely."""
    if dest_path.exists():
        if is_decodable_video(dest_path):
            return True
        # Cached entry failed decode validation (truncated CDN response, upstream rotation).
        # Evict it so the retry below can re-fetch instead of re-serving the poison.
        try:
            dest_path.unlink()
        except Exception:
            pass

    tmp_path = dest_path.with_suffix(f".tmp_{uuid.uuid4().hex[:6]}.mp4")
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=25) as resp, open(tmp_path, "wb") as out_f:
            while True:
                chunk = resp.read(1024 * 64)
                if not chunk:
                    break
                out_f.write(chunk)
        if tmp_path.exists() and tmp_path.stat().st_size > 50000:
            if is_decodable_video(tmp_path):
                try:
                    tmp_path.replace(dest_path)
                except Exception:
                    if not (dest_path.exists() and dest_path.stat().st_size > 50000):
                        return False
                return dest_path.exists() and dest_path.stat().st_size > 50000
            # Downloaded but undecodable: do not cache it.
            try:
                tmp_path.unlink()
            except Exception:
                pass
            return False
        return False
    except Exception as e:
        print(f"  [B-Roll Engine] Download error from {url}: {e}")
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        return False

def find_broll_cues(words: List[Dict], clip_duration: float, max_cues: int = 2) -> List[Dict]:
    """
    Identifies optimal timestamp windows (3-4 seconds each) for B-roll overlays
    based on spoken visual keywords. Never in the first 4s or last 4s of clip.
    """
    cues = []
    min_start = 4.0
    max_end = max(4.0, clip_duration - 4.0)
    last_broll_end = -10.0

    for idx, w in enumerate(words):
        start_t = w["start"]
        token = clean_token(w["word"])
        
        if start_t < min_start or start_t >= (max_end - 1.0):
            continue
            
        matched_kw = None
        if idx + 1 < len(words):
            next_token = clean_token(words[idx + 1]["word"])
            bigram = f"{token} {next_token}"
            if bigram in VISUAL_KEYWORDS:
                matched_kw = bigram

        if not matched_kw and token in VISUAL_KEYWORDS:
            matched_kw = token

        if matched_kw:
            # Check spacing from previous B-roll (at least 12 seconds apart)
            if start_t - last_broll_end < 12.0:
                continue
                
            duration = 3.5  # 3.5 seconds B-roll duration
            end_t = min(start_t + duration, max_end)
            if (end_t - start_t) < 1.0:
                continue
            
            cues.append({
                "keyword": matched_kw,
                "start": round(start_t, 2),
                "end": round(end_t, 2),
                "duration": round(end_t - start_t, 2)
            })
            last_broll_end = end_t
            if len(cues) >= max_cues:
                break
                
    return cues

def plan_and_fetch_brolls(words: List[Dict], clip_duration: float, max_brolls: int = 2) -> List[Dict]:
    """
    Finds keywords, fetches matching Pexels B-roll, and returns ready overlay descriptors.
    """
    cues = find_broll_cues(words, clip_duration, max_cues=max_brolls)
    prepared = []
    
    for idx, cue in enumerate(cues):
        kw = cue["keyword"]
        safe_kw = re.sub(r"[^a-zA-Z0-9]", "_", kw)
        
        print(f"  [AI B-Roll] Found visual moment at {cue['start']}s: '{kw.upper()}'")
        video_url = search_pexels_video(kw)
        if not video_url:
            print(f"  [AI B-Roll] No Pexels video found for '{kw}', skipping.")
            continue
            
        url_hash = hashlib.md5(video_url.encode("utf-8")).hexdigest()[:8]
        local_file = BROLL_CACHE_DIR / f"{safe_kw}_{url_hash}.mp4"
            
        print(f"  [AI B-Roll] Downloading vertical stock B-roll for '{kw}'...")
        if download_broll(video_url, local_file):
            cue["file_path"] = local_file
            prepared.append(cue)
            print(f"  [AI B-Roll] Ready: {local_file.name} ({cue['duration']}s overlay at {cue['start']}s)")
            
    return prepared
