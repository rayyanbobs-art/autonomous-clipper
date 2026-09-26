import os
import sys
import json
import uuid
import threading
import subprocess
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory

from config import OUTPUT_DIR, get_jev_api_key
from scorer import create_windows, score_chunk_with_jev, filter_non_overlapping_clips, critique_gate_search
from video_cutter import cut_and_format_clip
from clipper import extract_video_id, get_transcript
from subtitles import SUBTITLE_STYLES
from niche_scraper import search_niche_channels, get_channel_details, get_video_transcript
from niche_analyzer import analyze_channel_outliers, analyze_hook_and_pacing
from niche_generator import generate_viral_ideas
# Safeguard UTF-8 encoding on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from title_tag_engine import generate_smart_title_and_hashtags
from uploader import (
    load_upload_config, save_upload_config, get_youtube_auth_url,
    exchange_youtube_code, start_youtube_local_auth, upload_clip_to_platforms
)

app = Flask(__name__)

# In-memory job tracker
JOBS = {}

def run_clipping_job(
    job_id: str,
    youtube_url: str,
    top_k: int,
    max_candidates: int,
    subtitle_style: str = "bold_pop",
    threshold: float = 7.0,
    max_attempts: int = 4,
    enable_broll: bool = True,
    enable_emojis: bool = True,
    framing_mode: str = "blurred",
    enable_snappy_cuts: bool = True,
    enable_punch_zooms: bool = True,
    enable_outro: bool = True,
    enable_sponsor_killer: bool = True,
    enable_auto_bleep: bool = False
):
    try:
        job = JOBS[job_id]
        job["status"] = "running"
        job["progress"] = 5
        job["step"] = "Extracting video details..."

        video_id = extract_video_id(youtube_url)
        clean_url = f"https://www.youtube.com/watch?v={video_id}"

        job["progress"] = 15
        job["step"] = "Fetching YouTube captions & transcript..."
        transcript = get_transcript(video_id)
        if not transcript:
            raise RuntimeError("No captions or transcript found for this video. Please ensure the video has English captions enabled.")

        total_seconds = transcript[-1]["start"] + transcript[-1]["duration"]
        job["progress"] = 25
        job["step"] = f"Analyzing transcript ({len(transcript)} lines, {int(total_seconds // 60)} min runtime)..."

        top_k = max(1, int(top_k))
        max_attempts = max(1, int(max_attempts))
        max_candidates = max(1, int(max_candidates))

        # Generate candidate windows across transcript
        candidate_windows = create_windows(transcript, min_duration=28.0, max_duration=55.0, step=25.0)
        if max_candidates and max_candidates > 0 and len(candidate_windows) > max_candidates:
            stride = len(candidate_windows) / max_candidates
            candidate_windows = [candidate_windows[int(i * stride)] for i in range(max_candidates)]
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

        top_clips = critique_gate_search(
            candidate_windows=candidate_windows,
            target_clips=top_k,
            threshold=threshold,
            max_attempts=max_attempts,
            subtitle_style=subtitle_style,
            enable_sponsor_killer=enable_sponsor_killer,
            api_key=api_key,
            progress_callback=on_critique_progress
        )

        if not top_clips:
            raise RuntimeError("Critique gate could not find high-impact clips.")

        job["step"] = f"Rendering {len(top_clips)} 9:16 vertical Shorts with FFmpeg..."
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
                enable_auto_bleep=enable_auto_bleep
            )

            if success and output_mp4.exists():
                cat_formatted = clip.get("category", "high_value_insight").replace("_", " ").title()
                critique_info = clip.get("critique", {})
                smart_meta = generate_smart_title_and_hashtags(
                    transcript_text=clip["text"],
                    category=clip.get("category", "high_value_insight")
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
                    "niche": smart_meta.get("niche", "general_viral"),
                    "candidate_titles": smart_meta.get("candidates", []),
                    "platform_metadata": smart_meta.get("platform_metadata", {})
                }
                with open(meta_json, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=2)

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
        job["step"] = f"Finished! {len(completed_clips)} viral Shorts are ready."
        job["clips"] = completed_clips

    except Exception as e:
        job = JOBS.get(job_id, {})
        job["status"] = "failed"
        job["error"] = str(e)
        job["step"] = f"Error: {str(e)}"

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/subtitle-styles")
def get_subtitle_styles():
    return jsonify({"styles": SUBTITLE_STYLES})

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

    subtitle_style = data.get("subtitle_style", "bold_pop")
    try:
        raw_thresh = data.get("threshold", 7.0)
        threshold = float(raw_thresh) if raw_thresh is not None else 7.0
    except (ValueError, TypeError):
        threshold = 7.0

    try:
        max_attempts = max(1, int(data.get("max_attempts", 4)))
    except (ValueError, TypeError):
        max_attempts = 4
    enable_broll = bool(data.get("enable_broll", True))
    enable_emojis = bool(data.get("enable_emojis", True))
    framing_mode = str(data.get("framing_mode", "blurred"))
    enable_snappy_cuts = bool(data.get("enable_snappy_cuts", True))
    enable_punch_zooms = bool(data.get("enable_punch_zooms", True))
    enable_outro = bool(data.get("enable_outro", True))
    enable_sponsor_killer = bool(data.get("enable_sponsor_killer", True))
    enable_auto_bleep = bool(data.get("enable_auto_bleep", False))

    if not url:
        return jsonify({"error": "Please provide a valid YouTube video URL."}), 400

    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "step": "Queued and starting up with Critique Gate...",
        "clips": [],
        "error": None
    }

    t = threading.Thread(
        target=run_clipping_job,
        args=(job_id, url, top_k, candidates, subtitle_style, threshold, max_attempts, enable_broll, enable_emojis, framing_mode, enable_snappy_cuts, enable_punch_zooms, enable_outro, enable_sponsor_killer, enable_auto_bleep),
        daemon=True
    )
    t.start()

    return jsonify({"job_id": job_id, "status": "queued"})

@app.route("/api/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)

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
        return jsonify({
            "success": True,
            "data": analyzed
        })
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
    data = request.get_json(silent=True) or {}
    cfg = load_upload_config()
    
    if "ayrshare_key" in data and data["ayrshare_key"].strip():
        cfg["ayrshare"]["api_key"] = data["ayrshare_key"].strip()
        cfg["ayrshare"]["is_configured"] = True
        
    if "auto_upload" in data:
        cfg["auto_upload"]["enabled"] = bool(data["auto_upload"].get("enabled", False))
        cfg["auto_upload"]["platforms"] = data["auto_upload"].get("platforms", ["youtube"])
        cfg["auto_upload"]["default_privacy"] = data["auto_upload"].get("default_privacy", "public")
        
    ok = save_upload_config(cfg)
    return jsonify({"success": ok})

@app.route("/api/upload/youtube-auth-start", methods=["POST"])
def api_youtube_auth_start():
    res = start_youtube_local_auth(open_browser=True)
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


if __name__ == "__main__":
    import webbrowser
    port = 5000
    host = "127.0.0.1"
    print(f"Starting Autonomous Video Clipper Web GUI on http://{host}:{port}")
    # Open default web browser automatically
    threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    app.run(host=host, port=port, debug=False)
