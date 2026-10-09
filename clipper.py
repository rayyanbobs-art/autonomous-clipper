import os
import sys
import re
import json
import argparse
from pathlib import Path

# Safeguard UTF-8 encoding on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import hashlib
import subprocess
from typing import Optional, Any
from youtube_transcript_api import YouTubeTranscriptApi
from config import (
    OUTPUT_DIR,
    TEMP_DIR,
    get_jev_api_key,
    MIN_CLIP_DURATION,
    MAX_CLIP_DURATION,
    CLIP_WINDOW_STEP,
)
from scorer import create_windows, critique_gate_search, parse_timestamp_to_seconds
from video_cutter import cut_and_format_clip

from niche_scraper import get_video_transcript, extract_video_id as _extract_id
from title_tag_engine import generate_smart_title_and_hashtags, build_video_context
from publish_log import build_publication_record, log_publication, default_log_path
from uploader import upload_clip_to_platforms, load_upload_config

def is_local_video(path_str: Any) -> bool:
    """Returns True if path_str points to an existing file on the local filesystem."""
    if not path_str or not isinstance(path_str, (str, Path)):
        return False
    try:
        p = Path(str(path_str).strip('"').strip("'"))
        return p.is_file()
    except Exception:
        return False

def extract_video_id(url_or_id: str) -> str:
    """Extracts YouTube 11-character video ID or generates a deterministic 11-char ID for local files."""
    if is_local_video(url_or_id):
        p = Path(str(url_or_id).strip('"').strip("'")).resolve()
        digest = hashlib.md5(str(p).encode("utf-8")).hexdigest()[:7]
        return f"loc_{digest}"
    vid = _extract_id(url_or_id)
    if vid and len(vid) == 11 and re.match(r"^[0-9A-Za-z_-]{11}$", vid):
        return vid
    raise ValueError(f"Could not extract a valid YouTube video ID from '{url_or_id}'")

def get_local_transcript(media_path: Path, video_id: Optional[str] = None) -> list:
    """Transcribes an offline local video file using faster-whisper, with caching."""
    media_path = Path(media_path).resolve()
    cache_id = video_id or f"loc_{hashlib.md5(str(media_path).encode('utf-8')).hexdigest()[:7]}"
    cache_file = TEMP_DIR / f"{cache_id}_transcript.json"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(cached, list) and cached:
                return cached
        except Exception:
            pass

    from subtitles import get_whisper_model
    model = get_whisper_model("base.en")
    print(f"      Transcribing local video with faster-whisper ({media_path.name})...")
    segments, _ = model.transcribe(
        str(media_path),
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=250)
    )
    snippets = []
    for s in segments:
        text = (s.text or "").strip()
        if text:
            snippets.append({
                "text": text,
                "start": round(float(s.start), 2),
                "duration": round(float(max(0.1, s.end - s.start)), 2)
            })

    if not snippets:
        raise RuntimeError(f"No spoken words detected in local video '{media_path.name}'.")

    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(snippets, indent=2), encoding="utf-8")
    except Exception:
        pass

    return snippets

def get_transcript(video_id: str, temp_dir: Optional[Path] = None):
    """Fetches English or auto-generated transcript with resilient yt-dlp audio download + Faster-Whisper fallback."""
    try:
        res = get_video_transcript(video_id)
        if res and res.get("available") and res.get("snippets"):
            return res.get("snippets", [])
    except Exception as e:
        print(f"  [Transcript API Warning] Caption fetch failed ({e}). Engaging audio fallback...", flush=True)

    print(f"  [Resilient 429 Fallback] Captions blocked or unavailable for {video_id}. Downloading low-bitrate audio stream...", flush=True)
    work_dir = Path(temp_dir) if temp_dir else TEMP_DIR
    work_dir.mkdir(parents=True, exist_ok=True)
    audio_path = work_dir / f"audio_fallback_{video_id}.m4a"

    ytdlp_cmd = [
        sys.executable, "-m", "yt_dlp",
        "--extractor-args", "youtube:player_client=all",
        "--cookies-from-browser", "vivaldi",
        "--js-runtimes", r"node:C:\Users\Saeed\AppData\Local\Programs\nodejs\node.exe",
        "-f", "ba[ext=m4a]/140/ba/worstvideo+worstaudio",
        "--no-playlist",
        "-o", str(audio_path),
        f"https://www.youtube.com/watch?v={video_id}"
    ]
    try:
        sub_kwargs = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace", "timeout": 180}
        if sys.platform == "win32" and hasattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS"):
            sub_kwargs["creationflags"] = subprocess.BELOW_NORMAL_PRIORITY_CLASS
        proc = subprocess.run(ytdlp_cmd, **sub_kwargs)
        if not audio_path.exists():
            for f in work_dir.glob(f"audio_fallback_{video_id}*"):
                audio_path = f
                break
        if audio_path.exists():
            print(f"  [Resilient 429 Fallback] Audio downloaded ({audio_path.name}). Transcribing locally with Faster-Whisper...", flush=True)
            snippets = get_local_transcript(audio_path, video_id=video_id)
            if snippets:
                return snippets
    except Exception as ex:
        print(f"  [Resilient 429 Fallback Error] Audio download/transcribe failed: {ex}", flush=True)

    raise RuntimeError(f"No captions or transcript found for video '{video_id}'. YouTube captions and audio fallback both failed.")

