"""
Channel Video Picker & Non-Repetition Registry for Outdoor Boys.

Guarantees strict zero-repetition:
1. Queries channel videos sorted by most popular.
2. Cross-references against processed_channel_videos.json.
3. Automatically selects the next unclipped popular video in order.
4. Records each processed video with timestamp so it is never reused.
"""

import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

BASE_DIR = Path(__file__).resolve().parent
REGISTRY_FILE = BASE_DIR / "processed_channel_videos.json"
DEFAULT_CHANNEL_URL = "https://www.youtube.com/@OutdoorBoys/videos?sort=p"


def load_registry() -> Dict[str, Any]:
    """Loads the history of processed video IDs."""
    if not REGISTRY_FILE.exists():
        # Pre-seed with the test video already processed
        initial = {
            "iys_pmJSp9M": {
                "title": "3 Days in Arctic Survival Shelter - Solo Bushcraft Camping & Blacksmithing.",
                "processed_at": datetime.now(timezone.utc).isoformat(),
                "status": "scheduled_on_youtube",
                "notes": "Processed and scheduled for 23:30 during initial test run"
            }
        }
        save_registry(initial)
        return initial

    try:
        with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Warning] Failed to read {REGISTRY_FILE}: {e}", file=sys.stderr)
        return {}


def save_registry(registry: Dict[str, Any]) -> bool:
    """Saves the processed video history to disk."""
    try:
        with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"[Error] Failed to write {REGISTRY_FILE}: {e}", file=sys.stderr)
        return False


def is_video_processed(video_id: str) -> bool:
    """Checks if a video has already been processed."""
    registry = load_registry()
    return video_id in registry


def mark_video_as_processed(
    video_id: str,
    title: str = "",
    status: str = "completed",
    upload_url: str = "",
    clip_filename: str = ""
) -> None:
    """Hardcodes a video as processed to permanently prevent any re-use."""
    registry = load_registry()
    registry[video_id] = {
        "title": title,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "upload_url": upload_url,
        "clip_filename": clip_filename
    }
    save_registry(registry)
    print(f"[Registry] Permanently recorded video {video_id} ('{title}') as processed. Repetition blocked.", flush=True)


def get_next_popular_video(
    channel_url: str = DEFAULT_CHANNEL_URL,
    max_candidates: int = 100
) -> Optional[Dict[str, Any]]:
    """
    Scans the channel's popular videos and returns the first video that has NEVER been processed.
    """
    import yt_dlp

    registry = load_registry()
    processed_ids = set(registry.keys())

    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "playlist_items": f"1:{max_candidates}"
    }

    print(f"[Channel Picker] Querying top {max_candidates} popular videos from {channel_url}...", flush=True)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)
        entries = info.get("entries") or []

    for rank, entry in enumerate(entries, 1):
        vid_id = entry.get("id")
        title = entry.get("title", "")
        if not vid_id:
            continue

        if vid_id in processed_ids:
            print(f"  [Skipping] Rank #{rank}: {vid_id} ('{title}') - Already processed.", flush=True)
            continue

        video_url = f"https://www.youtube.com/watch?v={vid_id}"
        print(f"  [Selected] Rank #{rank}: {vid_id} ('{title}') - Next unclipped video in popularity order!", flush=True)
        return {
            "id": vid_id,
            "url": video_url,
            "title": title,
            "rank_by_popularity": rank,
            "view_count": entry.get("view_count")
        }

    print("[Channel Picker] Error: All inspected popular videos have already been processed!", file=sys.stderr)
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Outdoor Boys video popularity picker with non-repetition lock.")
    parser.add_argument("--next", action="store_true", help="Fetch the next unclipped popular video")
    parser.add_argument("--mark", type=str, help="Mark video ID as processed")
    parser.add_argument("--title", type=str, default="", help="Video title when marking")
    parser.add_argument("--list", action="store_true", help="List all processed videos")

    args = parser.parse_args()

    if args.mark:
        mark_video_as_processed(args.mark, title=args.title)
    elif args.list:
        reg = load_registry()
        print(json.dumps(reg, indent=2))
    elif args.next or len(sys.argv) == 1:
        chosen = get_next_popular_video()
        if chosen:
            print(json.dumps(chosen, indent=2))
        else:
            sys.exit(1)
