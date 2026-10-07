import os
import re
import sys
import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

from config import BASE_DIR, OUTPUT_DIR as CLI_OUTPUT_DIR
WORKSPACE_DIR = BASE_DIR.parent / "my-video"

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

def detect_audio_spikes(video_path: Path, spike_db: float = 7.0) -> List[Tuple[float, float]]:
    """Extracts loud moments to trigger punch zooms."""
    import numpy as np
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(video_path),
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
    thresh = np.mean(db_arr) + spike_db
    spikes = []
    last_end = -1.0
    for i, db in enumerate(db_arr):
        t_start = round(i * 0.25, 2)
        t_end = round(t_start + 1.4, 2)
        if db > thresh and t_start >= (last_end + 1.5):
            spikes.append((t_start, t_end))
            last_end = t_end
    return spikes

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

    # Zooms in GSAP
    zoom_lines = []
    if zoom_scale > 1.01:
        for z_s, z_e in zooms:
            zoom_lines.append(f'''      tl.to("#video-wrapper", {{ scale: {zoom_scale}, duration: 0.22, ease: "power3.out" }}, {z_s});''')
            zoom_lines.append(f'''      tl.to("#video-wrapper", {{ scale: 1.0, duration: 0.35, ease: "power2.inOut" }}, {z_e});''')

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
        background: radial-gradient(circle at 50% 45%, rgba(0,0,0,0) 65%, rgba(0,0,0,0.55) 100%);
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
      #subtitle-shield {{
        position: absolute;
        bottom: 180px;
        left: 0;
        width: 1080px;
        height: 520px;
        background: radial-gradient(ellipse 95% 70% at 50% 50%, rgba(0, 0, 0, 0.94) 0%, rgba(0, 0, 0, 0.82) 65%, rgba(0, 0, 0, 0) 100%);
        backdrop-filter: blur(16px);
        pointer-events: none;
        z-index: 1;
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
      <div id="subtitle-shield"></div>
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
    clean_candidate = input_video_path.with_name(f"{input_video_path.stem}_clean.mp4")
    if clean_candidate.exists() and get_video_duration(clean_candidate) > 0:
        print(f"  [Hyperframes] Clean video twin found: {clean_candidate.name}. Using as base.")
        shutil.copy2(clean_candidate, target_base)
        return "clean_twin"

    print(f"  [Hyperframes] Old subtitles detected on {input_video_path.name}. Scrubbing burned text...")
    cmd = [
        "ffmpeg", "-y",
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

def setup_and_render_preview(
    input_video_path: Path,
    style_key: str = "viral_pop",
    badge_text: str = "",
    zoom_intensity: str = "dynamic"
) -> Dict[str, Any]:
    """Generates contact sheet preview for user approval."""
    if not input_video_path.exists():
        raise FileNotFoundError(f"Input video not found: {input_video_path}")

    # Prepare base video without old burned subtitles
    target_base = WORKSPACE_DIR / "base_vertical.mp4"
    prepare_clean_base_video(input_video_path, target_base)

    duration = get_video_duration(target_base)
    
    # Check for existing words or transcribe
    words = extract_transcript_words(target_base)
    
    # Zoom scale mapping
    zoom_scales = {"none": 1.0, "subtle": 1.08, "dynamic": 1.15, "aggressive": 1.22}
    scale = zoom_scales.get(zoom_intensity, 1.15)
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

    snap_cmd = f"npx hyperframes snapshot . --at {sample_arg}"
    subprocess.run(snap_cmd, shell=True, cwd=str(WORKSPACE_DIR), check=True)

    sheet_path = WORKSPACE_DIR / "snapshots" / "contact-sheet.jpg"
    return {
        "status": "ready",
        "contact_sheet": str(sheet_path),
        "duration": duration,
        "word_count": len(words),
        "zoom_count": len(zooms)
    }

def render_final_deliverables(
    output_prefix: str,
    output_dir: Path = CLI_OUTPUT_DIR
) -> Dict[str, Any]:
    """Renders 1080x1920 master and encodes < 3 MB preview MP4."""
    master_file = output_dir / f"{output_prefix}_hyperframes_master.mp4"
    preview_file = output_dir / f"{output_prefix}_hyperframes_preview.mp4"

    # Step 1: Render master via Hyperframes
    render_cmd = f"npx hyperframes render . -o \"{master_file}\""
    subprocess.run(render_cmd, shell=True, cwd=str(WORKSPACE_DIR), check=True)

    # Step 2: Encode mobile preview (< 3 MB)
    dur = get_video_duration(master_file)
    maxrate = "350k" if dur > 40 else "420k"
    bufsize = "700k" if dur > 40 else "840k"
    
    enc_cmd = [
        "ffmpeg", "-y", "-i", str(master_file),
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