def process_video(
    youtube_url: str,
    top_k: int = 3,
    candidates: int = 15,
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
    time_range_end: Optional[Any] = None,
    custom_subtitle_style: Optional[Dict] = None
):
    print("=" * 60)
    print("  AUTONOMOUS PODCAST-TO-SHORTS CLIPPING ENGINE")
    print("  Powered by Jev AI + 6-Dimension Critique Gate + Pexels B-Roll + FFmpeg")
    print(f"  Subtitle Style: {subtitle_style.replace('_', ' ').title()} | Gate Threshold: {threshold}/10")
    print(f"  AI B-Roll: {'ON' if enable_broll else 'OFF'} | Auto-Emojis: {'ON' if enable_emojis else 'OFF'} | Framing: {framing_mode.title()}")
    print(f"  Snappy Cuts: {'ON' if enable_snappy_cuts else 'OFF'} | Punch-Zooms: {'ON' if enable_punch_zooms else 'OFF'} | Outro Card: {'ON' if enable_outro else 'OFF'}")
    print(f"  Sponsor Killer: {'ON' if enable_sponsor_killer else 'OFF'} | Auto-Bleeper: {'ON' if enable_auto_bleep else 'OFF'}")
    print("=" * 60)

    is_local = is_local_video(youtube_url)
    video_id = extract_video_id(youtube_url)
    clean_url = str(Path(str(youtube_url).strip('"').strip("'")).resolve()) if is_local else f"https://www.youtube.com/watch?v={video_id}"

    if is_local:
        print(f"\n[1/5] Processing Local Video: {Path(clean_url).name} (ID: {video_id})")
        print(f"      Path: {clean_url}")
        print("\n[2/5] Transcribing local audio with faster-whisper...")
        transcript = get_local_transcript(Path(clean_url), video_id=video_id)
    else:
        print(f"\n[1/5] Processing Video ID: {video_id}")
        print(f"      URL: {clean_url}")
        print("\n[2/5] Fetching transcript captions...")
        transcript = get_transcript(video_id)

    if not transcript:
        print(f"      [Error] No transcript captions available for video '{video_id}'.")
        return
    total_seconds = transcript[-1]["start"] + transcript[-1]["duration"]
    print(f"      Loaded {len(transcript)} caption blocks. Total runtime: {int(total_seconds // 60)}m {int(total_seconds % 60)}s")

    r_start = parse_timestamp_to_seconds(time_range_start)
    r_end = parse_timestamp_to_seconds(time_range_end)
    if r_start is not None or r_end is not None:
        print(f"      Restricting candidate search to time range: {r_start or 0.0}s -> {r_end or 'End'}s")

    # Video-level context: computed ONCE from the whole transcript, then reused by every
    # clip so titles, niche and hashtags stay consistent across the job. Must stay in sync
    # with app.run_clipping_job, which writes the same output/*.json schema.
    video_context = build_video_context(" ".join(t.get("text", "") for t in transcript))

    # Generate candidate windows (same constants as the web GUI -> reproducible clip boundaries)
    print(f"\n[3/5] Sliding {int(MIN_CLIP_DURATION)}s-{int(MAX_CLIP_DURATION)}s candidate windows across transcript...")
    candidate_windows = create_windows(
        transcript,
        min_duration=MIN_CLIP_DURATION,
        max_duration=MAX_CLIP_DURATION,
        step=CLIP_WINDOW_STEP,
        range_start=r_start,
        range_end=r_end
    )
    effective_candidates = max(candidates or 15, top_k * 4)
    if effective_candidates > 0 and len(candidate_windows) > effective_candidates:
        stride = len(candidate_windows) / effective_candidates
        candidate_windows = [candidate_windows[int(i * stride)] for i in range(effective_candidates)]
    print(f"      Generated {len(candidate_windows)} candidate windows.")

    api_key = get_jev_api_key()
    print(f"\n[4/5] Running 6-Dimension Critique Gate (Threshold: {threshold}/10, Max Attempts: {max_attempts})...")

    def on_progress(clip_num, attempt_num, msg):
        print(f"      Clip #{clip_num} [Attempt {attempt_num}/{max_attempts}]: {msg}")

    top_clips = critique_gate_search(
        candidate_windows=candidate_windows,
        target_clips=top_k,
        threshold=threshold,
        max_attempts=max_attempts,
        subtitle_style=subtitle_style,
        enable_sponsor_killer=enable_sponsor_killer,
        api_key=api_key,
        progress_callback=on_progress
    )

    if not top_clips:
        print("      No clips passed the critique gate. Try adjusting duration or threshold.")
        return

    print(f"\n      Selected {len(top_clips)} winning clips meeting quality standards.")

    # Slicing and rendering clips
    print(f"\n[5/5] Slicing & Rendering 9:16 Vertical Video Shorts with {subtitle_style.replace('_', ' ').title()} Subtitles...")
    created_files = []

    def _render_and_package_clip(item):
        rank, clip = item
        filename_base = f"{video_id}_clip_{rank}_{int(clip['start'])}s"
        output_mp4 = OUTPUT_DIR / f"{filename_base}.mp4"
        meta_json = OUTPUT_DIR / f"{filename_base}.json"

        critique_info = clip.get("critique", {})
        rubric_scores = critique_info.get("scores", {})
        rubric_avg = critique_info.get("average", round(clip.get("virality_score", 1.0) * 3.0, 1))

        print(f"\n  --- Rendering Clip #{rank} [{clip['start']}s -> {clip['end']}s] ---")
        print(f"  Rubric Quality Score: {rubric_avg}/10 | Hook: {rubric_scores.get('hook', '-')}/10 | Payoff: {rubric_scores.get('payoff', '-')}/10")
        print(f"  Category: {clip.get('category', 'high_value_insight')} | Style: {subtitle_style}")
        print(f"  Hook preview: \"{clip['text'][:90]}...\"")

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
            bg_music_volume=bg_music_volume,
            custom_subtitle_style=custom_subtitle_style
        )

        if success and output_mp4.exists():
            cat_formatted = clip.get("category", "high_value_insight").replace("_", " ").title()
            extra_meta_kwargs = {}
            if clip.get("visual_context") is not None:
                extra_meta_kwargs["visual_context"] = clip["visual_context"]
            smart_meta = generate_smart_title_and_hashtags(
                transcript_text=clip["text"],
                category=clip.get("category", "high_value_insight"),
                video_context=video_context,
                **extra_meta_kwargs
            )
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
                "rubric_scores": rubric_scores,
                "rubric_average": rubric_avg,
                "passed_gate": critique_info.get("passed", True),
                "critique_notes": critique_info.get("notes", ""),
                "transcript_snippet": clip["text"],
                "suggested_title": smart_meta.get("suggested_title", f"The brutal truth about {cat_formatted} 🤯"),
                "suggested_hashtags": smart_meta.get("suggested_hashtags", ["#shorts", "#viral"]),
                "niche": video_context.get("niche") or smart_meta.get("niche", "general_viral"),
                "seo_topic": (video_context.get("topics") or [None])[0],
                "seo_topic_source": "full_transcript",
                "candidate_titles": smart_meta.get("candidates", []),
                "platform_metadata": smart_meta.get("platform_metadata", {})
            }
            with open(meta_json, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)
            log_publication(default_log_path(), build_publication_record(
                source="clipper",
                video_id=video_id,
                filename=output_mp4.name,
                smart_meta=smart_meta,
                virality_score=metadata.get("virality_score"),
                standalone_probability=metadata.get("standalone_probability"),
                sponsor_probability=metadata.get("sponsor_probability"),
            ))

            file_size_mb = round(output_mp4.stat().st_size / (1024 * 1024), 2)
            return (output_mp4, file_size_mb, clip, metadata, smart_meta)
        return None

    import concurrent.futures
    workers = min(2, len(top_clips)) if len(top_clips) > 1 else 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        rendered_results = list(executor.map(_render_and_package_clip, enumerate(top_clips, 1)))

    for res in rendered_results:
        if res:
            output_mp4, file_size_mb, clip, metadata, smart_meta = res
            created_files.append((output_mp4, file_size_mb, clip))
            print(f"  -> SUCCESS: Created {output_mp4.name} ({file_size_mb} MB)")
            print(f"     Title: {metadata['suggested_title']}")
            print(f"     Tags:  {' '.join(metadata['suggested_hashtags'])}")

            # Auto-upload if configured
            upload_cfg = load_upload_config()
            if upload_cfg.get("auto_upload", {}).get("enabled", False):
                auto_platforms = upload_cfg.get("auto_upload", {}).get("platforms", ["youtube"])
                auto_privacy = upload_cfg.get("auto_upload", {}).get("default_privacy", "public")
                print(f"  [Auto-Upload] Dispatching {output_mp4.name} to {auto_platforms} ({auto_privacy})...")
                up_res = upload_clip_to_platforms(
                    clip_filename=output_mp4.name,
                    title=metadata["suggested_title"],
                    description=smart_meta.get("platform_metadata", {}).get("youtube", {}).get("description", ""),
                    hashtags=metadata["suggested_hashtags"],
                    platforms=auto_platforms,
                    privacy_status=auto_privacy
                )
                if up_res.get("success"):
                    print(f"  [Auto-Upload Success] Clip uploaded successfully!")

    print("\n" + "=" * 60)
    print("  PIPELINE COMPLETE!")
    print(f"  Successfully produced {len(created_files)} ready-to-post vertical Shorts.")
    print(f"  Output folder: {OUTPUT_DIR}")
    print("=" * 60)

