"""
Daily Autonomous YouTube Shorts Generation & Scheduled Upload Runner.

This script executes the complete daily automated workflow:
1. Picks the next unclipped popular video from Outdoor Boys using channel_video_picker.py (zero repetition).
2. Submits the video to Auto Clipper (http://127.0.0.1:5000) with the user's exact required parameters:
   - Up to 10 clips
   - No subtitles
   - Smart face framing
   - Slow-mo in and drift audio bed (slow zoom) enabled
   - Ambient adventure bed disabled
   - All other toggles (broll, emojis, snappy cuts, punch zooms, outro, sponsor killer, auto bleep) disabled
3. Waits for generation and retrieves the top-scoring clip (Rank 1).
4. Enhances the top clip with Hyperframes:
   - Style: survival_arctic (or viral_pop) matching tone
   - Zoom: dynamic (recommended)
   - Badge: blank
   - Renders master 1080x1920 MP4
5. Uploads the master video to YouTube scheduled for 23:30 (Asia/Karachi).
6. Permanently records the video in processed_channel_videos.json so it is NEVER repeated.
"""

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import time
import json
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from channel_video_picker import get_next_popular_video, mark_video_as_processed

API_BASE = "http://127.0.0.1:5000"


def ensure_server_running():
    """Checks if the local Flask server is running; starts it if needed."""
    try:
        r = requests.get(f"{API_BASE}/api/upload/config", timeout=3)
        if r.status_code == 200:
            print("[Server] Auto Clipper backend is online at http://127.0.0.1:5000", flush=True)
            return True
    except Exception:
        pass

    import subprocess
    print("[Server] Starting Auto Clipper server in background...", flush=True)
    subprocess.Popen([sys.executable, "app.py"], cwd=str(BASE_DIR))
    for _ in range(15):
        time.sleep(2)
        try:
            r = requests.get(f"{API_BASE}/api/upload/config", timeout=2)
            if r.status_code == 200:
                print("[Server] Auto Clipper server successfully started!", flush=True)
                return True
        except Exception:
            continue
    raise RuntimeError("Failed to connect to Auto Clipper server on port 5000.")


def calculate_scheduled_iso_time(target_hour=23, target_minute=30, tz_offset_hours=5) -> str:
    """
    Calculates the ISO 8601 UTC timestamp for 23:30 Asia/Karachi (UTC+5).
    """
    now_utc = datetime.now(timezone.utc)
    user_local = now_utc + timedelta(hours=tz_offset_hours)
    
    # Target time today in local timezone
    target_local = user_local.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    if target_local <= user_local + timedelta(minutes=10):
        # If already past 23:20 local time, schedule for tomorrow's 23:30
        target_local += timedelta(days=1)
        
    # Convert back to UTC for YouTube Data API publishAt
    target_utc = target_local - timedelta(hours=tz_offset_hours)
    iso_str = target_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    print(f"[Scheduler] Target upload publish time: {target_local.strftime('%Y-%m-%d %H:%M:%S')} (PKT) -> {iso_str} (UTC)", flush=True)
    return iso_str


