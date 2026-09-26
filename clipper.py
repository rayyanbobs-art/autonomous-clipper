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

from youtube_transcript_api import YouTubeTranscriptApi
from config import OUTPUT_DIR, get_jev_api_key
from scorer import create_windows, score_chunk_with_jev, critique_gate_search
from video_cutter import cut_and_format_clip

from niche_scraper import get_video_transcript, extract_video_id as _extract_id
from title_tag_engine import generate_smart_title_and_hashtags
from uploader import upload_clip_to_platforms, load_upload_config

def extract_video_id(url_or_id: str) -> str:
    """Extracts YouTube 11-character video ID from various URL formats using robust parser."""
    vid = _extract_id(url_or_id)
    if vid and len(vid) == 11 and re.match(r"^[0-9A-Za-z_-]{11}$", vid):
        return vid
    raise ValueError(f"Could not extract a valid YouTube video ID from '{url_or_id}'")

def get_transcript(video_id: str):
    """Fetches English or auto-generated transcript with yt-dlp fallback."""
    res = get_video_transcript(video_id)
    if not res or not res.get("available"):
        return []
    return res.get("snippets", [])

def process_video(
    youtube_url: str,
    top_k: int = 3,
    candidates: int = 15,
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
    print("=" * 60)
    print("  AUTONOMOUS PODCAST-TO-SHORTS CLIPPING ENGINE")
    print("  Powered by Jev AI + 6-Dimension Critique Gate + Pexels B-Roll + FFmpeg")
    print(f"  Subtitle Style: {subtitle_style.replace('_', ' ').title()} | Gate Threshold: {threshold}/10")
    print(f"  AI B-Roll: {'ON' if enable_broll else 'OFF'} | Auto-Emojis: {'ON' if enable_emojis else 'OFF'} | Framing: {framing_mode.title()}")
    print(f"  Snappy Cuts: {'ON' if enable_snappy_cuts else 'OFF'} | Punch-Zooms: {'ON' if enable_punch_zooms else 'OFF'} | Outro Card: {'ON' if enable_outro else 'OFF'}")
    print(f"  Sponsor Killer: {'ON' if enable_sponsor_killer else 'OFF'} | Auto-Bleeper: {'ON' if enable_auto_bleep else 'OFF'}")
    print("=" * 60)

    video_id = extract_video_id(youtube_url)
    clean_url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"\n[1/5] Processing Video ID: {video_id}")
    print(f"      URL: {clean_url}")

    # Fetch transcript
    print("\n[2/5] Fetching transcript captions...")
    transcript = get_transcript(video_id)
    if not transcript:
        print(f"      [Error] No transcript captions available for video '{video_id}'.")
        return
    total_seconds = transcript[-1]["start"] + transcript[-1]["duration"]
    print(f"      Loaded {len(transcript)} caption blocks. Total runtime: {int(total_seconds // 60)}m {int(total_seconds % 60)}s")

    # Generate candidate windows
    print("\n[3/5] Sliding 28s-55s candidate windows across transcript...")
    candidate_windows = create_windows(transcript, min_duration=28.0, max_duration=55.0, step=30.0)
    if candidates and candidates > 0 and len(candidate_windows) > candidates:
        stride = len(candidate_windows) / candidates
        candidate_windows = [candidate_windows[int(i * stride)] for i in range(candidates)]
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

    for rank, clip in enumerate(top_clips, 1):
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
            enable_auto_bleep=enable_auto_bleep
        )

        if success and output_mp4.exists():
            cat_formatted = clip.get("category", "high_value_insight").replace("_", " ").title()
            smart_meta = generate_smart_title_and_hashtags(
                transcript_text=clip["text"],
                category=clip.get("category", "high_value_insight")
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
                "niche": smart_meta.get("niche", "general_viral"),
                "candidate_titles": smart_meta.get("candidates", []),
                "platform_metadata": smart_meta.get("platform_metadata", {})
            }
            with open(meta_json, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)

            file_size_mb = round(output_mp4.stat().st_size / (1024 * 1024), 2)
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
        else:
            print(f"  -> FAILED to render clip #{rank}")

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
    parser.add_argument("--style", type=str, default="bold_pop", help="Subtitle preset (e.g. bold_pop, karaoke, boxed_clean, hormozi, beast, kinetic_pop)")
    parser.add_argument("--threshold", type=float, default=7.0, help="Critique gate quality threshold out of 10 (default: 7.0)")
    parser.add_argument("--max-attempts", type=int, default=4, help="Maximum critique refinement attempts per clip (default: 4)")
    parser.add_argument("--no-broll", action="store_true", help="Disable AI stock B-roll video overlays")
    parser.add_argument("--no-emojis", action="store_true", help="Disable auto-emojis in subtitles")
    parser.add_argument("--framing", type=str, default="blurred", choices=["blurred", "smart_face", "split_gaming"], help="Framing: blurred (default), smart_face (AI face tracking), or split_gaming (Streamer Cam + Full Game)")
    parser.add_argument("--no-snappy", action="store_true", help="Disable snappy dead-air silence jump-cuts")
    parser.add_argument("--no-zooms", action="store_true", help="Disable audio energy reaction punch-zooms")
    parser.add_argument("--no-outro", action="store_true", help="Disable 1-second SUBSCRIBE outro card")
    parser.add_argument("--no-sponsor-killer", action="store_true", help="Disable automatic sponsor & promo ad disqualification")
    parser.add_argument("--enable-auto-bleep", action="store_true", help="Enable 1000Hz audio bleep and subtitle masking for profanities")

    args = parser.parse_args()
    process_video(
        youtube_url=args.url,
        top_k=args.top,
        candidates=args.candidates,
        subtitle_style=args.style,
        threshold=args.threshold,
        max_attempts=args.max_attempts,
        enable_broll=not args.no_broll,
        enable_emojis=not args.no_emojis,
        framing_mode=args.framing,
        enable_snappy_cuts=not args.no_snappy,
        enable_punch_zooms=not args.no_zooms,
        enable_outro=not args.no_outro,
        enable_sponsor_killer=not args.no_sponsor_killer,
        enable_auto_bleep=args.enable_auto_bleep
    )

if __name__ == "__main__":
    main()
