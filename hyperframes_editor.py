import os
import re
import sys
import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

from config import BASE_DIR, OUTPUT_DIR as CLI_OUTPUT_DIR
from video_cutter import get_ffmpeg_path

WORKSPACE_DIR = BASE_DIR / "hyperframes_studio"

def ensure_workspace(workspace_dir: Path = WORKSPACE_DIR) -> Path:
    """
    Ensures that the HyperFrames composition workspace exists and contains
    all necessary configuration and vendor assets.
    """
    workspace_dir.mkdir(parents=True, exist_ok=True)
    vendor_dir = workspace_dir / "vendor"
    vendor_dir.mkdir(parents=True, exist_ok=True)

    # 1. hyperframes.json
    hf_json_path = workspace_dir / "hyperframes.json"
    if not hf_json_path.exists():
        hf_config = {
            "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
            "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
            "paths": {
                "blocks": "compositions",
                "components": "compositions/components",
                "assets": "assets"
            },
            "media": {
                "autoProxy": True
            }
        }
        hf_json_path.write_text(json.dumps(hf_config, indent=2), encoding="utf-8")

    # 2. package.json
    pkg_json_path = workspace_dir / "package.json"
    if not pkg_json_path.exists():
        pkg_config = {
            "name": "hyperframes-studio",
            "private": True,
            "type": "module",
            "dependencies": {
                "gsap": "^3.12.5",
                "hyperframes": "^0.8.142"
            }
        }
        pkg_json_path.write_text(json.dumps(pkg_config, indent=2), encoding="utf-8")

    # 3. meta.json
    meta_json_path = workspace_dir / "meta.json"
    if not meta_json_path.exists():
        meta_config = {
            "id": "hyperframes-studio",
            "name": "hyperframes-studio"
        }
        meta_json_path.write_text(json.dumps(meta_config, indent=2), encoding="utf-8")

    # 4. vendor/gsap.min.js
    dest_gsap = vendor_dir / "gsap.min.js"
    if not dest_gsap.exists():
        source_gsap = BASE_DIR / "assets" / "vendor" / "gsap.min.js"
        if source_gsap.exists():
            shutil.copy2(source_gsap, dest_gsap)
        else:
            for candidate in BASE_DIR.glob("**/gsap.min.js"):
                if candidate.is_file():
                    shutil.copy2(candidate, dest_gsap)
                    break

    # 5. Ensure dependencies installed
    nm_dir = workspace_dir / "node_modules"
    if not nm_dir.exists():
        try:
            if shutil.which("bun"):
                subprocess.run(["bun", "install"], cwd=str(workspace_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            elif shutil.which("npm"):
                npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
                subprocess.run([npm_cmd, "install", "--no-audit", "--no-fund"], cwd=str(workspace_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except Exception:
            pass

    return workspace_dir

# Style presets
STYLES = {
    "viral_pop": {
        "name": "Viral Pop (Alex Hormozi)",
        "font": "Impact",
        "active_color": "#FFE600",
        "active_glow": "rgba(255, 230, 0, 0.75)",
        "active_scale": 1.16,
        "pill_bg": "rgba(0, 0, 0, 0.65)",
        "pill_border": "rgba(255, 255, 255, 0.12)",
        "badge_icon": "🔥",
        "badge_color": "#FFE600"
    },
    "mrbeast_hyper": {
        "name": "MrBeast Hyper (High Stakes)",
        "font": "Impact",
        "active_color": "#39FF14",
        "active_glow": "rgba(57, 255, 20, 0.85)",
        "active_scale": 1.20,
        "pill_bg": "rgba(10, 10, 15, 0.80)",
        "pill_border": "rgba(57, 255, 20, 0.30)",
        "badge_icon": "🏆",
        "badge_color": "#FFD700"
    },
    "desi_comedy": {
        "name": "Desi Comedy (Bollywood / Memes)",
        "font": "Impact",
        "active_color": "#FF9933",
        "active_glow": "rgba(255, 153, 51, 0.85)",
        "active_scale": 1.18,
        "pill_bg": "rgba(15, 5, 20, 0.75)",
        "pill_border": "rgba(255, 20, 147, 0.35)",
        "badge_icon": "😂",
        "badge_color": "#FF1493"
    },
    "survival_arctic": {
        "name": "Survival Adventure (Outdoor Boys)",
        "font": "Impact",
        "active_color": "#FFE600",
        "active_glow": "rgba(255, 230, 0, 0.75)",
        "active_scale": 1.15,
        "pill_bg": "rgba(0, 0, 0, 0.65)",
        "pill_border": "rgba(255, 255, 255, 0.12)",
        "badge_icon": "❄️",
        "badge_color": "#00F2FE"
    },
    "clean_minimal": {
        "name": "Clean Minimal (Podcast / Commentary)",
        "font": "Arial",
        "active_color": "#FFFFFF",
        "active_glow": "rgba(255, 255, 255, 0.60)",
        "active_scale": 1.10,
        "pill_bg": "rgba(15, 20, 30, 0.60)",
        "pill_border": "rgba(255, 255, 255, 0.15)",
        "badge_icon": "🎙️",
        "badge_color": "#F1F5F9"
    },
    "bold_pop": {
        "name": "Bold Pop (Opus Classic)",
        "font": "Impact",
        "active_color": "#FFE600",
        "active_glow": "rgba(255, 230, 0, 0.75)",
        "active_scale": 1.16,
        "pill_bg": "rgba(0, 0, 0, 0.65)",
        "pill_border": "rgba(255, 255, 255, 0.12)",
        "badge_icon": "🔥",
        "badge_color": "#FFE600"
    },
    "karaoke": {
        "name": "Karaoke Highlight",
        "font": "Arial",
        "active_color": "#00E5FF",
        "active_glow": "rgba(0, 229, 255, 0.75)",
        "active_scale": 1.12,
        "pill_bg": "rgba(0, 0, 0, 0.65)",
        "pill_border": "rgba(0, 229, 255, 0.30)",
        "badge_icon": "🎤",
        "badge_color": "#00E5FF"
    },
    "boxed_clean": {
        "name": "Boxed Clean",
        "font": "Arial",
        "active_color": "#FFE600",
        "active_glow": "rgba(255, 230, 0, 0.50)",
        "active_scale": 1.10,
        "pill_bg": "rgba(10, 10, 15, 0.90)",
        "pill_border": "rgba(255, 255, 255, 0.20)",
        "badge_icon": "⬛",
        "badge_color": "#F1F5F9"
    },
    "minimal_caption": {
        "name": "Minimal Caption",
        "font": "Arial",
        "active_color": "#F0F0F0",
        "active_glow": "rgba(240, 240, 240, 0.40)",
        "active_scale": 1.05,
        "pill_bg": "rgba(15, 20, 25, 0.60)",
        "pill_border": "rgba(255, 255, 255, 0.10)",
        "badge_icon": "📝",
        "badge_color": "#CBD5E1"
    },
    "hormozi": {
        "name": "Hormozi Classic",
        "font": "Impact",
        "active_color": "#FFE600",
        "active_glow": "rgba(255, 230, 0, 0.75)",
        "active_scale": 1.16,
        "pill_bg": "rgba(0, 0, 0, 0.65)",
        "pill_border": "rgba(255, 255, 255, 0.12)",
        "badge_icon": "⚡",
        "badge_color": "#FFE600"
    },
    "beast": {
        "name": "Beast Viral",
        "font": "Impact",
        "active_color": "#FFD700",
        "active_glow": "rgba(255, 215, 0, 0.85)",
        "active_scale": 1.20,
        "pill_bg": "rgba(10, 10, 15, 0.80)",
        "pill_border": "rgba(255, 215, 0, 0.35)",
        "badge_icon": "🌟",
        "badge_color": "#FFD700"
    },
    "neon_green": {
        "name": "Cyber Neon",
        "font": "Impact",
        "active_color": "#39FF14",
        "active_glow": "rgba(57, 255, 20, 0.85)",
        "active_scale": 1.18,
        "pill_bg": "rgba(5, 15, 10, 0.75)",
        "pill_border": "rgba(57, 255, 20, 0.30)",
        "badge_icon": "🟢",
        "badge_color": "#39FF14"
    },
    "red_punch": {
        "name": "Red Punch",
        "font": "Impact",
        "active_color": "#FF3B30",
        "active_glow": "rgba(255, 59, 48, 0.85)",
        "active_scale": 1.18,
        "pill_bg": "rgba(20, 5, 5, 0.75)",
        "pill_border": "rgba(255, 59, 48, 0.35)",
        "badge_icon": "🔴",
        "badge_color": "#FF3B30"
    },
    "clean_white": {
        "name": "Minimal White",
        "font": "Arial",
        "active_color": "#FFFFFF",
        "active_glow": "rgba(255, 255, 255, 0.60)",
        "active_scale": 1.10,
        "pill_bg": "rgba(15, 20, 30, 0.60)",
        "pill_border": "rgba(255, 255, 255, 0.15)",
        "badge_icon": "⚪",
        "badge_color": "#FFFFFF"
    },
    "netflix_standard": {
        "name": "Netflix Standard",
        "font": "Arial",
        "active_color": "#F5F5F5",
        "active_glow": "rgba(245, 245, 245, 0.40)",
        "active_scale": 1.05,
        "pill_bg": "rgba(0, 0, 0, 0.70)",
        "pill_border": "rgba(255, 255, 255, 0.10)",
        "badge_icon": "🎬",
        "badge_color": "#EF4444"
    },
    "bbc_sdh": {
        "name": "BBC SDH Boxed",
        "font": "Arial",
        "active_color": "#FFE600",
        "active_glow": "rgba(255, 230, 0, 0.40)",
        "active_scale": 1.05,
        "pill_bg": "rgba(0, 0, 0, 0.95)",
        "pill_border": "rgba(255, 255, 255, 0.15)",
        "badge_icon": "📺",
        "badge_color": "#FFE600"
    },
    "cinematic_auteur": {
        "name": "Cinematic Auteur",
        "font": "Georgia",
        "active_color": "#E8F0F8",
        "active_glow": "rgba(232, 240, 248, 0.50)",
        "active_scale": 1.08,
        "pill_bg": "rgba(10, 10, 15, 0.65)",
        "pill_border": "rgba(232, 240, 248, 0.20)",
        "badge_icon": "🎞️",
        "badge_color": "#FBBF24"
    },
    "kinetic_pop": {
        "name": "Kinetic Pop",
        "font": "Impact",
        "active_color": "#FFE600",
        "active_glow": "rgba(255, 230, 0, 0.85)",
        "active_scale": 1.20,
        "pill_bg": "rgba(0, 0, 0, 0.70)",
        "pill_border": "rgba(255, 230, 0, 0.35)",
        "badge_icon": "⚡",
        "badge_color": "#FFE600"
    },
    "wild_den": {
        "name": "Wild Den Bold",
        "font": "Impact",
        "active_color": "#FFE600",
        "active_glow": "rgba(255, 230, 0, 0.75)",
        "active_scale": 1.15,
        "pill_bg": "rgba(0, 0, 0, 0.65)",
        "pill_border": "rgba(255, 255, 255, 0.12)",
        "badge_icon": "🏔️",
        "badge_color": "#00F2FE"
    },
    "iman_gadzhi": {
        "name": "Iman Gadzhi Luxury",
        "font": "Georgia",
        "active_color": "#FFD700",
        "active_glow": "rgba(255, 215, 0, 0.80)",
        "active_scale": 1.15,
        "pill_bg": "rgba(15, 10, 5, 0.75)",
        "pill_border": "rgba(255, 215, 0, 0.30)",
        "badge_icon": "👑",
        "badge_color": "#FBBF24"
    },
    "joe_rogan": {
        "name": "Joe Rogan Podcast",
        "font": "Arial",
        "active_color": "#FF3B30",
        "active_glow": "rgba(255, 59, 48, 0.75)",
        "active_scale": 1.14,
        "pill_bg": "rgba(10, 10, 12, 0.85)",
        "pill_border": "rgba(255, 59, 48, 0.30)",
        "badge_icon": "🎙️",
        "badge_color": "#EF4444"
    },
    "ali_abdaal": {
        "name": "Ali Abdaal Clean",
        "font": "Arial",
        "active_color": "#38BDF8",
        "active_glow": "rgba(56, 189, 248, 0.70)",
        "active_scale": 1.10,
        "pill_bg": "rgba(15, 23, 42, 0.65)",
        "pill_border": "rgba(56, 189, 248, 0.25)",
        "badge_icon": "📚",
        "badge_color": "#38BDF8"
    },
    "vox_explainer": {
        "name": "Vox Journalism",
        "font": "Arial",
        "active_color": "#FFE600",
        "active_glow": "rgba(255, 230, 0, 0.80)",
        "active_scale": 1.15,
        "pill_bg": "rgba(0, 0, 0, 0.85)",
        "pill_border": "rgba(255, 230, 0, 0.35)",
        "badge_icon": "📰",
        "badge_color": "#FFE600"
    },
    "andrew_huberman": {
        "name": "Huberman Lab",
        "font": "Arial",
        "active_color": "#2DD4BF",
        "active_glow": "rgba(45, 212, 191, 0.70)",
        "active_scale": 1.10,
        "pill_bg": "rgba(10, 20, 20, 0.70)",
        "pill_border": "rgba(45, 212, 191, 0.25)",
        "badge_icon": "🧠",
        "badge_color": "#2DD4BF"
    },
    "david_goggins": {
        "name": "David Goggins Savage",
        "font": "Impact",
        "active_color": "#FF4500",
        "active_glow": "rgba(255, 69, 0, 0.90)",
        "active_scale": 1.22,
        "pill_bg": "rgba(20, 10, 5, 0.80)",
        "pill_border": "rgba(255, 69, 0, 0.40)",
        "badge_icon": "🔥",
        "badge_color": "#FF4500"
    },
    "luke_belmar": {
        "name": "Belmar Matrix Glitch",
        "font": "Impact",
        "active_color": "#22C55E",
        "active_glow": "rgba(34, 197, 94, 0.85)",
        "active_scale": 1.18,
        "pill_bg": "rgba(5, 15, 10, 0.85)",
        "pill_border": "rgba(34, 197, 94, 0.35)",
        "badge_icon": "⚡",
        "badge_color": "#22C55E"
    },
    "diary_of_a_ceo": {
        "name": "Diary of a CEO",
        "font": "Arial",
        "active_color": "#E2E8F0",
        "active_glow": "rgba(226, 232, 240, 0.60)",
        "active_scale": 1.10,
        "pill_bg": "rgba(15, 15, 18, 0.75)",
        "pill_border": "rgba(255, 255, 255, 0.15)",
        "badge_icon": "💼",
        "badge_color": "#E2E8F0"
    },
    "fintech_pro": {
        "name": "Fintech Ticker",
        "font": "Arial",
        "active_color": "#10B981",
        "active_glow": "rgba(16, 185, 129, 0.75)",
        "active_scale": 1.12,
        "pill_bg": "rgba(5, 20, 15, 0.80)",
        "pill_border": "rgba(16, 185, 129, 0.30)",
        "badge_icon": "📈",
        "badge_color": "#10B981"
    },
    "anime_shonen": {
        "name": "Shonen Action",
        "font": "Impact",
        "active_color": "#FF8C00",
        "active_glow": "rgba(255, 140, 0, 0.85)",
        "active_scale": 1.20,
        "pill_bg": "rgba(25, 10, 20, 0.80)",
        "pill_border": "rgba(255, 140, 0, 0.35)",
        "badge_icon": "⚔️",
        "badge_color": "#FF8C00"
    },
    "retro_vhs": {
        "name": "Retro VHS Synthwave",
        "font": "Impact",
        "active_color": "#F43F5E",
        "active_glow": "rgba(244, 63, 94, 0.85)",
        "active_scale": 1.16,
        "pill_bg": "rgba(20, 5, 25, 0.80)",
        "pill_border": "rgba(244, 63, 94, 0.35)",
        "badge_icon": "📼",
        "badge_color": "#F43F5E"
    },
    "streetwear_hype": {
        "name": "Streetwear Hype",
        "font": "Impact",
        "active_color": "#FACC15",
        "active_glow": "rgba(250, 204, 21, 0.80)",
        "active_scale": 1.18,
        "pill_bg": "rgba(10, 10, 10, 0.90)",
        "pill_border": "rgba(250, 204, 21, 0.35)",
        "badge_icon": "👟",
        "badge_color": "#FACC15"
    }
}

KEYWORD_RULES = {
    # Danger / Shock / Tension (Red)
    r"\b(fire|danger|warning|dead|died|destroy|destroyed|shock|alert|sneak|disappeared|khatam|mar|pagal|omg|never|ruin|trapped|blood|kill|loss|mistake)\b": {
        "color": "#FF3B30",
        "glow": "rgba(255, 59, 48, 0.85)"
    },
    # Money / Wealth / Value (Gold)
    r"(\$|\b(money|dollar|dollars|cash|win|won|winner|paisa|crore|lakh|rich|billion|million|prize|cost|price|expensive|paid)\b)": {
        "color": "#FFD700",
        "glow": "rgba(255, 215, 0, 0.85)"
    },
    # Nature / Food / Growth (Green)
    r"\b(survival|food|eat|meat|hunt|ridge|valley|forest|jungle|green|trees|alive|nature|animal|fish|water|fuel)\b": {
        "color": "#30D158",
        "glow": "rgba(48, 209, 88, 0.85)"
    },
    # Cold / Digital / Ice (Cyan)
    r"\b(ice|snow|cold|frost|froze|frozen|freeze|cave|sleeping|bag|pillow|furry|winter|arctic|tech|digital|ai)\b": {
        "color": "#00F2FE",
        "glow": "rgba(0, 242, 254, 0.85)"
    },
    # Comedy / Slang / High-Energy (Orange & Pink)
    r"\b(bhai|yaar|kya|bro|chat|speed|prank|joke|laugh|funny|crazy|insane|troll|epic|lit|meme|w|l|noob|goat)\b": {
        "color": "#FF9933",
        "glow": "rgba(255, 153, 51, 0.85)"
    }
}

def get_keyword_meta(text: str) -> Optional[Dict[str, str]]:
    low = text.lower()
    for pat, meta in KEYWORD_RULES.items():
        if re.search(pat, low):
            return meta
    return None

def extract_transcript_words(video_path: Path) -> List[Dict[str, Any]]:
    """Runs faster-whisper to extract word-level timestamps."""
    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(video_path), word_timestamps=True)
    words = []
    for segment in segments:
        for w in segment.words:
            words.append({
                "word": w.word.strip(),
                "start": round(w.start, 2),
                "end": round(w.end, 2),
                "probability": round(w.probability, 2)
            })
    return words

def get_video_duration(video_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return float(res.stdout.strip()) if res.returncode == 0 and res.stdout.strip() else 0.0

def detect_audio_spikes(
    video_path: Path, 
    min_spacing: float = 7.0, 
    max_spikes: int = 4
) -> List[Tuple[float, float]]:
    """
    Extracts only true high-impact energy peaks to trigger well-placed punch zooms.
    Enforces minimum 7.0s spacing and caps total zooms to prevent rapid yo-yo zooming.
    """
    import numpy as np
    try:
        ffmpeg_exe = get_ffmpeg_path()
    except Exception:
        ffmpeg_exe = "ffmpeg"

    cmd = [
        ffmpeg_exe, "-v", "error", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0 or not proc.stdout:
        return []
    samples = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32)
    if len(samples) == 0:
        return []
    hop = int(16000 * 0.25)
    num_win = len(samples) // hop
    if num_win == 0:
        return []
    rms_vals = [np.sqrt(np.mean(samples[i*hop:(i+1)*hop]**2) + 1e-9) for i in range(num_win)]
    db_arr = 20 * np.log10(np.array(rms_vals) + 1e-5)
    p90 = np.percentile(db_arr, 90)

    # Collect candidate peaks in top 10% loudness
    candidates = []
    for i, db in enumerate(db_arr):
        if db >= p90:
            t = round(i * 0.25, 2)
            candidates.append((t, db))

    # Pick top highest energy peaks separated by at least min_spacing
    candidates.sort(key=lambda x: x[1], reverse=True)
    selected = []
    for t, db in candidates:
        if all(abs(t - st) >= min_spacing for st, _ in selected):
            selected.append((t, db))
            if len(selected) >= max_spikes:
                break

    # Sort selected spikes chronologically
    selected.sort(key=lambda x: x[0])
    return [(round(t, 2), round(t + 1.25, 2)) for t, _ in selected]

def generate_composition_html(
    video_rel_path: str,
    duration: float,
    words: List[Dict[str, Any]],
    zooms: List[Tuple[float, float]],
    badge_text: str,
    style_key: str = "viral_pop",
    zoom_scale: float = 1.15
) -> str:
    style = STYLES.get(style_key, STYLES["viral_pop"])
    
    # Phrase chunking (2 to 4 words per line)
    chunks = []
    cur = []
    for i, w in enumerate(words):
        prev = words[i-1] if i > 0 else None
        pause = (w["start"] - prev["end"]) if prev else 0
        should_break = False
        if cur:
            if pause > 0.32:
                should_break = True
            elif len(cur) >= 4:
                should_break = True
            elif len(cur) >= 3 and len(w["word"]) > 5:
                should_break = True
        if should_break and cur:
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)

    html_chunks = []
    js_timelines = []

    for c_idx, chunk in enumerate(chunks):
        start_t = chunk[0]["start"]
        next_t = chunks[c_idx + 1][0]["start"] if c_idx + 1 < len(chunks) else chunk[-1]["end"] + 0.20
        end_t = min(next_t, chunk[-1]["end"] + 0.08)
        dur = max(0.20, round(end_t - start_t, 2))

        words_html = []
        for w_idx, w in enumerate(chunk):
            w_id = f"w_{c_idx}_{w_idx}"
            w_text = w["word"].upper()
            clean = re.sub(r"[^\w$]", "", w_text)
            km = get_keyword_meta(clean)
            cls = " kw" if km else ""
            words_html.append(f'<span id="{w_id}" class="sub-word{cls}">{w_text}</span>')

        html_chunks.append(f'''      <div id="chunk_{c_idx}" class="sub-chunk clip" data-start="{start_t:.2f}" data-duration="{dur:.2f}" data-track-index="2">
        <p class="sub-box">
          {" ".join(words_html)}
        </p>
      </div>''')

        for w_idx, w in enumerate(chunk):
            w_id = f"#w_{c_idx}_{w_idx}"
            w_start = round(w["start"], 2)
            w_dur = max(0.10, round(w["end"] - w["start"], 2))
            clean = re.sub(r"[^\w$]", "", w["word"].upper())
            km = get_keyword_meta(clean)
            col = km["color"] if km else style["active_color"]
            glow = km["glow"] if km else style["active_glow"]

            js_timelines.append(f'''      // "{w['word']}"
      tl.fromTo("{w_id}", 
        {{ scale: 1.0, color: "#FFFFFF" }},
        {{ scale: {style['active_scale']}, color: "{col}", textShadow: "0 0 24px {glow}, 0 0 3px #000, 3px 3px 0 #000, -3px -3px 0 #000, 3px -3px 0 #000, -3px 3px 0 #000", duration: 0.08, ease: "power2.out" }}, 
        {w_start}
      );
      tl.to("{w_id}", 
        {{ scale: 1.0, color: "#FFFFFF", textShadow: "0 0 3px #000, 3px 3px 0 #000, -3px -3px 0 #000, 3px -3px 0 #000, -3px 3px 0 #000", duration: 0.10, ease: "power2.in" }}, 
        {round(w_start + w_dur, 2)}
      );''')

    # Zooms in GSAP - smooth punch in, hold, and smooth ease-in-out reset
    zoom_lines = []
    if zoom_scale > 1.01:
        for z_s, z_e in zooms:
            zoom_lines.append(f'''      tl.to("#video-wrapper", {{ scale: {zoom_scale}, duration: 0.28, ease: "power2.out" }}, {z_s});''')
            zoom_lines.append(f'''      tl.to("#video-wrapper", {{ scale: 1.0, duration: 0.40, ease: "power2.inOut" }}, {z_e});''')

    badge_html = ""
    if badge_text.strip():
        badge_html = f'''      <!-- Status Hook Badge -->
      <div id="status-badge" class="clip" data-start="0" data-duration="{duration:.2f}" data-track-index="1">
        <span id="badge-dot"></span>
        <span id="badge-text">{badge_text}</span>
      </div>'''

    html = f'''<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1080, height=1920" />
    <script src="vendor/gsap.min.js"></script>
    <style>
      @font-face {{
        font-family: '{style['font']}';
        src: local('{style['font']}');
      }}
      * {{
        margin: 0;
        padding: 0;
        box-sizing: border-box;
      }}
      html, body {{
        margin: 0;
        width: 1080px;
        height: 1920px;
        overflow: hidden;
        background: #000000;
        font-family: "{style['font']}", sans-serif;
      }}
      #root {{
        position: relative;
        width: 100%;
        height: 100%;
        overflow: hidden;
      }}
      #video-wrapper {{
        position: absolute;
        inset: 0;
        width: 1080px;
        height: 1920px;
        transform-origin: 50% 38%;
      }}
      #base-video {{
        width: 100%;
        height: 100%;
        object-fit: cover;
      }}
      #vignette {{
        position: absolute;
        inset: 0;
        pointer-events: none;
        background: radial-gradient(circle at 50% 45%, rgba(0,0,0,0) 75%, rgba(0,0,0,0.30) 100%);
      }}
      #status-badge {{
        position: absolute;
        top: 85px;
        left: 54px;
        display: flex;
        align-items: center;
        gap: 12px;
        background: rgba(10, 15, 24, 0.85);
        padding: 10px 22px;
        border-radius: 999px;
        border: 1px solid rgba(255, 255, 255, 0.22);
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
      }}
      #badge-dot {{
        display: inline-block;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: {style['badge_color']};
        box-shadow: 0 0 10px {style['badge_color']};
      }}
      #badge-text {{
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        font-size: 22px;
        font-weight: 800;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: #f1f5f9;
      }}
      .sub-chunk {{
        position: absolute;
        bottom: 380px;
        left: 0;
        width: 1080px;
        display: flex;
        justify-content: center;
        align-items: center;
        pointer-events: none;
        z-index: 2;
      }}
      .sub-box {{
        margin: 0;
        max-width: 960px;
        text-align: center;
        display: flex;
        flex-wrap: wrap;
        justify-content: center;
        align-items: center;
        gap: 16px 28px;
        padding: 16px 36px;
        border-radius: 20px;
        background: {style['pill_bg']};
        box-shadow: 0 12px 36px rgba(0, 0, 0, 0.7);
        border: 1px solid {style['pill_border']};
      }}
      .sub-word {{
        display: inline-block;
        margin: 0 8px;
        font-size: 68px;
        font-weight: 900;
        letter-spacing: 0.03em;
        color: #ffffff;
        text-shadow: 
          0 0 3px #000,
          3px 3px 0 #000, 
          -3px -3px 0 #000, 
          3px -3px 0 #000, 
          -3px 3px 0 #000,
          0 6px 14px rgba(0, 0, 0, 0.9);
        transform-origin: center center;
        will-change: transform, color;
      }}
    </style>
  </head>
  <body>
    <div
      id="root"
      data-composition-id="main"
      data-start="0"
      data-duration="{duration:.2f}"
      data-width="1080"
      data-height="1920"
    >
      <div id="video-wrapper">
        <video
          id="base-video"
          src="{video_rel_path}"
          data-start="0"
          data-duration="{duration:.2f}"
          class="clip"
          data-track-index="0"
          muted
        ></video>
      </div>
      <audio
        id="base-audio"
        src="{video_rel_path}"
        data-start="0"
        data-duration="{duration:.2f}"
      ></audio>
      <div id="vignette"></div>
{badge_html}
{"\n".join(html_chunks)}
    </div>
    <script>
      const tl = gsap.timeline({{ paused: true }});
{"\n".join(zoom_lines)}
{"\n".join(js_timelines)}
      window.__timelines["main"] = tl;
      tl.seek(0);
    </script>
  </body>
</html>'''
    return html

def prepare_clean_base_video(input_video_path: Path, target_base: Path) -> str:
    """
    Ensures the base video has NO old burned-in subtitles:
    1. If a [stem]_clean.mp4 twin exists, uses it directly (100% native quality).
    2. Otherwise, automatically runs FFmpeg delogo inpainting over the lower-third
       subtitle band (x:100..980, y:1280..1640) at 6x real-time speed.
    """
    target_base.parent.mkdir(parents=True, exist_ok=True)
    clean_candidate = input_video_path.with_name(f"{input_video_path.stem}_clean.mp4")
    if clean_candidate.exists() and get_video_duration(clean_candidate) > 0:
        print(f"  [Hyperframes] Clean video twin found: {clean_candidate.name}. Using as base.")
        shutil.copy2(clean_candidate, target_base)
        return "clean_twin"

    print(f"  [Hyperframes] Old subtitles detected on {input_video_path.name}. Scrubbing burned text...")
    try:
        ffmpeg_exe = get_ffmpeg_path()
    except Exception:
        ffmpeg_exe = "ffmpeg"

    cmd = [
        ffmpeg_exe, "-y",
        "-i", str(input_video_path),
        "-vf", "delogo=x=40:y=1240:w=1000:h=480:show=0",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(target_base)
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("  [Hyperframes] Old subtitles scrubbed with delogo successfully.")
        return "delogo_scrubbed"
    except Exception as e:
        print(f"  [Hyperframes Warning] Scrubber fallback ({e}), copying original video.")
        shutil.copy2(input_video_path, target_base)
        return "fallback_copy"

def get_hyperframes_cli_cmd() -> List[str]:
    """
    Resolves the appropriate Hyperframes CLI runner for non-interactive execution.
    Prioritizes local node_modules binary, then bunx, then npx with automatic prompt acceptance (--yes).
    """
    local_bin_exe = WORKSPACE_DIR / "node_modules" / ".bin" / "hyperframes.exe"
    local_bin_cmd = WORKSPACE_DIR / "node_modules" / ".bin" / "hyperframes.cmd"
    local_bin_unix = WORKSPACE_DIR / "node_modules" / ".bin" / "hyperframes"
    if local_bin_exe.exists():
        return [str(local_bin_exe)]
    elif sys.platform == "win32" and local_bin_cmd.exists():
        return [str(local_bin_cmd)]
    elif local_bin_unix.exists():
        return [str(local_bin_unix)]

    # Check for bun / bunx
    if shutil.which("bun") or shutil.which("bunx"):
        return ["bunx", "hyperframes"]

    # Check for npx
    if shutil.which("npx"):
        # Always use --yes so npx never prompts "Ok to proceed? (y)" on missing package
        return ["npx", "--yes", "hyperframes"]

    raise RuntimeError(
        "Neither Node.js (npx) nor Bun was found on this system. "
        "Please install Node.js (https://nodejs.org) or Bun (https://bun.sh) to enable Hyperframes rendering."
    )

def run_hyperframes_command(args: List[str], cwd: Path = WORKSPACE_DIR, timeout: int = 600) -> subprocess.CompletedProcess:
    """
    Executes a Hyperframes CLI command safely in non-interactive mode.
    Guarantees stdin=DEVNULL to prevent hanging prompts, captures output, and raises clean errors.
    """
    base_cmd = get_hyperframes_cli_cmd()
    full_cmd = base_cmd + args
    print(f"  [Hyperframes CLI] Executing: {' '.join(full_cmd)} in {cwd}", flush=True)

    use_shell = sys.platform == "win32"
    cmd_to_run = " ".join(f'"{a}"' if " " in a else a for a in full_cmd) if use_shell else full_cmd

    try:
        proc = subprocess.run(
            cmd_to_run,
            cwd=str(cwd),
            shell=use_shell,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Hyperframes operation timed out after {timeout} seconds.")
    except Exception as e:
        raise RuntimeError(f"Failed to execute Hyperframes CLI: {e}")

    if proc.returncode != 0:
        err_msg = proc.stderr.strip() or proc.stdout.strip() or f"Process exited with code {proc.returncode}"
        raise RuntimeError(f"Hyperframes CLI failed (code {proc.returncode}): {err_msg}")

    return proc

def setup_composition(
    input_video_path: Path,
    style_key: str = "viral_pop",
    badge_text: str = "",
    zoom_intensity: str = "dynamic"
) -> Dict[str, Any]:
    """Prepares clean base video, transcribes words, and writes index.html composition."""
    if not input_video_path.exists():
        raise FileNotFoundError(f"Input video not found: {input_video_path}")

    ensure_workspace()

    # Prepare base video without old burned subtitles
    target_base = WORKSPACE_DIR / "base_vertical.mp4"
    prepare_clean_base_video(input_video_path, target_base)

    duration = get_video_duration(target_base)
    
    # Check for existing words or transcribe
    words = extract_transcript_words(target_base)
    
    # Zoom scale mapping (natural, non-jarring punch zooms)
    zoom_scales = {"none": 1.0, "subtle": 1.06, "dynamic": 1.10, "aggressive": 1.15}
    scale = zoom_scales.get(zoom_intensity, 1.10)
    zooms = detect_audio_spikes(target_base) if scale > 1.01 else []

    # Auto badge if blank
    if not badge_text:
        badge_icon = STYLES.get(style_key, {}).get("badge_icon", "⚡")
        badge_text = f"{badge_icon} VIRAL SHORT"

    html = generate_composition_html(
        video_rel_path="base_vertical.mp4",
        duration=duration,
        words=words,
        zooms=zooms,
        badge_text=badge_text,
        style_key=style_key,
        zoom_scale=scale
    )

    index_path = WORKSPACE_DIR / "index.html"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)

    return {
        "status": "ready",
        "duration": duration,
        "word_count": len(words),
        "zoom_count": len(zooms)
    }

def setup_and_render_preview(
    input_video_path: Path,
    style_key: str = "viral_pop",
    badge_text: str = "",
    zoom_intensity: str = "dynamic"
) -> Dict[str, Any]:
    """Generates contact sheet preview for user approval."""
    comp_info = setup_composition(
        input_video_path=input_video_path,
        style_key=style_key,
        badge_text=badge_text,
        zoom_intensity=zoom_intensity
    )
    duration = comp_info["duration"]

    # Calculate sample times for snapshot
    sample_times = [
        round(duration * 0.05, 1),
        round(duration * 0.20, 1),
        round(duration * 0.40, 1),
        round(duration * 0.60, 1),
        round(duration * 0.80, 1),
        round(duration * 0.95, 1)
    ]
    sample_arg = ",".join(str(t) for t in sample_times)

    run_hyperframes_command(["snapshot", ".", "--at", sample_arg])

    sheet_path = WORKSPACE_DIR / "snapshots" / "contact-sheet.jpg"
    comp_info["contact_sheet"] = str(sheet_path)
    return comp_info

def render_final_deliverables(
    output_prefix: str,
    output_dir: Path = CLI_OUTPUT_DIR
) -> Dict[str, Any]:
    """Renders 1080x1920 master and encodes < 3 MB preview MP4."""
    ensure_workspace()

    master_file = output_dir / f"{output_prefix}_hyperframes_master.mp4"
    preview_file = output_dir / f"{output_prefix}_hyperframes_preview.mp4"

    # Step 1: Render master via Hyperframes
    run_hyperframes_command(["render", ".", "-o", str(master_file)])

    # Step 2: Encode mobile preview (< 3 MB)
    dur = get_video_duration(master_file)
    maxrate = "350k" if dur > 40 else "420k"
    bufsize = "700k" if dur > 40 else "840k"
    
    try:
        ffmpeg_exe = get_ffmpeg_path()
    except Exception:
        ffmpeg_exe = "ffmpeg"

    enc_cmd = [
        ffmpeg_exe, "-y", "-i", str(master_file),
        "-c:v", "libx264", "-crf", "32", "-preset", "slow",
        "-maxrate", maxrate, "-bufsize", bufsize,
        "-c:a", "aac", "-b:a", "48k",
        str(preview_file)
    ]
    subprocess.run(enc_cmd, check=True)

    return {
        "status": "completed",
        "master_path": str(master_file),
        "master_size": master_file.stat().st_size,
        "preview_path": str(preview_file),
        "preview_size": preview_file.stat().st_size
    }
