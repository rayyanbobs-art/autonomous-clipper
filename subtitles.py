import os
import re
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from faster_whisper import WhisperModel

PROFANITY_ROOTS = {
    "fuck", "fucks", "fucking", "fucked", "fucker", "fuckers", "motherfucker", "motherfuckers",
    "shit", "shits", "shitty", "bullshit", "horseshit", "dipshit",
    "bitch", "bitches", "bitching",
    "cunt", "cunts",
    "dick", "dicks", "dickhead", "dickheads",
    "pussy", "pussies",
    "asshole", "assholes",
    "bastard", "bastards",
    "nigga", "niggers", "nigger",
    "fag", "faggot", "faggots",
    "retard", "retarded"
}

def is_profane_word(word: str) -> bool:
    """Checks if a word matches demonetization profanity roots."""
    clean = re.sub(r"[^\w]", "", word.lower())
    if not clean:
        return False
    if clean in PROFANITY_ROOTS:
        return True
    for root in ["fuck", "shit", "bitch", "cunt", "dickhead", "motherfuck", "asshole", "nigger", "faggot"]:
        if root in clean:
            return True
    return False

def mask_profane_word(word: str) -> str:
    """Masks profane words with asterisks while preserving case and punctuation (e.g. F*** or s***)."""
    if not is_profane_word(word):
        return word
    m = re.match(r"^(\W*)([\w]+)(\W*)$", word)
    if not m:
        return "****"
    pre, body, post = m.groups()
    if len(body) <= 2:
        masked = "*" * len(body)
    else:
        masked = body[0] + ("*" * (len(body) - 1))
    return f"{pre}{masked}{post}"

def detect_profanity_intervals(words: List[Dict], padding_sec: float = 0.06) -> List[Tuple[float, float]]:
    """
    Extracts start/end timestamps of profane words with slight padding for seamless audio bleeping.
    Merges adjacent overlapping intervals.
    """
    raw_intervals = []
    for w in words:
        if is_profane_word(w.get("word", "")):
            start_t = max(0.0, float(w.get("start", 0.0)) - padding_sec)
            end_t = float(w.get("end", 0.0)) + padding_sec
            raw_intervals.append((start_t, end_t))

    if not raw_intervals:
        return []

    raw_intervals.sort(key=lambda x: x[0])
    merged = [raw_intervals[0]]
    for s, e in raw_intervals[1:]:
        last_s, last_e = merged[-1]
        if s <= last_e + 0.08:
            merged[-1] = (last_s, max(last_e, e))
        else:
            merged.append((s, e))

    return merged

# Global cached model instance to avoid reloading weights
_WHISPER_MODEL = None

EMOJI_MAP = {
    # Nature, Animals & Outdoors
    "bear": "🐻", "bears": "🐻", "dog": "🐶", "dogs": "🐶", "cat": "🐱", "cats": "🐱",
    "wolf": "🐺", "wolves": "🐺", "lion": "🦁", "tiger": "🐯", "shark": "🦈", "sharks": "🦈",
    "bird": "🦅", "eagle": "🦅", "monkey": "🐵", "snake": "🐍", "fish": "🐟",
    "mountain": "🏔️", "mountains": "🏔️", "snow": "❄️", "cave": "🪨", "tent": "⛺", "camp": "⛺",
    "camping": "⛺", "forest": "🌲", "tree": "🌲", "trees": "🌲", "wood": "🪵", "woods": "🌲",
    "ocean": "🌊", "sea": "🌊", "river": "🌊", "water": "💧", "rain": "🌧️", "storm": "⛈️",
    "cold": "🥶", "freezing": "🥶", "ice": "🧊", "sun": "☀️", "sunrise": "🌅", "sunset": "🌇",
    "night": "🌙", "moon": "🌙", "dark": "🌑", "fire": "🔥", "hot": "🔥", "warm": "🔥",
    "alaska": "🏔️", "toboggan": "🛷", "sled": "🛷",
    
    # Wealth, Business & Success
    "money": "💰", "cash": "💵", "dollar": "💵", "dollars": "💵", "rich": "🤑", "wealth": "💎",
    "diamond": "💎", "gold": "🏆", "win": "🏆", "winner": "🏆", "won": "🏆", "success": "🚀",
    "rocket": "🚀", "grow": "📈", "growth": "📈", "business": "💼", "boss": "👑", "king": "👑",
    "queen": "👑", "crypto": "🪙", "bitcoin": "🪙", "invest": "📊", "market": "📊", "free": "🎁",
    "secret": "🤫", "pay": "💳", "cost": "💳",
    
    # Fitness, Health & Action
    "gym": "🏋️", "workout": "🏋️", "muscle": "💪", "strong": "💪", "run": "🏃", "running": "🏃",
    "fight": "🥊", "boxing": "🥊", "punch": "🥊", "fast": "⚡", "speed": "⚡", "lightning": "⚡",
    "food": "🍔", "eat": "🍽️", "eating": "🍽️", "meat": "🥩", "coffee": "☕", "beer": "🍺",
    "drink": "🥤", "heart": "❤️", "brain": "🧠", "mind": "🧠", "sleep": "😴", "tired": "😴",
    
    # Emotions, Reactions & Impact
    "crazy": "🤯", "insane": "🤯", "dead": "💀", "kill": "💀", "died": "💀", "death": "💀",
    "shock": "😱", "shocked": "😱", "scary": "😱", "laugh": "🤣", "funny": "😂", "lol": "😂",
    "wow": "🤩", "love": "❤️", "hate": "😡", "angry": "😡", "sad": "😢", "cry": "😭",
    "boom": "💥", "danger": "⚠️", "warning": "⚠️", "stop": "🛑", "look": "👀", "see": "👀",
    "listen": "👂", "talk": "🗣️", "time": "⏳", "clock": "⏰", "100": "💯", "truth": "💯",
    "brutal": "🔥", "killer": "⚡", "great": "🔥", "bad": "❌", "worst": "❌", "best": "⭐"
}

