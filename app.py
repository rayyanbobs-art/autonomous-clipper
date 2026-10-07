import os
import sys
import json
import time
import uuid
import shutil
import threading
import subprocess
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory

from typing import Optional, Any
from config import (
    OUTPUT_DIR,
    get_jev_api_key,
    MIN_CLIP_DURATION,
    MAX_CLIP_DURATION,
    CLIP_WINDOW_STEP,
)
from scorer import create_windows, critique_gate_search, parse_timestamp_to_seconds
from video_cutter import cut_and_format_clip, get_video_duration
from clipper import extract_video_id, get_transcript, is_local_video, get_local_transcript
from subtitles import SUBTITLE_STYLES
from niche_scraper import search_niche_channels, get_channel_details, get_video_transcript
from niche_analyzer import analyze_channel_outliers, analyze_hook_and_pacing
from niche_generator import generate_viral_ideas
# Safeguard UTF-8 encoding on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from title_tag_engine import generate_smart_title_and_hashtags, build_video_context
from publish_log import build_publication_record, log_publication, default_log_path
from uploader import (
    load_upload_config, save_upload_config, get_youtube_auth_url,
    exchange_youtube_code, start_youtube_local_auth, upload_clip_to_platforms
)
import hyperframes_editor

app = Flask(__name__)

# In-memory job tracker
JOBS = {}

# Completed/failed jobs are retained for this long so a browser that polls slowly can still
# read the result. Without eviction, JOBS grows monotonically for the life of the server and
# each entry retains the full clip metadata (transcript snippets, critique records) forever.
JOB_RETENTION_SECONDS = 3600
JOB_MAX_ENTRIES = 200

# In-memory YouTube OAuth handshake tracker (request id -> status/result)
AUTH_JOBS = {}


def _prune_jobs(now: float = None) -> None:
    """Evicts expired and overflow job entries. Safe to call from any thread."""
    now = time.time() if now is None else now
    for job_id in [
        jid for jid, j in JOBS.items()
        if isinstance(j.get("_finished_at"), (int, float)) and (now - j["_finished_at"]) > JOB_RETENTION_SECONDS
    ]:
        JOBS.pop(job_id, None)

    overflow = len(JOBS) - JOB_MAX_ENTRIES
    if overflow > 0:
        # Drop the oldest jobs first.
        for job_id in sorted(JOBS.keys(), key=lambda jid: JOBS[jid].get("_created_at", 0))[:overflow]:
            JOBS.pop(job_id, None)


_JOBS_LOCK = threading.Lock()


def _jobs_manifest_path() -> Path:
    # Resolved at call time so tests that patch OUTPUT_DIR also redirect the manifest.
    # Kept in a subfolder: list_clips, batch_rerender and backfill_titles treat every
    # output/*.json as clip metadata, and their non-recursive globs never see .state/.
    state_dir = Path(OUTPUT_DIR) / ".state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "jobs_manifest.json"


def _save_jobs() -> None:
    """Atomically persists JOBS so status survives a server restart. Never raises:
    persistence failure must not fail a clipping job."""
    try:
        with _JOBS_LOCK:
            snapshot = {jid: dict(j) for jid, j in list(JOBS.items())}
            path = _jobs_manifest_path()
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(snapshot, default=str), encoding="utf-8")
            os.replace(tmp, path)
    except Exception as e:
        print(f"[Jobs] Could not persist job manifest: {e}", flush=True)


def _load_jobs() -> None:
    """Restores JOBS from the manifest. Jobs that were mid-flight when the server died
    have no worker thread anymore, so they are marked failed instead of polling forever."""
    try:
        path = _jobs_manifest_path()
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        now = time.time()
        for jid, job in data.items():
            if not isinstance(job, dict):
                continue
            if job.get("status") not in ("completed", "failed"):
                job["status"] = "failed"
                job["error"] = "Server restarted while this job was running. Please start it again."
                job["step"] = f"Error: {job['error']}"
                job["_finished_at"] = now
            JOBS.setdefault(jid, job)
        _prune_jobs()
    except Exception as e:
        print(f"[Jobs] Could not load job manifest: {e}", flush=True)


_load_jobs()

