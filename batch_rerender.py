import json
import os
import sys
import time
from pathlib import Path

# Force unbuffered output so logs display in real-time, safeguard UTF-8 encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from video_cutter import cut_and_format_clip
from title_tag_engine import generate_smart_title_and_hashtags

OUTPUT_DIR = Path(__file__).parent / "output"

def rerender_all(filter_video_id: str = None, skip_recent_seconds: int = 1800):
    json_files = sorted(list(OUTPUT_DIR.glob("*.json")))
    if filter_video_id:
        json_files = [jf for jf in json_files if filter_video_id in jf.name]
        
    print(f"Found {len(json_files)} clip metadata files to process.", flush=True)
    
    # Priority order: Put A6v5Vj6h_fQ first
    def sort_key(f):
        if "A6v5Vj6h_fQ" in f.name:
            return (0, f.name)
        return (1, f.name)
    
    json_files.sort(key=sort_key)
    
    success_count = 0
    fail_count = 0
    skipped_count = 0
    start_total = time.time()
    
    for i, jf in enumerate(json_files, 1):
        try:
            with open(jf, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[{i}/{len(json_files)}] Error reading {jf.name}: {e}", flush=True)
            fail_count += 1
            continue
            
        video_id = data.get("video_id")
        start = float(data.get("start_time", data.get("start", 0.0)))
        end = float(data.get("end_time", data.get("end", 0.0)))
        style = data.get("subtitle_style", "bold_pop")
        if not style or style == "NOT_SET":
            style = "bold_pop"
        framing = data.get("framing_mode", "blurred")

        # Backfill smart title, candidates, and tags if missing or old generic title
        snippet = data.get("transcript_snippet", "")
        if snippet and (not data.get("candidate_titles") or "brutal truth" in str(data.get("suggested_title", "")).lower()):
            try:
                smart_meta = generate_smart_title_and_hashtags(snippet, data.get("category", "high_value_insight"))
                data["suggested_title"] = smart_meta.get("suggested_title", data.get("suggested_title"))
                data["suggested_hashtags"] = smart_meta.get("suggested_hashtags", data.get("suggested_hashtags"))
                data["candidate_titles"] = smart_meta.get("candidates", [])
                data["niche"] = smart_meta.get("niche", "general_viral")
                data["platform_metadata"] = smart_meta.get("platform_metadata", {})
                with open(jf, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception:
                pass
            
        mp4_name = data.get("filename", f"{jf.stem}.mp4")
        mp4_path = OUTPUT_DIR / mp4_name
        
        # Skip if already re-rendered recently with new engine
        if mp4_path.exists() and skip_recent_seconds > 0:
            mtime = mp4_path.stat().st_mtime
            age_sec = time.time() - mtime
            if age_sec < skip_recent_seconds:
                print(f"[{i}/{len(json_files)}] SKIPPING {mp4_name} (already rendered {int(age_sec)}s ago with new engine)", flush=True)
                skipped_count += 1
                success_count += 1
                continue
        
        url = f"https://www.youtube.com/watch?v={video_id}"
        print(f"\n==========================================", flush=True)
        print(f"[{i}/{len(json_files)}] Processing: {mp4_name}", flush=True)
        print(f"Video: {video_id} | Time: {start}s -> {end}s | Style: {style} | Framing: {framing}", flush=True)
        print(f"==========================================", flush=True)
        
        t0 = time.time()
        ok = cut_and_format_clip(
            youtube_url=url,
            start_sec=start,
            end_sec=end,
            output_path=mp4_path,
            subtitle_style=style,
            framing_mode=framing,
            enable_broll=data.get("enable_broll", True),
            enable_emojis=data.get("enable_emojis", True),
            enable_snappy_cuts=data.get("enable_snappy_cuts", True),
            enable_punch_zooms=data.get("enable_punch_zooms", True),
            enable_outro=data.get("enable_outro", True),
            enable_auto_bleep=data.get("enable_auto_bleep", False)
        )
        elapsed = round(time.time() - t0, 1)
        if ok and mp4_path.exists():
            mb = round(mp4_path.stat().st_size / (1024 * 1024), 2)
            print(f"[{i}/{len(json_files)}] OK: {mp4_name} ({mb} MB) rendered in {elapsed}s", flush=True)
            success_count += 1
        else:
            print(f"[{i}/{len(json_files)}] FAILED: {mp4_name}", flush=True)
            fail_count += 1
            
    total_time = round(time.time() - start_total, 1)
    print("\n" + "=" * 50, flush=True)
    print(f"BATCH RE-RENDER SUMMARY:", flush=True)
    print(f"Total: {len(json_files)} | Succeeded: {success_count} (Skipped already fresh: {skipped_count}) | Failed: {fail_count}", flush=True)
    print(f"Total time elapsed: {total_time}s", flush=True)
    print("=" * 50, flush=True)

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a not in ("--force", "-f")]
    target_vid = args[0] if args else None
    force = any(a in ("--force", "-f") for a in sys.argv[1:])
    skip_sec = 0 if force else 1800
    rerender_all(filter_video_id=target_vid, skip_recent_seconds=skip_sec)
