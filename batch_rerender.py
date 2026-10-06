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
from title_tag_engine import generate_smart_title_and_hashtags, build_video_context
from publish_log import build_publication_record, log_publication, default_log_path

OUTPUT_DIR = Path(__file__).parent / "output"

def _video_context_by_id(json_files):
    """
    Build ONE video context per source video, shared by every clip of that video.

    Rerendering is a per-clip loop, so a naive fix would recompute the niche from each
    30 s snippet and each clip of one video would get a different niche -- exactly the bug
    this whole change set exists to remove. Only the `transcript_snippet` of already
    rendered clips is available here (the full transcript is not stored), so the context is
    built from the union of that video's snippets, which is a strict improvement over
    per-clip extraction and is still consistent within the video.
    """
    snippets = {}
    for jf in json_files:
        try:
            with open(jf, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        vid = data.get("video_id")
        snippet = data.get("transcript_snippet")
        if vid and snippet:
            snippets.setdefault(vid, []).append(str(snippet))
    return {vid: build_video_context(" ".join(parts)) for vid, parts in snippets.items()}

def rerender_all(filter_video_id: str = None, skip_recent_seconds: int = 1800):
    json_files = sorted(list(OUTPUT_DIR.glob("*.json")))
    if filter_video_id:
        json_files = [jf for jf in json_files if filter_video_id in jf.name]
        
    print(f"Found {len(json_files)} clip metadata files to process.", flush=True)

    # Computed once per video (not per clip) so every clip of one video agrees on niche.
    # Guarded because this is the one call in the function that sits outside every try: an
    # unhandled raise here aborts the whole batch before the summary accounting below runs,
    # which is exactly the failure mode the cut_and_format_clip guard further down exists to
    # prevent. Degrade to "no context for anyone" rather than dying.
    try:
        video_contexts = _video_context_by_id(json_files)
    except Exception as e:
        print(f"WARNING: could not build video contexts ({type(e).__name__}: {e}); "
              f"niche/seo_topic will be left untouched for this run.", flush=True)
        video_contexts = {}

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
            if not isinstance(data, dict):
                raise ValueError("metadata root is not a JSON object")
        except Exception as e:
            print(f"[{i}/{len(json_files)}] Error reading {jf.name}: {e}", flush=True)
            fail_count += 1
            continue

        # dict.get() returns the stored value (including null) whenever the key exists,
        # so every field needs an explicit None-coalesce before float() sees it.
        try:
            raw_start = data.get("start_time")
            if raw_start is None:
                raw_start = data.get("start")
            raw_end = data.get("end_time")
            if raw_end is None:
                raw_end = data.get("end")
            if raw_start is None or raw_end is None:
                raise ValueError("missing start_time/end_time")
            start = float(raw_start)
            end = float(raw_end)
            if end <= start:
                raise ValueError(f"end_time ({end}) must be greater than start_time ({start})")
        except (TypeError, ValueError) as e:
            print(f"[{i}/{len(json_files)}] SKIPPING {jf.name}: bad timing metadata ({e})", flush=True)
            fail_count += 1
            continue

        video_id = data.get("video_id")
        if not video_id:
            print(f"[{i}/{len(json_files)}] SKIPPING {jf.name}: missing video_id", flush=True)
            fail_count += 1
            continue

        style = data.get("subtitle_style", "bold_pop")
        if not style or style == "NOT_SET":
            style = "bold_pop"
        framing = data.get("framing_mode", "blurred")

        # Video-level context for this clip's video. Any file reaching here has both a
        # truthy video_id and a truthy transcript_snippet (both checked above), so its id is
        # necessarily a key in video_contexts. There is no fallback to write, and an earlier
        # `or build_video_context(snippet)` was dead code that quietly implied a per-clip
        # path still existed.
        ctx = video_contexts.get(video_id)

        # Niche and seo_topic reconciliation runs for EVERY file, not only inside the
        # title-backfill branch below. It used to live in there, and on the real corpus that
        # branch is entered for 0 of 45 files -- all 45 have candidate_titles and none has a
        # title containing "brutal truth" -- so the context was computed and then thrown
        # away: 0 niches changed and 0 seo_topic keys written.
        #
        # The niche policy is decided on observable evidence, not on a guess about history:
        #
        #   * `seo_topic_source == "full_transcript"` -- app.py or clipper.py produced it and
        #     had the WHOLE transcript. Stronger evidence than the snippet union available
        #     here, so the niche is preserved.
        #   * `seo_topic_source == "snippet_union"` -- backfill_titles.py produced it from
        #     30 s snippets, the same evidence level as this script, so the recomputation
        #     replaces it.
        #   * absent -- unknown, so the recomputation replaces it and the file is labelled.
        #
        # This used to branch on `"seo_topic" not in data`, with a comment claiming that
        # carrying that key PROVES a producer had the full transcript. True when written; false
        # the moment backfill_titles.py began writing the same key from snippet unions. The
        # rule then quietly became "preserve everything" -- a permanent freeze of exactly the
        # data it was meant to correct. A still earlier version froze unconditionally on a
        # premise that was simply false, leaving 9 of 11 videos disagreeing with themselves.
        migrated = False
        if ctx is not None:
            ctx_topic = (ctx.get("topics") or [None])[0]
            if data.get("seo_topic_source") == "full_transcript":
                # Keep the niche. Do not leave a stale seo_topic beside it.
                if data.get("seo_topic") != ctx_topic:
                    data["seo_topic"] = ctx_topic
                    migrated = True
            else:
                new_niche = ctx.get("niche") or data.get("niche") or "general_viral"
                if new_niche != data.get("niche"):
                    data["niche"] = new_niche
                    migrated = True
                if ctx_topic != data.get("seo_topic"):
                    data["seo_topic"] = ctx_topic
                    migrated = True
                if data.get("seo_topic_source") != "snippet_union":
                    # Record the evidence level actually used, so the next run -- and the
                    # quality budget -- can tell this apart from a full-transcript producer.
                    data["seo_topic_source"] = "snippet_union"
                    migrated = True

        # Backfill smart title, candidates, and tags if missing or old generic title
        snippet = data.get("transcript_snippet", "")
        decided_meta = None
        if snippet and (not data.get("candidate_titles") or "brutal truth" in str(data.get("suggested_title", "")).lower()):
            try:
                smart_meta = generate_smart_title_and_hashtags(
                    snippet,
                    data.get("category", "high_value_insight"),
                    video_context=ctx,
                )
                data["suggested_title"] = smart_meta.get("suggested_title", data.get("suggested_title"))
                data["suggested_hashtags"] = smart_meta.get("suggested_hashtags", data.get("suggested_hashtags"))
                data["candidate_titles"] = smart_meta.get("candidates", [])
                data["platform_metadata"] = smart_meta.get("platform_metadata", {})
                decided_meta = smart_meta
                migrated = True
            except Exception as e:
                # Was a bare `pass`, which hid every failure in this block -- including the
                # ones that mean a clip silently kept a junk title. A printed warning keeps
                # the batch running (the contract TestBug03_BatchRerenderAborts pins) while
                # making the failure visible.
                print(f"[{i}/{len(json_files)}] WARNING {jf.name}: title backfill failed "
                      f"({type(e).__name__}: {e})", flush=True)

        if migrated:
            try:
                with open(jf, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                if decided_meta is not None:
                    log_publication(default_log_path(), build_publication_record(
                        source="batch_rerender",
                        video_id=data.get("video_id", ""),
                        filename=data.get("filename", jf.name),
                        smart_meta=decided_meta,
                    ))
            except Exception as e:
                print(f"[{i}/{len(json_files)}] WARNING {jf.name}: metadata write failed "
                      f"({type(e).__name__}: {e})", flush=True)
            
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
        # cut_and_format_clip raises (e.g. RuntimeError when ffmpeg is not on PATH) instead of
        # always returning False, so the call itself must be guarded or one bad record aborts
        # the entire batch and the summary accounting below never runs.
        try:
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
                enable_auto_bleep=data.get("enable_auto_bleep", False),
                enable_slow_zoom=data.get("enable_slow_zoom", True),
                enable_bg_music=data.get("enable_bg_music", True)
            )
        except Exception as e:
            print(f"[{i}/{len(json_files)}] ERROR rendering {mp4_name}: {type(e).__name__}: {e}", flush=True)
            fail_count += 1
            continue
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