def run_clipping_job(
    job_id: str,
    youtube_url: str,
    top_k: int,
    max_candidates: int,
    subtitle_style: str = "wild_den",
    threshold: float = 7.0,
    max_attempts: int = 4,
    enable_broll: bool = False,
    enable_emojis: bool = False,
    framing_mode: str = "smart_face",
    enable_snappy_cuts: bool = True,
    enable_punch_zooms: bool = False,
    enable_outro: bool = False,
    enable_sponsor_killer: bool = True,
    enable_auto_bleep: bool = False,
    enable_slow_zoom: bool = True,
    enable_bg_music: bool = False,
    bg_music_volume: float = 0.12,
    time_range_start: Optional[Any] = None,
    time_range_end: Optional[Any] = None
):
    try:
        job = JOBS[job_id]
        job["status"] = "running"
        job["progress"] = 5
        job["step"] = "Extracting video details..."

        is_local = is_local_video(youtube_url)
        video_id = extract_video_id(youtube_url)
        clean_url = str(Path(str(youtube_url).strip('"').strip("'")).resolve()) if is_local else f"https://www.youtube.com/watch?v={video_id}"

        job["progress"] = 15
        if is_local:
            job["step"] = f"Transcribing local video with faster-whisper ({Path(clean_url).name})..."
            transcript = get_local_transcript(Path(clean_url), video_id=video_id)
        else:
            job["step"] = "Fetching YouTube captions & transcript..."
            transcript = get_transcript(video_id)

        if not transcript:
            raise RuntimeError("No captions or transcript found for this video. Please ensure the video has English captions or speech enabled.")

        # Video-level context: computed ONCE from the whole transcript, then reused by
        # every clip so titles, niche and hashtags stay consistent across the job.
        video_context = build_video_context(" ".join(t.get("text", "") for t in transcript))

        total_seconds = transcript[-1]["start"] + transcript[-1]["duration"]
        job["progress"] = 25
        job["step"] = f"Analyzing transcript ({len(transcript)} lines, {int(total_seconds // 60)} min runtime)..."

        top_k = max(1, int(top_k))
        max_attempts = max(1, int(max_attempts))
        max_candidates = max(1, int(max_candidates))

        r_start = parse_timestamp_to_seconds(time_range_start)
        r_end = parse_timestamp_to_seconds(time_range_end)

        # Generate candidate windows across transcript (shared constants -> same windows as the CLI)
        candidate_windows = create_windows(
            transcript,
            min_duration=MIN_CLIP_DURATION,
            max_duration=MAX_CLIP_DURATION,
            step=CLIP_WINDOW_STEP,
            range_start=r_start,
            range_end=r_end
        )
        effective_candidates = max(max_candidates or 15, top_k * 4)
        if effective_candidates > 0 and len(candidate_windows) > effective_candidates:
            stride = len(candidate_windows) / effective_candidates
            candidate_windows = [candidate_windows[int(i * stride)] for i in range(effective_candidates)]
        if not candidate_windows:
            raise RuntimeError("Could not find suitable candidate segments in transcript.")

        job["progress"] = 30
        job["step"] = f"Critique Gate: Evaluating candidates against rubric (Hook, Clarity, Pacing, Payoff, Shareability, Subtitles)..."

        api_key = get_jev_api_key()

        def on_critique_progress(clip_num, attempt_num, msg):
            denom = max(1, top_k * max_attempts)
            pct = 30 + int(((clip_num - 1) * max_attempts + attempt_num) / denom * 30)
            job["progress"] = min(60, pct)
            job["step"] = f"Clip #{clip_num} - {msg}"

        shortfalls = []

        def on_shortfall(found, requested):
            shortfalls.append((found, requested))
            print(f"[Critique Gate] WARNING: requested {requested} clips but only {found} "
                  f"non-overlapping candidate windows were available.", flush=True)

        top_clips = critique_gate_search(
            candidate_windows=candidate_windows,
            target_clips=top_k,
            threshold=threshold,
            max_attempts=max_attempts,
            subtitle_style=subtitle_style,
            enable_sponsor_killer=enable_sponsor_killer,
            api_key=api_key,
            progress_callback=on_critique_progress,
            shortfall_callback=on_shortfall
        )

        if not top_clips:
            raise RuntimeError("Critique gate could not find high-impact clips.")

        if len(top_clips) < top_k:
            print(f"[Critique Gate] Only {len(top_clips)}/{top_k} clips cleared the gate.", flush=True)

        # Never silently under-deliver: make the reduced count visible to the user.
        render_note = ""
        if shortfalls or len(top_clips) < top_k:
            render_note = (f" (NOTE: only {len(top_clips)} of {top_k} requested clips were available "
                           f"after the non-overlap and quality gates.)")

        job["step"] = f"Rendering {len(top_clips)} 9:16 vertical Shorts{render_note} with FFmpeg..."
        completed_clips = []

        for rank, clip in enumerate(top_clips, 1):
            pct_render = 60 + int((rank / len(top_clips)) * 35)
            job["progress"] = pct_render
            job["step"] = f"Formatting Clip #{rank} of {len(top_clips)} with {subtitle_style.replace('_', ' ').title()} subtitles..."

            filename_base = f"{video_id}_clip_{rank}_{int(clip['start'])}s"
            output_mp4 = OUTPUT_DIR / f"{filename_base}.mp4"
            meta_json = OUTPUT_DIR / f"{filename_base}.json"

            success = cut_and_format_clip(
                youtube_url=clean_url,
                start_sec=clip["start"],
                end_sec=clip["end"],
                output_path=output_mp4,
                subtitle_style=subtitle_style,
                enable_broll=enable_broll,
                enable_emojis=enable_emojis,
                framing_mode=framing_mode,
                enable_snappy_cuts=enable_snappy_cuts,
                enable_punch_zooms=enable_punch_zooms,
                enable_outro=enable_outro,
                enable_auto_bleep=enable_auto_bleep,
                enable_slow_zoom=enable_slow_zoom,
                enable_bg_music=enable_bg_music,
                bg_music_volume=bg_music_volume
            )

            if success and output_mp4.exists():
                cat_formatted = clip.get("category", "high_value_insight").replace("_", " ").title()
                critique_info = clip.get("critique", {})
                smart_meta = generate_smart_title_and_hashtags(
                    transcript_text=clip["text"],
                    category=clip.get("category", "high_value_insight"),
                    video_context=video_context
                )
                suggested_title = smart_meta.get("suggested_title", f"The brutal truth about {cat_formatted} 🤯")
                suggested_hashtags = smart_meta.get("suggested_hashtags", ["#shorts", "#viral"])
                metadata = {
                    "video_id": video_id,
                    "rank": rank,
                    "filename": output_mp4.name,
                    "start_time": clip["start"],
                    "end_time": clip["end"],
                    "duration": round(clip["duration"], 1),
                    "virality_score": round(clip.get("virality_score", 1.0), 2),
                    "standalone_probability": round(clip.get("standalone_prob", 0.5), 2),
                    "sponsor_probability": round(clip.get("sponsor_prob", 0.0), 2),
                    "category": clip.get("category", "high_value_insight"),
                    "subtitle_style": subtitle_style,
                    "framing_mode": framing_mode,
                    "enable_broll": enable_broll,
                    "enable_emojis": enable_emojis,
                    "enable_snappy_cuts": enable_snappy_cuts,
                    "enable_punch_zooms": enable_punch_zooms,
                    "enable_outro": enable_outro,
                    "enable_sponsor_killer": enable_sponsor_killer,
                    "enable_auto_bleep": enable_auto_bleep,
                    "critique": critique_info,
                    "rubric_scores": critique_info.get("scores", {}),
                    "rubric_average": critique_info.get("average", round(clip.get("virality_score", 1.0) * 3.0, 1)),
                    "passed_gate": critique_info.get("passed", True),
                    "critique_notes": critique_info.get("notes", ""),
                    "transcript_snippet": clip["text"],
                    "suggested_title": suggested_title,
                    "suggested_hashtags": suggested_hashtags,
                    "niche": video_context.get("niche") or smart_meta.get("niche", "general_viral"),
                    "seo_topic": (video_context.get("topics") or [None])[0],
                    # Provenance, explicit. PRESENCE of `seo_topic` used to be read as
                    # "a producer had the full transcript" -- but backfill_titles.py writes
                    # the same key from 30 s snippet unions, so the marker became
                    # self-certifying and stopped meaning anything. State the source.
                    "seo_topic_source": "full_transcript",
                    "candidate_titles": smart_meta.get("candidates", []),
                    "platform_metadata": smart_meta.get("platform_metadata", {})
                }
                with open(meta_json, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=2)
                log_publication(default_log_path(), build_publication_record(
                    source="app",
                    video_id=video_id,
                    filename=output_mp4.name,
                    smart_meta=smart_meta,
                    virality_score=metadata.get("virality_score"),
                    standalone_probability=metadata.get("standalone_probability"),
                    sponsor_probability=metadata.get("sponsor_probability"),
                ))

                completed_clips.append(metadata)

                # Background auto-upload if enabled
                upload_cfg = load_upload_config()
                if upload_cfg.get("auto_upload", {}).get("enabled", False):
                    auto_platforms = upload_cfg.get("auto_upload", {}).get("platforms", ["youtube"])
                    auto_privacy = upload_cfg.get("auto_upload", {}).get("default_privacy", "public")
                    threading.Thread(
                        target=upload_clip_to_platforms,
                        kwargs={
                            "clip_filename": output_mp4.name,
                            "title": suggested_title,
                            "description": smart_meta.get("platform_metadata", {}).get("youtube", {}).get("description", ""),
                            "hashtags": suggested_hashtags,
                            "platforms": auto_platforms,
                            "privacy_status": auto_privacy
                        },
                        daemon=True
                    ).start()

        if not completed_clips:
            raise RuntimeError("Failed to render any output video clips.")

        job["progress"] = 100
        job["status"] = "completed"
        job["step"] = f"Finished! {len(completed_clips)} viral Shorts are ready.{render_note}"
        job["clips"] = completed_clips
        job["_finished_at"] = time.time()
        if shortfalls or len(top_clips) < top_k:
            job["warning"] = (
                f"Requested {top_k} clips but only {len(completed_clips)} were produced. "
                f"Non-overlapping candidate windows were exhausted at the current quality threshold."
            )
        _save_jobs()

    except Exception as e:
        job = JOBS.get(job_id, {})
        job["status"] = "failed"
        job["error"] = str(e)
        job["step"] = f"Error: {str(e)}"
        job["_finished_at"] = time.time()
        _save_jobs()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/subtitle-styles")