def run():
    print("=" * 70, flush=True)
    print("  DAILY AUTONOMOUS YOUTUBE SHORTS AUTOMATION STARTING", flush=True)
    print("=" * 70, flush=True)

    ensure_server_running()

    # Step 1: Select next unclipped popular video
    print("\n--- [Step 1: Non-Repetitive Channel Video Selection] ---", flush=True)
    selected = get_next_popular_video()
    if not selected:
        raise RuntimeError("No unclipped popular videos available from channel.")

    video_url = selected["url"]
    video_id = selected["id"]
    video_title = selected["title"]
    print(f"Selected Video: {video_title} ({video_url}) [Popularity Rank #{selected.get('rank_by_popularity')}]", flush=True)

    # Step 2: Configure and trigger Auto Clipper
    print("\n--- [Step 2: Checking / Triggering Auto Clipper with Required Settings] ---", flush=True)
    existing_clips_res = requests.get(f"{API_BASE}/api/clips", timeout=60).json()
    all_existing = existing_clips_res.get("clips", [])
    matching_clips = [c for c in all_existing if c.get("video_id") == video_id or c.get("filename", "").startswith(video_id)]

    if matching_clips:
        print(f"[Auto Clipper] Found {len(matching_clips)} ready clips for {video_id}. Proceeding directly to selection!", flush=True)
    else:
        gen_payload = {
            "url": video_url,
            "top_k": 10,
            "candidates": 20,
            "subtitle_style": "none",
            "framing_mode": "smart_face",
            "enable_broll": False,
            "enable_emojis": False,
            "enable_slow_zoom": True,          # Slow-mo in and drift (Checked)
            "enable_bg_music": False,          # Ambient adventure bed (Unchecked)
            "enable_snappy_cuts": False,       # Unchecked
            "enable_punch_zooms": False,       # Unchecked
            "enable_outro": False,             # Unchecked
            "enable_sponsor_killer": False,    # Unchecked
            "enable_auto_bleep": False         # Unchecked
        }

        res = requests.post(f"{API_BASE}/api/generate", json=gen_payload, timeout=30)
        gen_data = res.json()
        if not gen_data.get("job_id"):
            raise RuntimeError(f"Auto Clipper failed to start: {gen_data.get('error', res.text)}")

        job_id = gen_data["job_id"]
        print(f"Job started successfully with ID: {job_id}. Polling progress...", flush=True)

        # Poll generation status
        consecutive_errors = 0
        while True:
            time.sleep(10)
            try:
                res = requests.get(f"{API_BASE}/api/status/{job_id}", timeout=60)
                if res.status_code == 404:
                    raise RuntimeError(f"Auto Clipper job {job_id} expired or was not found on server.")
                job = res.json()
                consecutive_errors = 0
            except requests.exceptions.RequestException as e:
                consecutive_errors += 1
                print(f"  [Auto Clipper Polling] Server busy encoding, retrying ({consecutive_errors}/10)...", flush=True)
                if consecutive_errors > 10:
                    raise RuntimeError(f"Server connection lost during clipping: {e}")
                continue

            status = job.get("status")
            progress = job.get("progress", 0)
            step = job.get("step", "")
            print(f"  [Progress {progress}%] {step}", flush=True)

            if status == "completed":
                print("[Auto Clipper] Clips generation complete!", flush=True)
                break
            elif status == "failed":
                raise RuntimeError(f"Auto Clipper job failed: {job.get('error') or job.get('step')}")

    # Step 3: Identify highest scoring clip for this video
    print("\n--- [Step 3: Selecting Top-Scoring Clip] ---", flush=True)
    clips_res = requests.get(f"{API_BASE}/api/clips", timeout=60).json()
    all_clips = clips_res.get("clips", [])
    if not all_clips:
        raise RuntimeError("No clips found in output.")

    # Prioritize clips produced from this specific video
    matching_clips = [c for c in all_clips if c.get("video_id") == video_id or c.get("filename", "").startswith(video_id)]
    target_pool = matching_clips if matching_clips else all_clips

    sorted_clips = sorted(
        target_pool,
        key=lambda c: (c.get("virality_score", 0), c.get("rubric_average", 0)),
        reverse=True
    )
    best_clip = sorted_clips[0]
    clip_filename = best_clip["filename"]
    clip_title = best_clip.get("suggested_title") or f"{video_title} #shorts"
    clip_hashtags = best_clip.get("suggested_hashtags") or ["#Shorts", "#outdoors", "#survival"]
    clip_niche = best_clip.get("niche", "outdoors_survival")

    print(f"Top-Scored Clip: {clip_filename} (Virality Score: {best_clip.get('virality_score')}, Rubric: {best_clip.get('rubric_average')})", flush=True)
    print(f"Suggested Title: {clip_title}", flush=True)

    # Step 4: Enhance with Hyperframes
    print("\n--- [Step 4: Enhancing with Hyperframes Universal Studio] ---", flush=True)
    hf_style = "survival_arctic" if "survival" in clip_niche or "outdoor" in clip_niche else "viral_pop"
    hf_payload = {
        "filename": clip_filename,
        "style": hf_style,
        "badge": "",               # Top hook and status badge left blank
        "zoom": "dynamic"          # Recommended camera punch & zoom dynamics
    }

    hf_render_res = requests.post(f"{API_BASE}/api/hyperframes/render", json=hf_payload, timeout=60).json()
    if not hf_render_res.get("success"):
        raise RuntimeError(f"Hyperframes render failed to initiate: {hf_render_res.get('error')}")

    hf_job_id = hf_render_res["job_id"]
    print(f"Hyperframes render started (Job: {hf_job_id}). Waiting for master 1080x1920 MP4...", flush=True)

    hf_errors = 0
    while True:
        time.sleep(10)
        try:
            hf_status_res = requests.get(f"{API_BASE}/api/hyperframes/status/{hf_job_id}", timeout=60).json()
            hf_job = hf_status_res.get("job", {})
            hf_errors = 0
        except requests.exceptions.RequestException as e:
            hf_errors += 1
            print(f"  [Hyperframes Polling] Server busy rendering, retrying ({hf_errors}/10)...", flush=True)
            if hf_errors > 10:
                raise RuntimeError(f"Server connection lost during Hyperframes rendering: {e}")
            continue

        hf_status = hf_job.get("status")
        hf_progress = hf_job.get("progress", 0)
        hf_step = hf_job.get("step", "")
        print(f"  [Hyperframes {hf_progress}%] {hf_step}", flush=True)

        if hf_status == "completed":
            master_filename = hf_job.get("result", {}).get("master_filename")
            print(f"[Hyperframes] Master deliverable rendered: {master_filename}", flush=True)
            break
        elif hf_status == "failed":
            raise RuntimeError(f"Hyperframes render failed: {hf_job.get('error')}")

    # Step 5: Upload to YouTube with Scheduled Release
    print("\n--- [Step 5: Uploading & Scheduling on YouTube] ---", flush=True)
    publish_iso = calculate_scheduled_iso_time(target_hour=23, target_minute=30, tz_offset_hours=5)

    clean_upload_title = clip_title if "#shorts" in clip_title.lower() else f"{clip_title} #shorts"

    upload_payload = {
        "filename": master_filename,
        "title": clean_upload_title,
        "description": f"{clean_upload_title}\n\n#Shorts #outdoorboys #viral",
        "hashtags": clip_hashtags,
        "platforms": ["youtube"],
        "privacy": "private",
        "publish_at": publish_iso
    }

    upload_res = requests.post(f"{API_BASE}/api/upload/clip", json=upload_payload, timeout=600).json()
    if not upload_res.get("success"):
        raise RuntimeError(f"YouTube upload failed: {upload_res.get('error')}")

    yt_result = upload_res.get("results", {}).get("youtube", {})
    short_url = yt_result.get("url")
    print(f"\n[SUCCESS] YouTube Short uploaded and scheduled for 23:30 PKT!", flush=True)
    print(f"URL: {short_url}", flush=True)

    # Step 6: Permanently record video as processed to guarantee zero repetition
    mark_video_as_processed(
        video_id=video_id,
        title=video_title,
        status="uploaded_and_scheduled",
        upload_url=short_url,
        clip_filename=master_filename
    )
    print("\n[COMPLETE] Video recorded in persistent non-repetition database. Daily automation finished successfully!", flush=True)


if __name__ == "__main__":
    run()