SUBTITLE_STYLES = {
    "none": {
        "name": "No Subtitles",
        "label": "🚫 No Subtitles (Raw 9:16)",
        "preview_bg": "from-slate-700/20 to-zinc-900/40",
        "badge_color": "text-slate-400 border-slate-600/30",
        "font": "None",
        "size": 0,
        "primary_color": "",
        "outline_color": "",
        "outline": 0,
        "shadow": 0,
        "border_style": 0,
        "back_color": "",
        "margin_v": 0,
        "highlight_color": None,
        "pop_scale": False,
        "uppercase": False,
    },
    "bold_pop": {
        "name": "Bold Pop",
        "label": "🔥 Bold Pop (Opus Classic)",
        "preview_bg": "from-amber-500/20 to-yellow-500/10",
        "badge_color": "text-yellow-400 border-yellow-500/30",
        "font": "Arial",
        "size": 60,
        "primary_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "outline": 6,
        "shadow": 2,
        "border_style": 1,
        "back_color": "&H80000000",
        "margin_v": 420,
        "highlight_color": "&H0000FFFF",  # Electric Yellow
        "pop_scale": True,
        "uppercase": True,
    },
    "karaoke": {
        "name": "Karaoke Highlight",
        "label": "🎤 Karaoke (Live Fill)",
        "preview_bg": "from-indigo-500/20 to-cyan-500/10",
        "badge_color": "text-cyan-400 border-cyan-500/30",
        "font": "Arial",
        "size": 58,
        "primary_color": "&H0000E5FF",  # Bright Yellow-Cyan highlight
        "secondary_color": "&H00FFFFFF", # Base white text
        "outline_color": "&H00000000",
        "outline": 6,
        "shadow": 2,
        "border_style": 1,
        "back_color": "&H80000000",
        "margin_v": 420,
        "karaoke_mode": True,
        "uppercase": True,
    },
    "boxed_clean": {
        "name": "Boxed Clean",
        "label": "⬛ Boxed Clean (Dark Badge)",
        "preview_bg": "from-slate-600/30 to-slate-800/20",
        "badge_color": "text-slate-200 border-slate-600/40",
        "font": "Arial",
        "size": 50,
        "primary_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "outline": 2,
        "shadow": 0,
        "border_style": 3,  # Opaque box background in ASS
        "back_color": "&HA0101015",  # Semi-transparent dark background
        "margin_v": 410,
        "highlight_color": "&H0000FFFF",
        "pop_scale": True,
        "uppercase": True,
    },
    "minimal_caption": {
        "name": "Minimal Caption",
        "label": "📝 Minimal Caption (Subtle)",
        "preview_bg": "from-slate-500/20 to-slate-500/10",
        "badge_color": "text-slate-300 border-slate-500/30",
        "font": "Arial",
        "size": 46,
        "primary_color": "&H00F0F0F0",
        "outline_color": "&H00000000",
        "outline": 3,
        "shadow": 1,
        "border_style": 1,
        "back_color": "&H80000000",
        "margin_v": 360,
        "highlight_color": None,
        # Natural case is intentional: this is the "subtle documentary caption" style and
        # the UI preview renders it in sentence case.
        "uppercase": False,
    },
    "hormozi": {
        "name": "Hormozi Classic",
        "label": "⚡ Hormozi (Yellow & White)",
        "preview_bg": "from-amber-500/20 to-yellow-500/10",
        "badge_color": "text-yellow-400 border-yellow-500/30",
        "font": "Arial",
        "size": 58,
        "primary_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "outline": 6,
        "shadow": 2,
        "border_style": 1,
        "back_color": "&H80000000",
        "margin_v": 420,
        "highlight_color": "&H0000FFFF",
        "pop_scale": True,
        "uppercase": True,
    },
    "beast": {
        "name": "Beast Viral",
        "label": "🌟 Beast (Electric Yellow)",
        "preview_bg": "from-yellow-500/20 to-amber-500/10",
        "badge_color": "text-amber-400 border-amber-500/30",
        "font": "Arial",
        "size": 60,
        "primary_color": "&H0000FFFF",
        "outline_color": "&H00000000",
        "outline": 7,
        "shadow": 3,
        "border_style": 1,
        "back_color": "&H80000000",
        "margin_v": 420,
        "highlight_color": "&H00FFFFFF",
        "pop_scale": True,
        "uppercase": True,
    },
    "neon_green": {
        "name": "Cyber Neon",
        "label": "🟢 Cyber (Neon Green)",
        "preview_bg": "from-emerald-500/20 to-green-500/10",
        "badge_color": "text-emerald-400 border-emerald-500/30",
        "font": "Arial",
        "size": 58,
        "primary_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "outline": 6,
        "shadow": 2,
        "border_style": 1,
        "back_color": "&H80000000",
        "margin_v": 420,
        "highlight_color": "&H0033FF33",
        "pop_scale": True,
        "uppercase": True,
    },
    "red_punch": {
        "name": "Red Punch",
        "label": "🔴 Punch (Hot Red)",
        "preview_bg": "from-rose-500/20 to-red-500/10",
        "badge_color": "text-rose-400 border-rose-500/30",
        "font": "Arial",
        "size": 58,
        "primary_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "outline": 6,
        "shadow": 2,
        "border_style": 1,
        "back_color": "&H80000000",
        "margin_v": 420,
        "highlight_color": "&H001010FF",
        "pop_scale": True,
        "uppercase": True,
    },
    "clean_white": {
        "name": "Minimal White",
        "label": "⚪ Clean (Minimal White)",
        "preview_bg": "from-slate-500/20 to-slate-500/10",
        "badge_color": "text-slate-300 border-slate-500/30",
        "font": "Arial",
        "size": 56,
        "primary_color": "&H00D0D0D0",
        "outline_color": "&H00000000",
        "outline": 4,
        "shadow": 2,
        "border_style": 1,
        "back_color": "&H80000000",
        "margin_v": 420,
        "highlight_color": "&H00FFFFFF",
        "pop_scale": True,
        "uppercase": True,
    },
    "netflix_standard": {
        "name": "Netflix Standard",
        "label": "🎬 Netflix (20 CPS Clean)",
        "preview_bg": "from-red-600/20 to-zinc-900/40",
        "badge_color": "text-red-400 border-red-500/30",
        "font": "Arial",
        "size": 48,
        "primary_color": "&H00F5F5F5",
        "secondary_color": "&H00F5F5F5",
        "outline_color": "&H00000000",
        "outline": 2.5,
        "shadow": 1.5,
        "border_style": 1,
        "back_color": "&H80000000",
        "margin_v": 220,
        "max_words_per_line": 7,
        "max_duration_sec": 3.0,
        "max_pause_sec": 0.45,
        "highlight_color": None,
        "pop_scale": False,
        "uppercase": False,
    },
    "bbc_sdh": {
        "name": "BBC SDH Boxed",
        "label": "📺 BBC SDH (Broadcast Box)",
        "preview_bg": "from-yellow-600/20 to-zinc-900/40",
        "badge_color": "text-yellow-400 border-yellow-500/30",
        "font": "Arial",
        "size": 48,
        "primary_color": "&H0000FFFF",  # BBC Yellow high-contrast speaker text
        "secondary_color": "&H0000FFFF",
        "outline_color": "&H00000000",
        "outline": 0,
        "shadow": 0,
        "border_style": 3,  # Opaque rectangular background plate
        "back_color": "&H00000000",  # Pure opaque black bounding box
        "margin_v": 240,
        "max_words_per_line": 6,
        "max_duration_sec": 2.8,
        "max_pause_sec": 0.40,
        "highlight_color": None,
        "pop_scale": False,
        "uppercase": False,
    },
    "cinematic_auteur": {
        "name": "Cinematic Auteur",
        "label": "🎞️ Auteur Film (Serif Letterbox)",
        "preview_bg": "from-amber-700/20 to-zinc-900/40",
        "badge_color": "text-amber-300 border-amber-500/30",
        "font": "Georgia",
        "size": 50,
        "primary_color": "&H00E8F0F8",  # Warm cream / vintage off-white
        "secondary_color": "&H00E8F0F8",
        "outline_color": "&H50000000",
        "outline": 2,
        "shadow": 3,
        "border_style": 1,
        "back_color": "&H90000000",
        "margin_v": 220,
        "max_words_per_line": 6,
        "max_duration_sec": 3.0,
        "max_pause_sec": 0.40,
        "highlight_color": None,
        "pop_scale": False,
        "fade": True,
        "uppercase": False,
    },
    "kinetic_pop": {
        "name": "Kinetic Pop",
        "label": "⚡ Kinetic Pop (TikTok / Reels)",
        "preview_bg": "from-yellow-500/25 to-amber-500/10",
        "badge_color": "text-yellow-300 border-yellow-400/40",
        "font": "Impact",
        "size": 62,
        "primary_color": "&H00FFFFFF",
        "secondary_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "outline": 8,
        "shadow": 3,
        "border_style": 1,
        "back_color": "&H90000000",
        "margin_v": 420,
        "max_words_per_line": 2,
        "max_duration_sec": 1.2,
        "max_pause_sec": 0.28,
        "highlight_color": "&H0000FFFF",  # Electric Yellow active word pop
        "pop_scale": True,
        "uppercase": True,
    },
    "wild_den": {
        "name": "Wild Den Bold",
        "label": "🏔️ Wild Den (Outdoor Viral)",
        "preview_bg": "from-amber-600/25 to-stone-900/40",
        "badge_color": "text-yellow-400 border-yellow-500/30",
        "font": "Impact",
        "size": 64,
        "primary_color": "&H00FFFFFF",
        "secondary_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "outline": 6,
        "shadow": 3,
        "border_style": 1,
        "back_color": "&H90000000",
        "margin_v": 930,
        "alignment": 2,
        "italic": True,
        "bold": True,
        "max_words_per_line": 2,
        "max_duration_sec": 1.1,
        "max_pause_sec": 0.25,
        "highlight_color": "&H0000E6FF",  # #FFE600 yellow on the spoken word (ASS is &HAABBGGRR)
        "pop_scale": False,
        "uppercase": True,
    },
}