def get_subtitle_styles():
    return jsonify({"styles": SUBTITLE_STYLES})

def format_seconds_to_timestamp(sec: float) -> str:
    s = int(round(sec))
    m = s // 60
    rem_s = s % 60
    if m >= 60:
        h = m // 60
        rem_m = m % 60
        return f"{h:02d}:{rem_m:02d}:{rem_s:02d}"
    return f"{m:02d}:{rem_s:02d}"

@app.route("/api/video/suggest-ranges", methods=["POST"])
def suggest_ranges():
    data = request.get_json() or {}
    url = (data.get("url") or data.get("video_url") or "").strip()
    if not url:
        return jsonify({"success": False, "error": "URL is required"}), 400

    try:
        video_id = extract_video_id(url)
    except Exception as e:
        return jsonify({"success": False, "error": f"Invalid URL or file path: {e}"}), 400

    try:
        if is_local_video(url):
            dur = get_video_duration(Path(url.strip('"').strip("'")))
            if dur > 0:
                total_seconds = float(dur)
            else:
                transcript = get_local_transcript(Path(url.strip('"').strip("'")), video_id=video_id)
                total_seconds = float(transcript[-1]["start"] + transcript[-1]["duration"]) if transcript else 0.0
        else:
            transcript = get_transcript(video_id)
            if not transcript:
                return jsonify({"success": False, "error": "No captions available for this video."}), 404
            total_seconds = float(transcript[-1]["start"] + transcript[-1]["duration"])
    except Exception as e:
        return jsonify({"success": False, "error": f"Could not fetch video metadata: {e}"}), 500

    total_int = int(round(total_seconds))
    mins = total_int // 60
    secs = total_int % 60
    formatted_duration = f"{mins}m {secs:02d}s"
    end_ts = format_seconds_to_timestamp(total_seconds)

    suggestions = [
        {
            "label": "Full Video",
            "start": "00:00",
            "end": end_ts,
            "start_sec": 0,
            "end_sec": total_int,
        }
    ]

    if total_seconds >= 120:
        if total_seconds < 420:
            half_sec = int(round(total_seconds / 2.0))
            half_ts = format_seconds_to_timestamp(half_sec)
            suggestions.append({
                "label": "First Half",
                "start": "00:00",
                "end": half_ts,
                "start_sec": 0,
                "end_sec": half_sec,
            })
            suggestions.append({
                "label": "Second Half",
                "start": half_ts,
                "end": end_ts,
                "start_sec": half_sec,
                "end_sec": total_int,
            })
        else:
            intro_sec = min(300, int(round(total_seconds * 0.25)))
            climax_sec = max(intro_sec + 60, int(round(total_seconds * 0.65)))
            
            intro_ts = format_seconds_to_timestamp(intro_sec)
            climax_ts = format_seconds_to_timestamp(climax_sec)

            suggestions.append({
                "label": "Opening / Intro",
                "start": "00:00",
                "end": intro_ts,
                "start_sec": 0,
                "end_sec": intro_sec,
            })
            suggestions.append({
                "label": "Middle Section",
                "start": intro_ts,
                "end": climax_ts,
                "start_sec": intro_sec,
                "end_sec": climax_sec,
            })
            suggestions.append({
                "label": "Climax / Ending",
                "start": climax_ts,
                "end": end_ts,
                "start_sec": climax_sec,
                "end_sec": total_int,
            })

    return jsonify({
        "success": True,
        "total_seconds": round(total_seconds, 1),
        "formatted_duration": formatted_duration,
        "suggestions": suggestions
    })