def main():
    parser = argparse.ArgumentParser(description="Autonomous Podcast-to-Shorts Video Clipper with Critique Gate")
    parser.add_argument("--url", type=str, required=True, help="YouTube video URL or video ID")
    parser.add_argument("--top", type=int, default=3, help="Number of top clips to produce (default: 3)")
    parser.add_argument("--candidates", type=int, default=15, help="Number of candidate windows to evaluate (default: 15)")
    parser.add_argument("--style", type=str, default="wild_den", help="Subtitle preset (e.g. wild_den, bold_pop, karaoke, boxed_clean, hormozi, beast, kinetic_pop)")
    parser.add_argument("--threshold", type=float, default=7.0, help="Critique gate quality threshold out of 10 (default: 7.0)")
    parser.add_argument("--max-attempts", type=int, default=4, help="Maximum critique refinement attempts per clip (default: 4)")
    parser.add_argument("--broll", action="store_true", help="Enable AI stock B-roll video overlays (default: off)")
    parser.add_argument("--no-broll", action="store_true", help="Disable AI stock B-roll video overlays (default; kept for compatibility)")
    parser.add_argument("--emojis", action="store_true", help="Enable auto-emojis in subtitles (default: off)")
    parser.add_argument("--no-emojis", action="store_true", help="Disable auto-emojis in subtitles (default; kept for compatibility)")
    parser.add_argument("--framing", type=str, default="smart_face", choices=["smart_face", "blurred", "split_gaming"], help="Framing: smart_face (default, AI face tracking), blurred, or split_gaming")
    parser.add_argument("--no-snappy", action="store_true", help="Disable snappy dead-air silence jump-cuts")
    parser.add_argument("--punch-zooms", action="store_true", help="Enable audio energy reaction punch-zooms (default: False)")
    parser.add_argument("--no-zooms", action="store_true", help="Disable audio energy reaction punch-zooms")
    parser.add_argument("--outro", action="store_true", help="Append 1-second SUBSCRIBE outro card (default: off, keeps the Short looping)")
    parser.add_argument("--no-outro", action="store_true", help="Disable 1-second SUBSCRIBE outro card (default; kept for compatibility)")
    parser.add_argument("--bg-music", action="store_true", help="Mix ambient background music bed (default: off)")
    parser.add_argument("--no-slow-zoom", action="store_true", help="Disable slow 1.00x-1.05x zoom & drift")
    parser.add_argument("--no-sponsor-killer", action="store_true", help="Disable automatic sponsor & promo ad disqualification")
    parser.add_argument("--enable-auto-bleep", action="store_true", help="Enable 1000Hz audio bleep and subtitle masking for profanities")
    parser.add_argument("--range-start", type=str, default=None, help="Clip only starting from this timestamp (e.g. '10:00', '600')")
    parser.add_argument("--range-end", type=str, default=None, help="Clip only ending at this timestamp (e.g. '21:00', '1260')")

    args = parser.parse_args()
    process_video(
        youtube_url=args.url,
        top_k=args.top,
        candidates=args.candidates,
        subtitle_style=args.style,
        threshold=args.threshold,
        max_attempts=args.max_attempts,
        enable_broll=args.broll and not args.no_broll,
        enable_emojis=args.emojis and not args.no_emojis,
        framing_mode=args.framing,
        enable_snappy_cuts=not args.no_snappy,
        enable_punch_zooms=args.punch_zooms and not args.no_zooms,
        enable_outro=args.outro and not args.no_outro,
        enable_sponsor_killer=not args.no_sponsor_killer,
        enable_auto_bleep=args.enable_auto_bleep,
        enable_slow_zoom=not args.no_slow_zoom,
        enable_bg_music=args.bg_music,
        time_range_start=args.range_start,
        time_range_end=args.range_end
    )

if __name__ == "__main__":
    main()