def get_whisper_model(model_name: str = "base.en") -> WhisperModel:
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        device = "cpu"
        compute_type = "int8"
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                device = "cuda"
                compute_type = "float16"
        except Exception:
            pass
        try:
            _WHISPER_MODEL = WhisperModel(model_name, device=device, compute_type=compute_type)
            if device == "cuda":
                print(f"  [Whisper Acceleration] Loaded {model_name} on NVIDIA CUDA GPU.")
        except Exception:
            _WHISPER_MODEL = WhisperModel(model_name, device="cpu", compute_type="int8")
    return _WHISPER_MODEL

def to_ass_timestamp(seconds: float) -> str:
    """
    Converts seconds (e.g. 72.35) to ASS timestamp format (0:01:12.35).

    Negative input is clamped to 0.0: Python's % returns a non-negative result for a
    positive modulus, so -2.0 previously produced the invalid "-1:59:58.00" and negative
    centiseconds broke the 2-digit format entirely.
    """
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return "0:00:00.00"
    if seconds != seconds:  # NaN
        return "0:00:00.00"
    seconds = max(0.0, seconds)

    total_cs = int(round(seconds * 100))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def generate_synced_subtitles(
    video_file: Path,
    ass_file: Path,
    style_key: str = "bold_pop",
    max_words_per_line: int = 3,
    max_pause_sec: float = 0.32,
    max_duration_sec: float = 1.5,
    enable_emojis: bool = False,
    enable_auto_bleep: bool = False,
    words_out: Optional[List[Dict]] = None
) -> bool:
    """
    Transcribes audio track using faster-whisper with word-level timestamps and VAD filtering,
    and generates ASS subtitles with dynamic per-word active pop highlighting, auto-emojis, and pause-cleared silence.
    Supports auto-bleep censoring for demonetization prevention.
    """
    if style_key in ("none", "no_subtitles", "off", None):
        return False

    try:
        model = get_whisper_model("base.en")
        segments, _ = model.transcribe(
            str(video_file),
            word_timestamps=True,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=250)
        )

        words = []
        for segment in segments:
            if not segment.words:
                continue
            for w in segment.words:
                cleaned = w.word.strip()
                if cleaned and w.start is not None and w.end is not None:
                    words.append({
                        "start": max(0.0, float(w.start)),
                        "end": max(0.0, float(w.end)),
                        "word": cleaned
                    })

        if not words:
            return False

        if words_out is not None:
            words_out.extend(words)

        ass_content = build_ass_from_words(
            words,
            style_key=style_key,
            enable_emojis=enable_emojis,
            enable_auto_bleep=enable_auto_bleep,
            max_words_per_line=max_words_per_line,
            max_pause_sec=max_pause_sec,
            max_duration_sec=max_duration_sec
        )
        ass_file.write_text(ass_content, encoding="utf-8")
        return True

    except Exception as e:
        print(f"  [Subtitle Generation Error] {e}")
        return False