@app.route("/api/generate", methods=["POST"])
@app.route("/api/start-autocut", methods=["POST"])
def generate():
    data = request.get_json() or {}
    url = (data.get("url") or data.get("video_url") or data.get("link") or "").strip()
    try:
        top_k = max(1, int(data.get("top_k", 3)))
    except (ValueError, TypeError):
        top_k = 3

    try:
        candidates = max(1, int(data.get("candidates", 15)))
    except (ValueError, TypeError):
        candidates = 15

    subtitle_style = data.get("subtitle_style", "wild_den")
    try:
        raw_thresh = data.get("threshold", 7.0)
        threshold = float(raw_thresh) if raw_thresh is not None else 7.0
    except (ValueError, TypeError):
        threshold = 7.0

    try:
        max_attempts = max(1, int(data.get("max_attempts", 4)))
    except (ValueError, TypeError):
        max_attempts = 4
    enable_broll = bool(data.get("enable_broll", False))
    enable_emojis = bool(data.get("enable_emojis", False))
    framing_mode = str(data.get("framing_mode", "smart_face"))
    enable_snappy_cuts = bool(data.get("enable_snappy_cuts", True))
    enable_punch_zooms = bool(data.get("enable_punch_zooms", False))
    enable_outro = bool(data.get("enable_outro", False))
    enable_sponsor_killer = bool(data.get("enable_sponsor_killer", True))
    enable_auto_bleep = bool(data.get("enable_auto_bleep", False))
    enable_slow_zoom = bool(data.get("enable_slow_zoom", True))
    enable_bg_music = bool(data.get("enable_bg_music", False))
    try:
        bg_music_volume = float(data.get("bg_music_volume", 0.12))
    except (ValueError, TypeError):
        bg_music_volume = 0.12

    time_range_start = data.get("time_range_start")
    time_range_end = data.get("time_range_end")

    if not url:
        return jsonify({"error": "Please provide a valid YouTube video URL or local video file path."}), 400

    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "step": "Queued and starting up with Critique Gate...",
        "clips": [],
        "error": None,
        "_created_at": time.time(),
        "_finished_at": None
    }
    _prune_jobs()
    _save_jobs()

    t = threading.Thread(
        target=run_clipping_job,
        args=(job_id, url, top_k, candidates, subtitle_style, threshold, max_attempts, enable_broll, enable_emojis, framing_mode, enable_snappy_cuts, enable_punch_zooms, enable_outro, enable_sponsor_killer, enable_auto_bleep, enable_slow_zoom, enable_bg_music, bg_music_volume),
        kwargs={"time_range_start": time_range_start, "time_range_end": time_range_end},
        daemon=True
    )
    t.start()

    return jsonify({"job_id": job_id, "status": "queued"})

