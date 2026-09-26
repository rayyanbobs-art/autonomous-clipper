import os
import re
import subprocess
import shutil
import uuid
import sys
from pathlib import Path
from typing import Optional, List, Dict, Tuple
import numpy as np
from config import TEMP_DIR, TARGET_WIDTH, TARGET_HEIGHT
from subtitles import generate_synced_subtitles, detect_profanity_intervals
from broll_engine import plan_and_fetch_brolls
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

    keep_chunks = []
    cur_t = 0.0
    for s_start, s_end in silence_intervals:
        if s_start - cur_t >= 0.15:
            keep_chunks.append((cur_t, s_start))
        cur_t = s_end

    if total_duration - cur_t >= 0.15:
        keep_chunks.append((cur_t, total_duration))

    if not keep_chunks or (len(keep_chunks) <= 1 and (keep_chunks[0][1] - keep_chunks[0][0]) >= (total_duration - 0.2)):
        return False

    print(f"  [Snappy Cuts] Keeping {len(keep_chunks)} active audio chunks, cutting {len(silence_intervals)} silences.")

    filter_chains = []
    concat_inputs = []
    for i, (start_t, end_t) in enumerate(keep_chunks):
        filter_chains.append(f"[0:v]trim=start={start_t:.3f}:end={end_t:.3f},setpts=PTS-STARTPTS[v{i}]")
        filter_chains.append(f"[0:a]atrim=start={start_t:.3f}:end={end_t:.3f},asetpts=PTS-STARTPTS[a{i}]")
        concat_inputs.append(f"[v{i}][a{i}]")

    concat_filter = ";".join(filter_chains) + ";" + "".join(concat_inputs) + f"concat=n={len(keep_chunks)}:v=1:a=1[v][a]"

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
    enable_emojis: bool = True,
    framing_mode: str = "blurred",  # "blurred" or "smart_face"
    enable_snappy_cuts: bool = True,
    enable_punch_zooms: bool = True,
    enable_outro: bool = True,
    enable_auto_bleep: bool = False
) -> bool:
    """
    Downloads the precise timestamp slice using yt-dlp, applies snappy silence jump-cuts,
    generates word-synced subtitles with auto-emojis, applies audio-energy punch-in zooms,
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

    # Step 1: Download the exact section using yt-dlp
    download_section_arg = f"*{start_sec:.2f}-{end_sec:.2f}"
    ffmpeg_dir = str(Path(ffmpeg_exe).parent)
    ytdlp_cmd = [
        sys.executable, "-m", "yt_dlp",
        "--ffmpeg-location", ffmpeg_dir,
        "--download-sections", download_section_arg,
        "-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
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
    actual_downloaded = None
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
                if actual_downloaded.exists():
                    try:
                        actual_downloaded.unlink()
                    except Exception:
                        pass
                actual_downloaded = snappy_clip
        except Exception as e:
            print(f"  [Jump-Cuts Warning] Silence cut skipped: {e}")

    # Step 2: Generate word-level audio-synchronized subtitles with auto-emojis & optional auto-bleep
    print(f"  [Subtitles] Generating audio-synced subtitles (Style: {subtitle_style}, Emojis: {enable_emojis}, Auto-Bleep: {enable_auto_bleep})...")
    words = []
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

    # Step 3.5: Detect vocal energy reaction peaks for 1.25x punch-in zooms
    spikes: List[Tuple[float, float]] = []
    if enable_punch_zooms:
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
            f"[0:v]scale='max(iw,{crop_w})':'max(ih,{crop_h})':force_original_aspect_ratio=increase,"
            f"crop={crop_w}:{crop_h}:'{crop_x}':{crop_y},scale={TARGET_WIDTH}:{TARGET_HEIGHT},"
            f"setsar=1[v_base]"
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
            f"[0:v]crop={cw}:{ch}:{cx}:{cy},scale={TARGET_WIDTH}:960:force_original_aspect_ratio=increase,crop={TARGET_WIDTH}:960,setsar=1[cam];"
            f"[0:v]scale=270:240:force_original_aspect_ratio=increase,crop=270:240,boxblur=10:3,scale={TARGET_WIDTH}:960,setsar=1[game_bg];"
            f"[0:v]scale={TARGET_WIDTH}:-2,setsar=1[game_fg];"
            f"[game_bg][game_fg]overlay=0:(960-h)/2[game_combined];"
            f"[cam][game_combined]vstack=inputs=2[stacked];"
            f"[stacked]drawbox=x=0:y=958:w={TARGET_WIDTH}:h=4:color=#6366f1@0.85:t=fill[v_base]"
        )
    else:
        # High-speed glass blur background
        base_filter = (
            f"[0:v]scale=270:480:force_original_aspect_ratio=increase,"
            f"crop=270:480,boxblur=8:2,scale={TARGET_WIDTH}:{TARGET_HEIGHT}[bg];"
            f"[0:v]scale={TARGET_WIDTH}:-2[fg];"
            f"[bg][fg]overlay=0:(H-h)/2,setsar=1[v_base]"
        )
        
    filter_parts.append(base_filter)
    current_top = "v_base"

    # Audio-energy punch-in zoom jump cuts
    if spikes:
        cond = "+".join([f"between(t,{s:.2f},{e:.2f})" for s, e in spikes])
        zoom_filter = (
            f"[{current_top}]crop=w='if({cond},iw*0.80,iw)':h='if({cond},ih*0.80,ih)':x='(iw-ow)/2':y='(ih-oh)/3',"
            f"scale={TARGET_WIDTH}:{TARGET_HEIGHT},setsar=1[v_zoomed]"
        )
        filter_parts.append(zoom_filter)
        current_top = "v_zoomed"

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

    # Burn subtitles on the topmost video layer
    if has_subtitles and temp_ass.exists():
        escaped_ass = str(temp_ass.resolve()).replace("\\", "/").replace(":", r"\:")
        filter_parts.append(f"[{current_top}]subtitles='{escaped_ass}'[v]")
    else:
        filter_parts.append(f"[{current_top}]null[v]")

    # Audio Censorship Bleep Graph
    if bleep_intervals:
        clip_dur = get_video_duration(actual_downloaded, ffmpeg_exe) or (end_sec - start_sec)
        bleep_cond = "+".join([f"between(t,{s:.3f},{e:.3f})" for s, e in bleep_intervals])
        audio_filter = (
            f"[0:a]volume='if({bleep_cond}, 0, 1)':eval=frame[censored_voice];"
            f"sine=f=1000:d={clip_dur:.2f},volume='if({bleep_cond}, 0.35, 0)':eval=frame[beep];"
            f"[censored_voice][beep]amix=inputs=2:duration=first:dropout_transition=0[a]"
        )
        filter_parts.append(audio_filter)
        audio_map = "[a]"
    else:
        audio_map = "0:a?"

    filter_complex = ";".join(filter_parts)

    temp_main_rendered = (TEMP_DIR / f"main_{int(start_sec)}_{int(end_sec)}_{clip_id}.mp4") if enable_outro else output_path

    ffmpeg_cmd = [
        ffmpeg_exe,
        "-y",
        "-i", str(actual_downloaded)
    ]
    for b_item in broll_items:
        ffmpeg_cmd.extend(["-i", str(b_item["file_path"])])

    ffmpeg_cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", audio_map,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(temp_main_rendered)
    ])

    print(f"  [FFmpeg] Rendering 9:16 Short (B-Rolls: {len(broll_items)}, Framing: {framing_mode}, Zooms: {len(spikes)}) -> {temp_main_rendered.name}...")
    try:
        ff_proc = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    except subprocess.TimeoutExpired as te:
        print(f"  [FFmpeg Error] Rendering timed out after 600s: {te}")
        return False

    # Cleanup temp video slices
    if actual_downloaded.exists():
        try:
            actual_downloaded.unlink()
        except Exception:
            pass
    if temp_ass.exists():
        try:
            temp_ass.unlink()
        except Exception:
            pass

    if ff_proc.returncode != 0:
        print(f"  [FFmpeg Error] {ff_proc.stderr[:400]}")
        return False

    # Step 5: Append 1-second high-contrast SUBSCRIBE outro card
    if enable_outro and temp_main_rendered != output_path and temp_main_rendered.exists():
        temp_outro = TEMP_DIR / f"outro_{clip_id}.mp4"
        outro_ok = generate_outro_card(temp_outro, duration_sec=1.0, width=TARGET_WIDTH, height=TARGET_HEIGHT, ffmpeg_exe=ffmpeg_exe)
        if outro_ok:
            has_main_audio = has_audio_stream(temp_main_rendered, ffmpeg_exe)
            if has_main_audio:
                concat_filter = f"[0:v]scale={TARGET_WIDTH}:{TARGET_HEIGHT},setsar=1[v0];[1:v]scale={TARGET_WIDTH}:{TARGET_HEIGHT},setsar=1[v1];[v0][0:a][v1][1:a]concat=n=2:v=1:a=1[v][a]"
            else:
                main_dur = get_video_duration(temp_main_rendered, ffmpeg_exe) or 1.0
                concat_filter = f"[0:v]scale={TARGET_WIDTH}:{TARGET_HEIGHT},setsar=1[v0];[1:v]scale={TARGET_WIDTH}:{TARGET_HEIGHT},setsar=1[v1];anullsrc=r=44100:cl=stereo,atrim=duration={main_dur:.2f}[silence];[v0][silence][v1][1:a]concat=n=2:v=1:a=1[v][a]"
            concat_cmd = [
                ffmpeg_exe, "-y",
                "-i", str(temp_main_rendered),
                "-i", str(temp_outro),
                "-filter_complex", concat_filter,
                "-map", "[v]",
                "-map", "[a]",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "22",
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

    return True