def build_ass_from_words(
    words: List[Dict],
    style_key: str = "bold_pop",
    enable_emojis: bool = False,
    enable_auto_bleep: bool = False,
    max_words_per_line: int = 3,
    max_pause_sec: float = 0.32,
    max_duration_sec: float = 1.5
) -> str:
    """
    Constructs a complete ASS subtitle script string from word timestamps,
    applying stylistic presets (Netflix 20-CPS, BBC SDH, Auteur Film, Kinetic Pop, etc.).
    """
    if not words:
        return ""

    # Work on copies: the pop-highlight branch below nudges a word's start forward when
    # whisper emits overlapping/zero-length timings, and `c` holds references to the caller's
    # dicts. Mutating them in place corrupted the shared word list that
    # generate_synced_subtitles hands back through words_out, which video_cutter then reuses
    # for profanity-bleep windows and B-roll cue scheduling.
    words = [dict(w) for w in words]

    style = SUBTITLE_STYLES.get(style_key, SUBTITLE_STYLES["bold_pop"])
    effective_max_words = style.get("max_words_per_line", max_words_per_line)
    effective_max_dur = style.get("max_duration_sec", max_duration_sec)
    effective_max_pause = style.get("max_pause_sec", max_pause_sec)

    # Group words into phrases with pause and style-aware boundaries
    chunks: List[List[Dict]] = []
    current: List[Dict] = []

    for w in words:
        if current:
            pause_before = w["start"] - current[-1]["end"]
            dur_so_far = w["end"] - current[0]["start"]
            if pause_before > effective_max_pause or len(current) >= effective_max_words or dur_so_far > effective_max_dur:
                chunks.append(current)
                current = []
        current.append(w)

    if current:
        chunks.append(current)

    sec_color = style.get("secondary_color", style["primary_color"])
    border_style = style.get("border_style", 1)
    back_color = style.get("back_color", "&H80000000")
    margin_v = style.get("margin_v", 420)

    # Generate ASS Header with selected style (PlayResX: 1080, PlayResY: 1920)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style['font']},{style['size']},{style['primary_color']},{sec_color},{style['outline_color']},{back_color},{1 if style.get('bold', True) else 0},{1 if style.get('italic') else 0},0,0,100,100,0,0,{border_style},{style['outline']},{style['shadow']},{style.get('alignment', 2)},60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dialogues = []
    # `uppercase` is a per-style opt-in control. Defaulting it to True made every style that
    # omitted the key shout in caps (10 of 13), which contradicted styles like
    # minimal_caption whose UI preview shows natural case.
    is_upper = style.get("uppercase", False)
    fade_tag = "{\\fad(80,80)}" if style.get("fade") else ""

    for chunk_idx, c in enumerate(chunks):
        next_chunk = chunks[chunk_idx + 1] if chunk_idx + 1 < len(chunks) else None
        next_chunk_start = next_chunk[0]["start"] if next_chunk else 999999.0

        raw_words = [
            (item["word"].upper() if is_upper else item["word"]).replace("\\", "").replace("{", "").replace("}", "")
            for item in c
        ]

        chunk_display_words = []
        for item, raw_w in zip(c, raw_words):
            display_text = mask_profane_word(raw_w) if enable_auto_bleep else raw_w
            if enable_emojis:
                clean_w = re.sub(r"[^a-zA-Z0-9]", "", item["word"]).lower()
                emoji_char = EMOJI_MAP.get(clean_w, "")
                if emoji_char:
                    chunk_display_words.append(f"{display_text} {emoji_char}")
                else:
                    chunk_display_words.append(display_text)
            else:
                chunk_display_words.append(display_text)

        if style.get("karaoke_mode"):
            # Real-time karaoke progressive fill across phrase
            start_str = to_ass_timestamp(c[0]["start"])
            chunk_end = c[-1]["end"] + 0.35
            if next_chunk:
                chunk_end = min(chunk_end, next_chunk_start - 0.02)
            # Unconditional clamp: this MUST also run for the final chunk, otherwise a chunk
            # spanning <= 0.35s emits a Dialogue event with End <= Start and libass silently
            # drops the caption.
            if chunk_end <= c[0]["start"]:
                chunk_end = max(c[-1]["end"], c[0]["start"] + 0.08)
            end_str = to_ass_timestamp(chunk_end)

            k_parts = []
            for idx, (item, w_text) in enumerate(zip(c, chunk_display_words)):
                cs_dur = max(8, int(round((item["end"] - item["start"]) * 100)))
                if idx + 1 < len(c):
                    gap = c[idx + 1]["start"] - item["end"]
                    if gap > 0.05:
                        gap_cs = int(round(gap * 100))
                        k_parts.append(f"{{\\k{cs_dur}}}{w_text}{{\\k{gap_cs}}} ")
                    else:
                        k_parts.append(f"{{\\k{cs_dur}}}{w_text} ")
                else:
                    k_parts.append(f"{{\\k{cs_dur}}}{w_text}")

            text_formatted = "".join(k_parts)
            dialogues.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{fade_tag}{text_formatted}")
        elif not style.get("pop_scale") and not style.get("highlight_color"):
            # Clean static display for subtle/minimal/streaming styles (no word-by-word animation)
            start_str = to_ass_timestamp(c[0]["start"])
            chunk_end = c[-1]["end"] + 0.35
            if next_chunk:
                chunk_end = min(chunk_end, next_chunk_start - 0.02)
            # Same unconditional clamp as the karaoke branch above; previously this guard was
            # nested inside `if next_chunk:` so the final chunk of every static style could
            # produce a negative-duration Dialogue event.
            if chunk_end <= c[0]["start"]:
                chunk_end = max(c[-1]["end"], c[0]["start"] + 0.08)
            end_str = to_ass_timestamp(chunk_end)
            line_text = " ".join(chunk_display_words)
            dialogues.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{fade_tag}{line_text}")
        else:
            # Progressive word-by-word active pop
            for active_idx, active_item in enumerate(c):
                w_start = active_item["start"]
                if active_idx + 1 < len(c):
                    next_start = c[active_idx + 1]["start"]
                    if next_start > w_start:
                        w_end = next_start
                    else:
                        w_end = w_start + 0.04
                        c[active_idx + 1]["start"] = w_end
                else:
                    w_end = active_item["end"] + 0.35
                    if next_chunk and next_chunk_start > w_start:
                        w_end = min(w_end, next_chunk_start)
                    if w_end <= w_start:
                        w_end = max(active_item["end"], w_start + 0.08)

                start_str = to_ass_timestamp(w_start)
                end_str = to_ass_timestamp(w_end)

                parts = []
                for i, display_w in enumerate(chunk_display_words):
                    if i == active_idx:
                        if style.get("pop_scale") and style.get("highlight_color"):
                            parts.append(f"{{\\c{style['highlight_color']}&\\fscx114\\fscy114}}{display_w}{{\\c{style['primary_color']}&\\fscx100\\fscy100}}")
                        elif style.get("pop_scale"):
                            parts.append(f"{{\\fscx114\\fscy114}}{display_w}{{\\fscx100\\fscy100}}")
                        elif style.get("highlight_color"):
                            parts.append(f"{{\\c{style['highlight_color']}&}}{display_w}{{\\c{style['primary_color']}&}}")
                        else:
                            parts.append(display_w)
                    else:
                        parts.append(display_w)

                line_text = " ".join(parts)
                dialogues.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{fade_tag}{line_text}")

    return header + "\n".join(dialogues) + "\n"