@app.route("/api/status/<job_id>")
def status(job_id):
    _prune_jobs()
    job = JOBS.get(job_id)
    if not job:
        # Terminal payload (not a bare 404) so the browser poller stops instead of hanging.
        return jsonify({
            "job_id": job_id,
            "status": "failed",
            "progress": 0,
            "step": "Error: Job expired or not found",
            "error": "Job expired or not found",
            "clips": []
        }), 404
    # Strip internal bookkeeping fields before serialising to the client.
    return jsonify({k: v for k, v in job.items() if not k.startswith("_")})

@app.route("/api/clips")
def list_clips():
    clips = []
    if OUTPUT_DIR.exists():
        for json_file in OUTPUT_DIR.glob("*.json"):
            mp4_file = OUTPUT_DIR / f"{json_file.stem}.mp4"
            if mp4_file.exists():
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        data["filename"] = mp4_file.name
                        data["size_mb"] = round(mp4_file.stat().st_size / (1024 * 1024), 2)
                        data["mtime"] = mp4_file.stat().st_mtime
                        clips.append(data)
                except Exception:
                    pass

    # Sort newest first
    clips.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    return jsonify({"clips": clips})

@app.route("/output/<path:filename>")
def serve_output(filename):
    return send_from_directory(OUTPUT_DIR, filename)

@app.route("/api/open-folder", methods=["POST"])
def open_folder():
    try:
        if sys.platform == "win32":
            os.startfile(str(OUTPUT_DIR))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(OUTPUT_DIR)])
        else:
            subprocess.run(["xdg-open", str(OUTPUT_DIR)])
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def open_native_file_dialog() -> str:
    """Opens native Windows file dialog to select an offline video file."""
    selected_path = ""
    # 1. On Windows, use PowerShell System.Windows.Forms for guaranteed foreground focus
    if os.name == "nt":
        try:
            ps_script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$dialog = New-Object System.Windows.Forms.OpenFileDialog; "
                "$dialog.Title = 'Select Offline Video File to Clip'; "
                "$dialog.Filter = 'Video Files (*.mp4;*.mkv;*.mov;*.webm;*.avi;*.flv;*.ts;*.m4v)|*.mp4;*.mkv;*.mov;*.webm;*.avi;*.flv;*.ts;*.m4v|All Files (*.*)|*.*'; "
                "$dialog.Multiselect = $false; "
                "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { "
                "    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                "    Write-Output $dialog.FileName "
                "}"
            )
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script]
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            if res.returncode == 0 and res.stdout.strip():
                selected_path = res.stdout.strip()
        except Exception as e:
            print(f"[PowerShell Browse Error] {e}", flush=True)

    # 2. Fallback to Tkinter if PowerShell didn't return a path
    if not selected_path:
        def _open():
            nonlocal selected_path
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.wm_attributes("-topmost", 1)
                selected_path = filedialog.askopenfilename(
                    parent=root,
                    title="Select Offline Video File to Clip",
                    filetypes=[
                        ("Video Files", "*.mp4;*.mkv;*.mov;*.webm;*.avi;*.flv;*.ts;*.m4v"),
                        ("All Files", "*.*")
                    ]
                )
                root.destroy()
            except Exception as e:
                print(f"[Browse File Error] {e}", flush=True)

        t = threading.Thread(target=_open)
        t.start()
        t.join(timeout=60)

    return selected_path

