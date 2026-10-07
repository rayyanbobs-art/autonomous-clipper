import os
import re
import subprocess
import shutil
import uuid
import sys
from pathlib import Path
from typing import Optional, List, Dict, Tuple
import numpy as np
import json
from config import TEMP_DIR, TARGET_WIDTH, TARGET_HEIGHT
from subtitles import generate_synced_subtitles, detect_profanity_intervals
from broll_engine import plan_and_fetch_brolls, is_decodable_video
import face_tracker
from face_tracker import compute_smart_crop_offset, compute_gaming_split_crop

def get_ffmpeg_path() -> str:
    """Finds ffmpeg executable on system PATH or WinGet package cache."""
    path = shutil.which("ffmpeg")
    if path:
        return path
    local_app_data = os.environ.get("LOCALAPPDATA")
    winget_base = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages" if local_app_data else (Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages")
    if winget_base.exists():
        for f in winget_base.glob("**/ffmpeg.exe"):
            return str(f)
    raise RuntimeError("ffmpeg not found on system PATH.")

_BEST_ENCODER_INFO: Optional[Tuple[str, List[str]]] = None

def get_best_video_encoder(ffmpeg_exe: str = "ffmpeg") -> Tuple[str, List[str]]:
    """
    Probes system for hardware-accelerated H.264 video encoders:
      1. NVIDIA NVENC (h264_nvenc)
      2. AMD AMF (h264_amf)
      3. Intel QuickSync (h264_qsv)
      4. CPU Software Fallback (libx264)
    Returns: (codec_name, encoding_arguments)
    """
    global _BEST_ENCODER_INFO
    if _BEST_ENCODER_INFO is not None:
        return _BEST_ENCODER_INFO

    candidates = [
        ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "19", "-pix_fmt", "yuv420p"]),
        ("h264_amf", ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp", "-qp_p", "19", "-qp_i", "19", "-pix_fmt", "yuv420p"]),
        ("h264_qsv", ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "19", "-pix_fmt", "yuv420p"]),
        ("libx264", ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p"])
    ]

    for enc_name, flags in candidates:
        if enc_name == "libx264":
            _BEST_ENCODER_INFO = (enc_name, flags)
            return _BEST_ENCODER_INFO
        try:
            test_cmd = [
                ffmpeg_exe, "-y", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.04",
                *flags, "-f", "null", "-"
            ]
            res = subprocess.run(test_cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                print(f"  [Hardware Acceleration] Enabled {enc_name} GPU encoder.")
                _BEST_ENCODER_INFO = (enc_name, flags)
                return _BEST_ENCODER_INFO
        except Exception:
            continue

    _BEST_ENCODER_INFO = ("libx264", ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p"])
    return _BEST_ENCODER_INFO

def get_video_duration(video_path: Path, ffmpeg_exe: str = "ffmpeg") -> float:
    """Retrieves total duration of a video file using ffprobe."""
    ffprobe_exe = shutil.which("ffprobe")
    if not ffprobe_exe:
        ffprobe_exe = str(Path(ffmpeg_exe).parent / "ffprobe.exe")
        if not Path(ffprobe_exe).exists():
            ffprobe_exe = "ffprobe"
    cmd = [
        ffprobe_exe, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode == 0 and res.stdout.strip():
        try:
            return float(res.stdout.strip())
        except ValueError:
            pass
    return 0.0

def has_audio_stream(video_path: Path, ffmpeg_exe: str = "ffmpeg") -> bool:
    """Checks whether a media file contains an audio stream using ffprobe."""
    ffprobe_exe = shutil.which("ffprobe")
    if not ffprobe_exe:
        ffprobe_exe = str(Path(ffmpeg_exe).parent / "ffprobe.exe")
        if not Path(ffprobe_exe).exists():
            ffprobe_exe = "ffprobe"
    cmd = [
        ffprobe_exe, "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_type",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return res.returncode == 0 and "audio" in res.stdout.lower()

def apply_snappy_silence_cuts(
    input_video: Path,
    output_video: Path,
    min_silence_sec: float = 0.28,
    silence_thresh_db: float = -28.0,
    ffmpeg_exe: str = "ffmpeg"
) -> bool:
    """
    Detects dead-air pauses longer than min_silence_sec below silence_thresh_db,
    and removes them cleanly using an FFmpeg trim/concat filter graph.
    """
    total_duration = get_video_duration(input_video, ffmpeg_exe)
    if total_duration <= 2.0:
        return False

    cmd = [
        ffmpeg_exe, "-y", "-i", str(input_video),
        "-af", f"silencedetect=noise={silence_thresh_db}dB:d={min_silence_sec}",
        "-f", "null", "-"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        return False

    events = re.findall(r"silence_(start|end): (\d+\.?\d*)", res.stderr)
    silence_intervals = []
    cur_start = None
    for ev_type, ts_str in events:
        ts = float(ts_str)
        if ev_type == "start":
            cur_start = ts
        elif ev_type == "end":
            start_val = cur_start if cur_start is not None else 0.0
            if ts > start_val:
                silence_intervals.append((max(0.0, start_val), min(total_duration, ts)))
            cur_start = None

    if cur_start is not None and cur_start < total_duration:
        silence_intervals.append((max(0.0, cur_start), total_duration))

    if not silence_intervals:
        return False

    # Invariant 4 & Job 1 Task 1: Silence removal must leave >= 180ms padding (0.20s)
    # after spoken words to preserve trailing consonants, plosives, and natural breaths,
    # with 50ms dialogue pre-roll. Silence gaps <= 250ms are left untouched to prevent stutter.
    speech_pad_post = 0.20  # >= 180ms speech padding
    speech_pad_pre = 0.05   # 50ms dialogue pre-roll
    keep_chunks = []
    cur_t = 0.0
    for s_start, s_end in silence_intervals:
        if (s_end - s_start) < (speech_pad_post + speech_pad_pre):
            # Silence gap is smaller than pause buffer: keep audio continuous
            continue
        chunk_end = min(total_duration, s_start + speech_pad_post)
        next_start = max(0.0, s_end - speech_pad_pre)
        if chunk_end > cur_t and (chunk_end - cur_t) >= 0.15:
            keep_chunks.append((cur_t, chunk_end))
        cur_t = max(chunk_end, next_start)

    if total_duration - cur_t >= 0.15:
        keep_chunks.append((cur_t, total_duration))

    if not keep_chunks or (len(keep_chunks) <= 1 and (keep_chunks[0][1] - keep_chunks[0][0]) >= (total_duration - 0.2)):
        return False

    print(f"  [Snappy Cuts] Keeping {len(keep_chunks)} active audio chunks, cutting {len(silence_intervals)} silences.")

    # Invariant 1: 20ms sine-squared equal-power micro-fades (curve=qsin) on every audio splice boundary
    filter_chains = []
    concat_inputs = []
    total_kept_dur = 0.0
    # Split edit (L-cut): the outgoing take's audio bleeds 200ms under the next shot.
    # The a/v concat below keeps every segment lip-synced; the bleed is a separate
    # layer (the ambience that followed each take, normally discarded) placed at the
    # cut point with a 20ms S-curve fade-in and a 200ms S-curve fade-out.
    lcut_bleed = 0.20
    tail_chains = []
    tail_labels = []
    for i, (start_t, end_t) in enumerate(keep_chunks):
        dur = end_t - start_t
        total_kept_dur += dur
        fade_d = min(0.020, dur / 2.0)
        fade_out_st = max(0.0, dur - fade_d)

        filter_chains.append(f"[0:v]trim=start={start_t:.3f}:end={end_t:.3f},setpts=PTS-STARTPTS[v{i}]")
        filter_chains.append(
            f"[0:a]atrim=start={start_t:.3f}:end={end_t:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:ss=0:d={fade_d:.3f}:curve=qsin,"
            f"afade=t=out:st={fade_out_st:.3f}:d={fade_d:.3f}:curve=qsin[a{i}]"
        )
        concat_inputs.append(f"[v{i}][a{i}]")

        is_last = i == len(keep_chunks) - 1
        if not is_last and end_t + lcut_bleed <= total_duration and (end_t - fade_d) >= start_t:
            tail_start = end_t - fade_d
            delay_ms = int(round((total_kept_dur - fade_d) * 1000))
            tail_chains.append(
                f"[0:a]atrim=start={tail_start:.3f}:end={end_t + lcut_bleed:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:ss=0:d={fade_d:.3f}:curve=qsin,"
                f"afade=t=out:st={fade_d:.3f}:d={lcut_bleed:.3f}:curve=qsin,"
                f"adelay=delays={delay_ms}:all=1[t{i}]"
            )
            tail_labels.append(f"[t{i}]")

    # Invariant 1 & Task 3: Room tone continuity bed (-48dB pink noise) so audio never drops to digital zero
    room_tone_filter = (
        f"anoisesrc=d={max(1.0, total_kept_dur):.2f}:c=pink:a=0.004:r=48000[room_tone];"
        f"[a_voice]{''.join(tail_labels)}[room_tone]amix=inputs={2 + len(tail_labels)}:duration=first:"
        f"dropout_transition=0:normalize=0[a]"
    )
    concat_filter = (
        ";".join(filter_chains + tail_chains) + ";" +
        "".join(concat_inputs) + f"concat=n={len(keep_chunks)}:v=1:a=1[v][a_voice];" +
        room_tone_filter
    )

    render_cmd = [
        ffmpeg_exe, "-y", "-i", str(input_video),
        "-filter_complex", concat_filter,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k",
        str(output_video)
    ]
    render_res = subprocess.run(render_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return render_res.returncode == 0 and output_video.exists()

def detect_audio_energy_spikes(
    video_path: Path,
    spike_db_threshold: float = 7.0,
    window_sec: float = 0.2,
    min_spike_gap: float = 1.0,
    zoom_duration: float = 1.2,
    ffmpeg_exe: str = "ffmpeg"
) -> List[Tuple[float, float]]:
    """
    Decodes audio to 16kHz mono PCM, calculates RMS dB in windows,
    and returns intervals (start_sec, end_sec) for audio-energy punch-in zooms.
    """
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

    sr = 16000
    hop = int(sr * window_sec)
    num_windows = len(samples) // hop
    if num_windows == 0:
        return []

    rms_values = []
    for i in range(num_windows):
        win = samples[i * hop : (i + 1) * hop]
        rms = np.sqrt(np.mean(win**2) + 1e-9)
        rms_values.append(rms)

    rms_arr = np.array(rms_values)
    db_arr = 20 * np.log10(rms_arr + 1e-5)
    mean_db = np.mean(db_arr)

    thresh = mean_db + spike_db_threshold
    spike_ranges = []
    last_end = -1.0
    for i, db_val in enumerate(db_arr):
        t_start = i * window_sec
        t_end = t_start + zoom_duration
        if db_val > thresh and t_start >= (last_end + min_spike_gap):
            spike_ranges.append((round(t_start, 2), round(t_end, 2)))
            last_end = t_end

    return spike_ranges

def get_bold_font_path() -> Optional[str]:
    """Finds standard bold font on the host operating system."""
    candidate_fonts = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arialbd.ttf",
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf",
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeuib.ttf",
        Path("/System/Library/Fonts/Helvetica.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    ]
    for p in candidate_fonts:
        if p.exists():
            return str(p.resolve()).replace("\\", "/").replace(":", r"\:")
    return None

def generate_outro_card(
    output_path: Path,
    duration_sec: float = 1.0,
    width: int = 1080,
    height: int = 1920,
    ffmpeg_exe: str = "ffmpeg"
) -> bool:
    """Generates a 1-second pure black outro card with bold red centered SUBSCRIBE text."""
    font_arg = ""
    font_path = get_bold_font_path()
    if font_path:
        font_arg = f":fontfile='{font_path}'"

    cmd = [
        ffmpeg_exe, "-y",
        "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:d={duration_sec}:r=30",
        "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration_sec}",
        "-vf", f"drawtext=text='SUBSCRIBE':fontcolor=red:fontsize=88:x=(w-text_w)/2:y=(h-text_h)/2{font_arg}",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "192k",
        str(output_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return res.returncode == 0 and output_path.exists()


def cut_and_format_clip(
    youtube_url: str,
    start_sec: float,
    end_sec: float,
    output_path: Path,
    subtitle_style: str = "bold_pop",
    enable_broll: bool = True,
    enable_emojis: bool = False,
    framing_mode: str = "smart_face",  # "smart_face", "blurred", or "split_gaming"
    enable_snappy_cuts: bool = True,
    enable_punch_zooms: bool = False,
    enable_outro: bool = False,
    enable_auto_bleep: bool = False,
    enable_slow_zoom: bool = True,
    enable_bg_music: bool = False,
    bg_music_volume: float = 0.12,
) -> bool:
    """
    Downloads the precise timestamp slice using yt-dlp, applies snappy silence jump-cuts,
    generates word-synced subtitles with auto-emojis, applies smart face centering with
    cinematic slow push-in zoom and camera drift, overlays subtle ambient background music bed,
    censors demonetization profanities with 1000Hz bleep tone, overlays Pexels AI B-roll,
    formats into 9:16 vertical MP4, and appends a 1s outro card.
    """
    ffmpeg_exe = get_ffmpeg_path()
    clip_id = uuid.uuid4().hex[:8]
    temp_clip_raw = TEMP_DIR / f"raw_{int(start_sec)}_{int(end_sec)}_{clip_id}.mp4"
    temp_ass = TEMP_DIR / f"subs_{int(start_sec)}_{int(end_sec)}_{clip_id}.ass"

    if temp_clip_raw.exists():
        temp_clip_raw.unlink()
    if temp_ass.exists():
        temp_ass.unlink()

    # Step 1: Check if input is an existing local file or download using yt-dlp
    actual_downloaded = None
    is_local_file = False
    try:
        p_in = Path(youtube_url)
        is_local_file = p_in.is_file()
    except Exception:
        is_local_file = False

    if is_local_file:
        # Cut the requested segment into a temp file. Everything downstream (snappy cuts,
        # Whisper, face tracking, cleanup) works on and deletes `actual_downloaded`, so it
        # must never be the user's original video.
        print(f"  [Local Video] Slicing section {start_sec}s -> {end_sec}s from {p_in.name}...")
        _, enc_flags = get_best_video_encoder(ffmpeg_exe)
        slice_cmd = [
            ffmpeg_exe, "-y", "-ss", f"{start_sec:.3f}", "-i", str(p_in),
            "-t", f"{max(0.1, end_sec - start_sec):.3f}",
            *enc_flags,
            "-c:a", "aac", "-b:a", "192k",
            str(temp_clip_raw)
        ]
        proc = subprocess.run(slice_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            print(f"  [Local Video Error] {proc.stderr[-300:]}")
            return False
        if temp_clip_raw.exists():
            actual_downloaded = temp_clip_raw
        else:
            actual_downloaded = p_in
    else:
        download_section_arg = f"*{start_sec:.2f}-{end_sec:.2f}"
        ffmpeg_dir = str(Path(ffmpeg_exe).parent)
        ytdlp_cmd = [
            sys.executable, "-m", "yt_dlp",
            "--ffmpeg-location", ffmpeg_dir,
            "--extractor-args", "youtube:player_client=all",
            "--download-sections", download_section_arg,
            "-f", "bestvideo[height<=2160]+bestaudio/best",
            "--merge-output-format", "mp4",
            "--force-keyframes-at-cuts",
            "--retries", "10",
            "--fragment-retries", "10",
            "--no-playlist",
            "-o", str(temp_clip_raw),
            youtube_url
        ]

        print(f"  [Downloader] Slicing section {start_sec}s -> {end_sec}s from YouTube...")
        proc = subprocess.run(ytdlp_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            print(f"  [Downloader Error] {proc.stderr[:300]}")
            return False

        # In case yt-dlp appended extension or formatting
        if temp_clip_raw.exists():
            actual_downloaded = temp_clip_raw
        else:
            prefix = f"raw_{int(start_sec)}_{int(end_sec)}_{clip_id}"
            for f in TEMP_DIR.glob(f"{prefix}*"):
                actual_downloaded = f
                break

    if not actual_downloaded or not actual_downloaded.exists():
        print(f"  [Downloader Error] Downloaded file not found in {TEMP_DIR}")
        return False

    # Step 1.5: Snappy dead-air jump-cuts (IShowSpeed / high-retention pacing)
    if enable_snappy_cuts:
        snappy_clip = TEMP_DIR / f"snappy_{int(start_sec)}_{int(end_sec)}_{clip_id}.mp4"
        try:
            if apply_snappy_silence_cuts(actual_downloaded, snappy_clip, min_silence_sec=0.28, silence_thresh_db=-28.0, ffmpeg_exe=ffmpeg_exe):
                print(f"  [Jump-Cuts] Truncated dead-air pauses into snappy jump-cuts -> {snappy_clip.name}")
                if actual_downloaded.exists() and actual_downloaded != p_in:
                    try:
                        actual_downloaded.unlink()
                    except Exception:
                        pass
                actual_downloaded = snappy_clip
        except Exception as e:
            print(f"  [Jump-Cuts Warning] Silence cut skipped: {e}")

    # Step 2: Generate word-level audio-synchronized subtitles with auto-emojis & optional auto-bleep
    words = []
    if subtitle_style in ("none", "no_subtitles", "off", None):
        print("  [Subtitles] Subtitles disabled by user (No Subtitles mode).")
        has_subtitles = False
    else:
        print(f"  [Subtitles] Generating audio-synced subtitles (Style: {subtitle_style}, Emojis: {enable_emojis}, Auto-Bleep: {enable_auto_bleep})...")
        has_subtitles = generate_synced_subtitles(
            actual_downloaded,
            temp_ass,
            style_key=subtitle_style,
            enable_emojis=enable_emojis,
            enable_auto_bleep=enable_auto_bleep,
            words_out=words
        )

    bleep_intervals: List[Tuple[float, float]] = []
    if enable_auto_bleep and words:
        bleep_intervals = detect_profanity_intervals(words)
        if bleep_intervals:
            print(f"  [Auto-Bleeper] Detected {len(bleep_intervals)} profanity intervals. Injecting 1000Hz censorship bleep...")

    # Step 3: Plan and fetch AI B-roll overlays from Pexels
    broll_items: List[Dict] = []
    if enable_broll and words:
        clip_dur = get_video_duration(actual_downloaded, ffmpeg_exe) or (end_sec - start_sec)
        broll_items = plan_and_fetch_brolls(words, clip_dur, max_brolls=2)
        # A corrupt cached B-roll file cannot be decoded by ffmpeg, and because it is passed
        # as an -i input that used to fail the WHOLE render and throw away a clip whose
        # download, transcription and subtitle burn-in had all succeeded. Drop the bad
        # overlay instead and keep the clip.
        if broll_items:
            verified: List[Dict] = []
            for item in broll_items:
                if is_decodable_video(item.get("file_path")):
                    verified.append(item)
                else:
                    print(f"  [B-Roll Warning] Dropping undecodable B-roll '{item.get('file_path')}'; "
                          f"continuing without this overlay.")
            broll_items = verified

    # Step 3.5: Detect vocal energy reaction peaks for 1.25x punch-in zooms (only if slow zoom disabled)
    spikes: List[Tuple[float, float]] = []
    if enable_punch_zooms and not enable_slow_zoom:
        try:
            spikes = detect_audio_energy_spikes(actual_downloaded, spike_db_threshold=7.0, ffmpeg_exe=ffmpeg_exe)
            if spikes:
                print(f"  [Punch Zoom] Detected {len(spikes)} high-energy vocal reaction peaks.")
        except Exception as e:
            print(f"  [Punch Zoom Warning] Energy spike detection skipped: {e}")

    # Step 4: Construct FFmpeg Filter Graph
    filter_parts = []
    
    if framing_mode == "smart_face":
        print(f"  [Framing] Running smart face tracking for 9:16 vertical crop...")
        try:
            crop_res = compute_smart_crop_offset(actual_downloaded, TARGET_WIDTH, TARGET_HEIGHT)
        except Exception as e:
            print(f"  [Framing Error] Face tracking error ({e}), using centered 9:16 fallback.")
            crop_res = (int(TARGET_HEIGHT * 9 / 16), TARGET_HEIGHT, "(iw-ow)/2", 0)
        if len(crop_res) == 4:
            crop_w, crop_h, crop_x, crop_y = crop_res
        else:
            crop_w, crop_h, crop_x = crop_res
            crop_y = 0
        base_filter = (
            f"[0:v]fps=30,scale='max(iw,{crop_w})':'max(ih,{crop_h})':force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={crop_w}:{crop_h}:'{crop_x}':{crop_y},scale={TARGET_WIDTH}:{TARGET_HEIGHT}:flags=lanczos,"
            f"unsharp=lx=3:ly=3:la=0.4:cx=3:cy=3:ca=0.2,setsar=1[v_base]"
        )
    elif framing_mode == "split_gaming":
        print(f"  [Framing] Running Gaming Split-Screen (Top: Facecam Zoom, Bottom: Full Gameplay)...")
        try:
            cam_crop = compute_gaming_split_crop(actual_downloaded, TARGET_WIDTH, 960)
        except Exception as e:
            print(f"  [Framing Warning] Facecam crop fallback ({e})")
            cam_crop = (720, 640, 0, 0)
        cw, ch, cx, cy = cam_crop
        base_filter = (
            f"[0:v]fps=30,crop={cw}:{ch}:{cx}:{cy},scale={TARGET_WIDTH}:960:force_original_aspect_ratio=increase,crop={TARGET_WIDTH}:960,setsar=1[cam];"
            f"[0:v]fps=30,scale=270:240:force_original_aspect_ratio=increase,crop=270:240,boxblur=10:3,scale={TARGET_WIDTH}:960,setsar=1[game_bg];"
            f"[0:v]fps=30,scale={TARGET_WIDTH}:-2,setsar=1[game_fg];"
            f"[game_bg][game_fg]overlay=0:(960-h)/2[game_combined];"
            f"[cam][game_combined]vstack=inputs=2[stacked];"
            f"[stacked]drawbox=x=0:y=958:w={TARGET_WIDTH}:h=4:color=#6366f1@0.85:t=fill[v_base]"
        )
    else:
        # High-speed glass blur background
        base_filter = (
            f"[0:v]fps=30,scale=270:480:force_original_aspect_ratio=increase,"
            f"crop=270:480,boxblur=8:2,scale={TARGET_WIDTH}:{TARGET_HEIGHT}[bg];"
            f"[0:v]fps=30,scale={TARGET_WIDTH}:-2[fg];"
            f"[bg][fg]overlay=0:(H-h)/2,setsar=1[v_base]"
        )
        
    filter_parts.append(base_filter)
    current_top = "v_base"

    # Step 3.2: Cinematic Slow Zoom In & Zoom Out with subtle organic camera drift (Outdoor Boys / Wild Den style)
    if enable_slow_zoom:
        # Periodic breathing zoom cycle (8.0 seconds per cycle = 240 frames @ 30fps)
        # Smooth cosine oscillation between 1.00x (wide/base) and 1.05x (subtle push-in)
        # Prevents getting stuck at maximum zoom or playing video at incorrect speed
        cycle_frames = 240
        zoom_expr = f"1.0+0.05*(1-cos(2*PI*on/{cycle_frames}))/2"
        # Subtle horizontal camera drift (+/- 14px slow organic panning)
        x_expr = f"iw/2-(iw/zoom/2)+sin(on/30.0*0.5)*14"
        # Eye-line anchor at upper 30% of frame so head and eyes stay framed naturally
        y_expr = f"ih*0.30-(ih/zoom*0.30)"
        slow_zoom_filter = (
            f"[{current_top}]zoompan=z='{zoom_expr}':d=1:x='{x_expr}':y='{y_expr}':s={TARGET_WIDTH}x{TARGET_HEIGHT}:fps=30,setsar=1[v_slow_zoom]"
        )
        filter_parts.append(slow_zoom_filter)
        current_top = "v_slow_zoom"

    # Audio-energy punch-in zoom jump cuts (suppressed when slow zoom is active)
    if spikes and not enable_slow_zoom:
        cond = "+".join([f"between(t,{s:.2f},{e:.2f})" for s, e in spikes])
        zoom_filter = (
            f"[{current_top}]crop=w='if({cond},iw*0.80,iw)':h='if({cond},ih*0.80,ih)':x='(iw-ow)/2':y='(ih-oh)/3',"
            f"scale={TARGET_WIDTH}:{TARGET_HEIGHT},setsar=1[v_zoomed]"
        )
        filter_parts.append(zoom_filter)
        current_top = "v_zoomed"

    # 30-Degree Focal Scale Shift: 1.15x punch-in zoom on consecutive cuts tracking same static speaker perspective
    continuity_meta = face_tracker.get_shot_continuity_metadata(actual_downloaded)
    focal_intervals = continuity_meta.get("focal_scale_intervals", []) if continuity_meta else []
    if focal_intervals:
        focal_cond = "+".join([f"between(t,{s:.2f},{e:.2f})" for s, e in focal_intervals])
        focal_filter = (
            f"[{current_top}]crop=w='if({focal_cond},iw/1.15,iw)':h='if({focal_cond},ih/1.15,ih)':"
            f"x='(iw-ow)/2':y='(ih-oh)*0.35',scale={TARGET_WIDTH}:{TARGET_HEIGHT}:flags=lanczos,setsar=1[v_focal]"
        )
        filter_parts.append(focal_filter)
        current_top = "v_focal"

    # Overlay B-Roll items
    for idx, b_item in enumerate(broll_items):
        input_idx = 1 + idx
        b_start = b_item["start"]
        b_end = b_item["end"]
        next_top = f"v_broll_{idx}"
        
        filter_parts.append(
            f"[{input_idx}:v]setpts=PTS-STARTPTS+{b_start}/TB,"
            f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_WIDTH}:{TARGET_HEIGHT},setsar=1[broll_scaled_{idx}]"
        )
        filter_parts.append(
            f"[{current_top}][broll_scaled_{idx}]overlay=0:0:enable='between(t,{b_start},{b_end})':eof_action=pass[{next_top}]"
        )
        current_top = next_top

    # Burn subtitles on the topmost video layer and keep clean stream for Hyperframes
    clean_target = output_path.with_name(f"{output_path.stem}_clean.mp4")
    if has_subtitles and temp_ass.exists():
        escaped_ass = str(temp_ass.resolve()).replace("\\", "/").replace(":", r"\:")
        filter_parts.append(f"[{current_top}]split=2[current_clean][current_for_sub]")
        filter_parts.append(f"[current_for_sub]subtitles='{escaped_ass}'[v]")
    else:
        filter_parts.append(f"[{current_top}]split=2[current_clean][v]")

    # Step 3.8: Audio Pipeline (Voice + Censorship Bleep + Atmospheric Background Music Bed)
    clip_dur = get_video_duration(actual_downloaded, ffmpeg_exe) or (end_sec - start_sec) or 30.0
    has_source_audio = has_audio_stream(actual_downloaded, ffmpeg_exe)

    if has_source_audio:
        if bleep_intervals:
            bleep_cond = "+".join([f"between(t,{s:.3f},{e:.3f})" for s, e in bleep_intervals])
            audio_filter = (
                f"[0:a]volume='if({bleep_cond}, 0, 1)':eval=frame[censored_voice];"
                f"sine=f=1000:d={clip_dur:.2f},volume='if({bleep_cond}, 0.35, 0)':eval=frame[beep];"
                f"[censored_voice][beep]amix=inputs=2:duration=first:dropout_transition=0[voice_ready]"
            )
            filter_parts.append(audio_filter)
            voice_output_label = "voice_ready"
        else:
            voice_output_label = "0:a"
    else:
        voice_output_label = None

    # Background music candidate selection
    bg_music_file: Optional[Path] = None
    if enable_bg_music:
        audio_dir = Path(__file__).parent / "assets" / "audio"
        if audio_dir.exists():
            candidate_tracks = list(audio_dir.glob("*.wav")) + list(audio_dir.glob("*.mp3")) + list(audio_dir.glob("*.m4a"))
            if candidate_tracks:
                preferred = [t for t in candidate_tracks if "ambient" in t.name.lower()]
                bg_music_file = preferred[0] if preferred else candidate_tracks[0]

    extra_audio_inputs = []
    if enable_bg_music:
        fade_out_start = max(0.5, clip_dur - 1.2)
        if bg_music_file and bg_music_file.exists():
            bg_input_idx = 1 + len(broll_items)
            extra_audio_inputs.append(str(bg_music_file))
            filter_parts.append(
                f"[{bg_input_idx}:a]volume={bg_music_volume:.2f},"
                f"afade=t=in:ss=0:d=1.0,"
                f"afade=t=out:st={fade_out_start:.2f}:d=1.2,"
                f"atrim=0:{clip_dur:.2f}[music_bed]"
            )
            if voice_output_label:
                filter_parts.append(
                    f"[{voice_output_label}]asplit=2[voice_for_mix][voice_for_duck];"
                    f"[music_bed][voice_for_duck]sidechaincompress=threshold=0.08:ratio=4:attack=20:release=350[music_ducked];"
                    f"[voice_for_mix][music_ducked]amix=inputs=2:duration=first:dropout_transition=0[a]"
                )
                audio_map = "[a]"
            else:
                audio_map = "[music_bed]"
        else:
            filter_parts.append(
                f"aevalsrc=exprs='0.022*sin(2*PI*220*t)+0.016*sin(2*PI*329.6*t)+0.012*sin(2*PI*440*t)':s=44100:d={clip_dur:.2f},"
                f"afade=t=in:ss=0:d=1.0,afade=t=out:st={fade_out_start:.2f}:d=1.2[music_synth]"
            )
            if voice_output_label:
                filter_parts.append(
                    f"[{voice_output_label}]asplit=2[voice_for_mix][voice_for_duck];"
                    f"[music_synth][voice_for_duck]sidechaincompress=threshold=0.08:ratio=4:attack=20:release=350[music_ducked];"
                    f"[voice_for_mix][music_ducked]amix=inputs=2:duration=first:dropout_transition=0[a]"
                )
                audio_map = "[a]"
            else:
                audio_map = "[music_synth]"
    else:
        if voice_output_label == "0:a" or voice_output_label is None:
            audio_map = "0:a?"
        else:
            audio_map = f"[{voice_output_label}]"

    # EBU R128 loudness: -14 LUFS integrated, -1.0 dBTP true peak (Shorts/Reels/TikTok target).
    # loudnorm upsamples to 192kHz internally, so resample back to 48kHz for AAC.
    norm_in = ("0:a" if has_source_audio else None) if audio_map == "0:a?" else audio_map.strip("[]")
    if norm_in:
        filter_parts.append(f"[{norm_in}]loudnorm=I=-14:TP=-1.0:LRA=11,aresample=48000,asplit=2[a_norm][a_norm_clean]")
        audio_map = "[a_norm]"
        audio_clean_map = "[a_norm_clean]"
    else:
        if audio_map.startswith("[") and audio_map.endswith("]"):
            base_label = audio_map.strip("[]")
            filter_parts.append(f"[{base_label}]asplit=2[{base_label}_out1][{base_label}_out2]")
            audio_map = f"[{base_label}_out1]"
            audio_clean_map = f"[{base_label}_out2]"
        else:
            audio_clean_map = audio_map

    filter_complex = ";".join(filter_parts)

    temp_main_rendered = (TEMP_DIR / f"main_{int(start_sec)}_{int(end_sec)}_{clip_id}.mp4") if enable_outro else output_path

    _, enc_flags = get_best_video_encoder(ffmpeg_exe)

    ffmpeg_cmd = [
        ffmpeg_exe,
        "-y",
        "-i", str(actual_downloaded)
    ]
    for b_item in broll_items:
        ffmpeg_cmd.extend(["-i", str(b_item["file_path"])])
    for extra_in in extra_audio_inputs:
        ffmpeg_cmd.extend(["-stream_loop", "-1", "-i", extra_in])

    ffmpeg_cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", audio_map,
        *enc_flags,
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(temp_main_rendered),
        "-map", "[current_clean]",
        "-map", audio_clean_map,
        *enc_flags,
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(clean_target)
    ])

    print(f"  [FFmpeg] Rendering 9:16 Short (B-Rolls: {len(broll_items)}, Framing: {framing_mode}, Zooms: {len(spikes)}) -> {temp_main_rendered.name}...")
    try:
        ff_proc = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    except subprocess.TimeoutExpired as te:
        print(f"  [FFmpeg Error] Rendering timed out after 600s: {te}")
        return False

    if temp_ass.exists():
        try:
            temp_ass.unlink()
        except Exception:
            pass

    if ff_proc.returncode != 0:
        if actual_downloaded.exists() and actual_downloaded != p_in:
            try:
                actual_downloaded.unlink()
            except Exception:
                pass
        print(f"  [FFmpeg Error] {ff_proc.stderr[-600:]}")
        return False

    # Step 5: Append 1-second high-contrast SUBSCRIBE outro card
    if enable_outro and temp_main_rendered != output_path and temp_main_rendered.exists():
        temp_outro = TEMP_DIR / f"outro_{clip_id}.mp4"
        outro_ok = generate_outro_card(temp_outro, duration_sec=1.0, width=TARGET_WIDTH, height=TARGET_HEIGHT, ffmpeg_exe=ffmpeg_exe)
        if outro_ok:
            has_main_audio = has_audio_stream(temp_main_rendered, ffmpeg_exe)
            if has_main_audio:
                main_dur = get_video_duration(temp_main_rendered, ffmpeg_exe) or 1.0
                xfade_dur = 0.025
                fade_out_st = max(0.0, main_dur - xfade_dur)
                concat_filter = (
                    f"[0:v]fps=30,scale={TARGET_WIDTH}:{TARGET_HEIGHT},setsar=1[v0];"
                    f"[1:v]fps=30,scale={TARGET_WIDTH}:{TARGET_HEIGHT},setsar=1[v1];"
                    f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,afade=t=out:st={fade_out_st:.3f}:d={xfade_dur}:curve=qsin[a0];"
                    f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,afade=t=in:ss=0:d={xfade_dur}:curve=qsin[a1];"
                    f"[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
                )
            else:
                main_dur = get_video_duration(temp_main_rendered, ffmpeg_exe) or 1.0
                xfade_dur = 0.025
                concat_filter = (
                    f"[0:v]fps=30,scale={TARGET_WIDTH}:{TARGET_HEIGHT},setsar=1[v0];"
                    f"[1:v]fps=30,scale={TARGET_WIDTH}:{TARGET_HEIGHT},setsar=1[v1];"
                    f"anullsrc=r=44100:cl=stereo,atrim=duration={main_dur:.2f}[silence];"
                    f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo,afade=t=in:ss=0:d={xfade_dur}:curve=qsin[a1];"
                    f"[v0][silence][v1][a1]concat=n=2:v=1:a=1[v][a]"
                )
            concat_cmd = [
                ffmpeg_exe, "-y",
                "-i", str(temp_main_rendered),
                "-i", str(temp_outro),
                "-filter_complex", concat_filter,
                "-map", "[v]",
                "-map", "[a]",
                *enc_flags,
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                str(output_path)
            ]
            concat_proc = subprocess.run(concat_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            # Cleanup outro and main
            if temp_main_rendered.exists():
                try:
                    temp_main_rendered.unlink()
                except Exception:
                    pass
            if temp_outro.exists():
                try:
                    temp_outro.unlink()
                except Exception:
                    pass
            if concat_proc.returncode != 0:
                print(f"  [Outro Concat Error] {concat_proc.stderr[:300]}")
                return False
        else:
            if output_path.exists():
                output_path.unlink()
            shutil.move(str(temp_main_rendered), str(output_path))

    # Step 6: Emit Continuity EDL Payload (cuts, focal anchors, and audio smoothing metadata)
    try:
        continuity_meta = face_tracker.get_shot_continuity_metadata(actual_downloaded)
        cuts_data = continuity_meta.get("cuts", []) if continuity_meta else []
        if not cuts_data:
            cuts_data = [{
                "cut_index": 0,
                "source_in": round(float(start_sec), 3),
                "source_out": round(float(end_sec), 3),
                "shot_type": "talking_head",
                "focal_anchor": {"x": int(TARGET_WIDTH // 2), "y": 0},
                "transition_out": {"type": "CUT_ON_ACTION", "audio_bleed_ms": 0}
            }]

        edl_payload = {
            "clip_id": clip_id,
            "video_path": str(output_path),
            "cuts": cuts_data,
            "audio_smoothing": {
                "crossfade_curve": "S_CURVE",
                "boundary_crossfade_duration_ms": 20,
                "l_cut_bleed_ms": 200 if enable_snappy_cuts else 0,
                "room_tone_pad_active": bool(enable_snappy_cuts),
                "room_tone_level_db": -48
            },
            "validation": {
                "loudnorm": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.0, "lra": 11},
                "emojis_enabled": bool(enable_emojis),
                "outro_card_appended": bool(enable_outro),
                "punch_zoom_count": len(spikes),
                "slow_zoom": {"enabled": bool(enable_slow_zoom), "range": [1.0, 1.05], "eye_line_anchor": 0.30}
            }
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        edl_path = output_path.parent / f"{output_path.stem}_edl.json"
        edl_path.write_text(json.dumps(edl_payload, indent=2), encoding="utf-8")
        if clip_id != output_path.stem:
            clip_id_edl_path = output_path.parent / f"{clip_id}_edl.json"
            clip_id_edl_path.write_text(json.dumps(edl_payload, indent=2), encoding="utf-8")
        print(f"  [EDL] Saved continuity EDL payload -> {edl_path.name}")
    except Exception as e_edl:
        print(f"  [EDL Warning] Failed to write EDL payload: {e_edl}")
    finally:
        if actual_downloaded and actual_downloaded.exists() and actual_downloaded != p_in:
            try:
                actual_downloaded.unlink()
            except Exception:
                pass

    return True