@app.route("/api/browse-local-file", methods=["POST", "GET"])
def browse_local_file():
    path = open_native_file_dialog()
    if path and os.path.exists(path):
        return jsonify({"success": True, "file_path": path})
    return jsonify({"success": False, "file_path": ""})

# ==========================================
# NICHE FINDER & OUTLIER EXPLORER ENDPOINTS
# ==========================================

@app.route("/api/niche/search", methods=["POST"])
def api_niche_search():
    data = request.get_json(silent=True) or {}
    query = str(data.get("query", "")).strip()[:200]
    if not query:
        return jsonify({"success": False, "error": "Search query is required"}), 400

    try:
        channels = search_niche_channels(query, max_entries=20)
        return jsonify({
            "success": True,
            "query": query,
            "total_channels": len(channels),
            "channels": channels
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/niche/channel", methods=["POST"])
def api_niche_channel():
    data = request.get_json(silent=True) or {}
    channel_input = str(data.get("channel_input", "")).strip()

    if not channel_input:
        return jsonify({"success": False, "error": "Channel handle or URL is required"}), 400

    try:
        max_videos = int(data.get("max_videos", 30))
        max_videos = max(5, min(max_videos, 100))
    except (TypeError, ValueError):
        max_videos = 30

    try:
        raw_data = get_channel_details(channel_input, max_videos=max_videos)
        analyzed = analyze_channel_outliers(raw_data)

        # Disclose when the requested handle/ID could not be resolved and the data actually
        # belongs to a search-resolved channel, so the user is never shown one channel's
        # numbers under another channel's request.
        warnings = []
        requested = raw_data.get("requested_input", channel_input)
        if raw_data.get("resolved_by_search"):
            warnings.append(
                f"'{requested}' could not be resolved as a handle or channel ID. "
                f"Results shown are for '{analyzed.get('channel_name') or raw_data.get('channel_name')}' "
                f"({raw_data.get('channel_url') or 'unknown URL'})."
            )

        payload = {
            "success": True,
            "data": analyzed
        }
        if warnings:
            payload["warnings"] = warnings
        return jsonify(payload)
    except ValueError as ve:
        return jsonify({"success": False, "error": str(ve)}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/niche/video-hook", methods=["POST"])
def api_niche_video_hook():
    data = request.get_json(silent=True) or {}
    raw_input = str(data.get("video_id", "")).strip()

    if not raw_input:
        return jsonify({"success": False, "error": "Video URL or ID is required"}), 400

    try:
        clean_video_id = extract_video_id(raw_input)
        raw_transcript = get_video_transcript(clean_video_id)
        analysis = analyze_hook_and_pacing(raw_transcript)
        return jsonify({
            "success": True,
            "video_id": clean_video_id,
            "transcript": raw_transcript,
            "analysis": analysis
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/niche/ideas", methods=["POST"])
def api_niche_ideas():
    data = request.get_json(silent=True) or {}
    channel_name = str(data.get("channel_name", "YouTube Channel")).strip()[:100]
    niche = str(data.get("niche", "Topic")).strip()[:200]
    outlier_titles = data.get("outlier_titles") if isinstance(data.get("outlier_titles"), list) else []
    hook_framework = str(data.get("hook_framework", "Curiosity Gap")).strip()
    api_key = str(data.get("api_key", "")).strip()

    try:
        pacing_wpm = int(data.get("pacing_wpm", 150))
        pacing_wpm = max(50, min(pacing_wpm, 300))
    except (TypeError, ValueError):
        pacing_wpm = 150

    try:
        ideas = generate_viral_ideas(
            channel_name=channel_name,
            niche=niche,
            outlier_titles=outlier_titles,
            hook_framework=hook_framework,
            pacing_wpm=pacing_wpm,
            api_key=api_key
        )
        return jsonify({
            "success": True,
            "ideas": ideas
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# =========================================================================
# MULTI-PLATFORM AUTO-UPLOAD API ENDPOINTS
# =========================================================================

@app.route("/api/upload/config", methods=["GET"])
def api_get_upload_config():
    cfg = load_upload_config()
    ayr_key = cfg.get("ayrshare", {}).get("api_key", "")
    masked_key = (ayr_key[:4] + "*" * (len(ayr_key) - 8) + ayr_key[-4:]) if len(ayr_key) >= 8 else ("*" * len(ayr_key))
    
    return jsonify({
        "success": True,
        "config": {
            "youtube": {
                "is_authenticated": cfg.get("youtube", {}).get("is_authenticated", False),
                "channel_title": cfg.get("youtube", {}).get("channel_title", ""),
                "channel_id": cfg.get("youtube", {}).get("channel_id", "")
            },
            "ayrshare": {
                "is_configured": bool(ayr_key),
                "masked_key": masked_key
            },
            "auto_upload": cfg.get("auto_upload", {
                "enabled": False,
                "platforms": ["youtube"],
                "default_privacy": "public"
            })
        }
    })

@app.route("/api/upload/config", methods=["POST"])
def api_save_upload_config():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "Request body must be a JSON object."}), 400
    cfg = load_upload_config()

    if "ayrshare_key" in data:
        raw_key = data["ayrshare_key"]
        if not isinstance(raw_key, str):
            # A JSON null / number is a client bug. It must NOT be treated as "clear the
            # key", otherwise any form serialiser that emits null for a blank optional field
            # would silently wipe the stored credential.
            return jsonify({"success": False,
                            "error": "'ayrshare_key' must be a string. "
                                    "Send \"\" to clear the stored key."}), 400
        key_str = raw_key.strip()

        if key_str:
            cfg["ayrshare"]["api_key"] = key_str
            cfg["ayrshare"]["is_configured"] = True
        else:
            # Explicit empty value clears the credential so it can be revoked/rotated.
            cfg["ayrshare"]["api_key"] = ""
            cfg["ayrshare"]["is_configured"] = False

    if "auto_upload" in data:
        auto = data["auto_upload"]
        if not isinstance(auto, dict):
            return jsonify({"success": False, "error": "'auto_upload' must be a JSON object."}), 400
        platforms = auto.get("platforms", ["youtube"])
        if isinstance(platforms, str):
            platforms = [platforms]
        if not isinstance(platforms, list):
            return jsonify({"success": False, "error": "'auto_upload.platforms' must be a list of strings."}), 400
        privacy = auto.get("default_privacy", "public")
        if not isinstance(privacy, str) or not privacy.strip():
            return jsonify({"success": False, "error": "'auto_upload.default_privacy' must be a non-empty string."}), 400
        cfg["auto_upload"]["enabled"] = bool(auto.get("enabled", False))
        cfg["auto_upload"]["platforms"] = [str(p) for p in platforms]
        cfg["auto_upload"]["default_privacy"] = privacy.strip()

    if not save_upload_config(cfg):
        return jsonify({"success": False, "error": "Failed to write upload_config.json"}), 500
    return jsonify({"success": True})

@app.route("/api/upload/youtube-auth-start", methods=["POST"])
def api_youtube_auth_start():
    """
    Starts the loopback OAuth flow on a background thread.

    `flow.run_local_server()` blocks until the user completes the browser round-trip, so it must
    never run inside a Flask request handler. The client polls /api/upload/youtube-auth-status/<id>.
    """
    auth_id = uuid.uuid4().hex
    AUTH_JOBS[auth_id] = {"status": "pending", "result": None, "error": None}

    def _worker():
        try:
            AUTH_JOBS[auth_id]["result"] = start_youtube_local_auth(open_browser=True)
        except Exception as e:  # start_youtube_local_auth already traps, this is belt-and-braces
            AUTH_JOBS[auth_id]["result"] = {"success": False, "error": str(e)}
        finally:
            AUTH_JOBS[auth_id]["status"] = "done"

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({"success": True, "status": "pending", "auth_id": auth_id})

@app.route("/api/upload/youtube-auth-status/<auth_id>", methods=["GET"])
def api_youtube_auth_status(auth_id):
    job = AUTH_JOBS.get(auth_id)
    if not job:
        return jsonify({"success": False, "error": "Unknown auth request."}), 404
    if job["status"] == "pending":
        return jsonify({"success": True, "status": "pending"})
    res = job["result"] or {"success": False, "error": "Authentication did not complete."}
    AUTH_JOBS.pop(auth_id, None)
    return jsonify(res)

@app.route("/api/upload/youtube-auth-url", methods=["GET"])
def api_youtube_auth_url():
    res = get_youtube_auth_url()
    return jsonify(res)

@app.route("/api/upload/youtube-auth-code", methods=["POST"])
def api_youtube_auth_code():
    data = request.get_json(silent=True) or {}
    code = data.get("code", "").strip()
    if not code:
        return jsonify({"success": False, "error": "Authorization code is required"}), 400
    res = exchange_youtube_code(code)
    return jsonify(res)

@app.route("/api/upload/clip", methods=["POST"])
def api_upload_clip():
    data = request.get_json(silent=True) or {}
    filename = data.get("filename", "").strip()
    title = data.get("title", "").strip()
    description = data.get("description", "").strip()
    hashtags = data.get("hashtags", [])
    platforms = data.get("platforms", ["youtube"])
    privacy = data.get("privacy", "public")
    
    if not filename:
        return jsonify({"success": False, "error": "Filename is required"}), 400
        
    res = upload_clip_to_platforms(
        clip_filename=filename,
        title=title,
        description=description,
        hashtags=hashtags,
        platforms=platforms,
        privacy_status=privacy
    )
    return jsonify(res)


HF_JOBS = {}

@app.route("/api/hyperframes/styles", methods=["GET"])
def api_hf_styles():
    return jsonify({"success": True, "styles": hyperframes_editor.STYLES})

@app.route("/api/hyperframes/preview", methods=["POST"])
def api_hf_preview():
    data = request.get_json(silent=True) or {}
    filename = data.get("filename", "").strip()
    style = data.get("style", "viral_pop")
    badge = data.get("badge", "")
    zoom = data.get("zoom", "dynamic")
    
    if not filename:
        return jsonify({"success": False, "error": "Filename required"}), 400
        
    clip_path = OUTPUT_DIR / filename
    if not clip_path.exists():
        return jsonify({"success": False, "error": f"File not found: {filename}"}), 404
        
    try:
        res = hyperframes_editor.setup_and_render_preview(
            input_video_path=clip_path,
            style_key=style,
            badge_text=badge,
            zoom_intensity=zoom
        )
        sheet_path = Path(res["contact_sheet"])
        dest_sheet_name = f"{clip_path.stem}_hf_preview.jpg"
        dest_sheet = OUTPUT_DIR / dest_sheet_name
        shutil.copy2(sheet_path, dest_sheet)
        
        return jsonify({
            "success": True,
            "preview_url": f"/output/{dest_sheet_name}",
            "duration": res["duration"],
            "word_count": res["word_count"],
            "zoom_count": res["zoom_count"]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/hyperframes/render", methods=["POST"])
def api_hf_render():
    data = request.get_json(silent=True) or {}
    filename = data.get("filename", "").strip()
    style = data.get("style", "viral_pop")
    badge = data.get("badge", "")
    zoom = data.get("zoom", "dynamic")
    
    if not filename:
        return jsonify({"success": False, "error": "Filename required"}), 400
        
    clip_path = OUTPUT_DIR / filename
    if not clip_path.exists():
        return jsonify({"success": False, "error": f"File not found: {filename}"}), 404
        
    job_id = f"hf_{uuid.uuid4().hex[:8]}"
    HF_JOBS[job_id] = {
        "status": "rendering",
        "progress": 10,
        "step": "Setting up Hyperframes composition...",
        "result": None,
        "error": None
    }
    
    def _run_hf_render():
        try:
            HF_JOBS[job_id]["step"] = "Preparing composition and assets..."
            HF_JOBS[job_id]["progress"] = 30
            hyperframes_editor.setup_and_render_preview(
                input_video_path=clip_path,
                style_key=style,
                badge_text=badge,
                zoom_intensity=zoom
            )
            
            HF_JOBS[job_id]["step"] = "Rendering master 1080x1920 MP4 with Hyperframes..."
            HF_JOBS[job_id]["progress"] = 60
            prefix = clip_path.stem
            out = hyperframes_editor.render_final_deliverables(prefix, output_dir=OUTPUT_DIR)
            
            HF_JOBS[job_id]["progress"] = 100
            HF_JOBS[job_id]["status"] = "completed"
            HF_JOBS[job_id]["step"] = "Done!"
            HF_JOBS[job_id]["result"] = {
                "master_filename": Path(out["master_path"]).name,
                "preview_filename": Path(out["preview_path"]).name,
                "master_url": f"/output/{Path(out['master_path']).name}",
                "preview_url": f"/output/{Path(out['preview_path']).name}",
                "master_size": out["master_size"],
                "preview_size": out["preview_size"]
            }
        except Exception as err:
            HF_JOBS[job_id]["status"] = "failed"
            HF_JOBS[job_id]["error"] = str(err)
            
    threading.Thread(target=_run_hf_render, daemon=True).start()
    return jsonify({"success": True, "job_id": job_id})

@app.route("/api/hyperframes/status/<job_id>", methods=["GET"])
def api_hf_status(job_id):
    job = HF_JOBS.get(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found"}), 404
    return jsonify({"success": True, "job": job})


if __name__ == "__main__":
    import webbrowser
    port = 5000
    host = "127.0.0.1"
    print(f"Starting Autonomous Video Clipper Web GUI on http://{host}:{port}")
    # Open default web browser automatically
    threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    app.run(host=host, port=port, debug=False)
