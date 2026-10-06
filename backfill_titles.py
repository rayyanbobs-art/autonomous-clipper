# backfill_titles.py
"""
Regenerate titles, hashtags, niche and seo_topic for existing clips in output/ WITHOUT
re-encoding any video. Useful after changing the title engine, and far faster than
batch_rerender.py (which re-downloads and re-renders every clip through ffmpeg).

Four rules govern what is written, and each one exists because breaking it was measured:

  1. ONE context per source VIDEO, built from the union of that video's clips' snippets --
     and built from ALL of that video's clips, never a filtered subset. Building it from a
     single clip's snippet is the pre-Task-3 per-clip bug wearing a different hat: two clips
     of one video would then disagree again.

  2. A clip whose video context is unavailable is SKIPPED, not given a per-clip one.
     `generate_smart_title_and_hashtags` falls back to `build_video_context(snippet)` when
     handed `video_context=None`, so "no context" and "a per-clip context" are the same code
     path unless this script refuses to call it. A version that did not refuse printed the
     exact success block for a run that had silently reintroduced the bug on all 45 files.

  3. `niche` and `seo_topic` are reconciled for every written file, unconditionally, BEFORE
     the title gate is consulted. Gating the migration behind the gate means a gate regression
     silently freezes the corpus: this plan's Calibration Finding #4 records the gate once
     rejecting 100% of titles, and the symptom would be a summary reading `gate rejected: 45`
     with every file still holding its old per-clip niche.

  4. Unrecognised flags are a hard error. `--dryrun` is not `--dry-run`, and on the one
     script in this plan that writes real data, mistyping the dry-run flag used to mean a
     full write with exit 0 and no warning.

Reproducibility, stated honestly: the winning candidate comes from
`score_and_rank_titles_with_jev`, which POSTs to a third-party API whenever a key is
configured. That endpoint currently answers 502, so the call falls through and picks
`candidates[0]` -- which is why re-running this is byte-stable today. That is a property of
someone else's outage, not of this script. Pass `--offline` to make it deliberate: the key is
stubbed, no request is made, and the run is reproducible on a machine with no network.
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from title_tag_engine import build_video_context, generate_smart_title_and_hashtags
from title_seo import validate_title
from publish_log import build_publication_record, log_publication, default_log_path

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"

# json.dump is called WITHOUT ensure_ascii, matching app.py, clipper.py and
# batch_rerender.py. Passing ensure_ascii=False here would rewrite the byte representation
# of every file in a corpus containing emoji, and the next batch_rerender pass would
# re-escape them -- permanent churn between two writers of the same file.
_JSON_WRITE = {"indent": 2}

_FLAGS = {
    "--dry-run": "report what would change and write nothing",
    "--offline": "stub the Jev API key, so no third-party request is made",
}
_VALUE_FLAGS = {"--output-dir": "read and write a different corpus directory"}


def _load(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("metadata root is not a JSON object")
    return data


def build_contexts(files: List[Path]) -> Dict[str, Dict[str, Any]]:
    """
    ONE video context per source video, from the UNION of that video's snippets.

    Only the `transcript_snippet` of already-rendered clips is available here -- the full
    transcript is not stored -- so the union is the best available signal. It is still
    consistent within the video, which is the property that matters.

    Note on `seo_topic`: `batch_rerender.py` and `test_video_context_parity.py` both treat the
    PRESENCE of that key as a provenance marker -- "a post-Task-3 producer saw the full
    transcript, so this niche is better evidence than a snippet union" -- and they preserve
    whatever they find. This script's evidence is a union of snippets, the same level those
    guards were reaching for. It writes the key anyway, which is correct for the value, and
    the value is identical to the snippet-union niche on 45/45 files. Recorded because the
    presence-based justification in those two modules is now slightly stronger than the
    evidence behind it, and nobody should read the key as proof of a full transcript.
    """
    snippets: Dict[str, List[str]] = {}
    for path in files:
        vid, snippet = None, None
        try:
            data = _load(path)
            vid = data.get("video_id")
            snippet = data.get("transcript_snippet")
        except Exception as exc:
            # Surfaced, not swallowed. A file that fails to load here but loads in the main
            # loop would otherwise land with no context and be silently skipped.
            print(f"  [Backfill] could not read {path.name} while grouping: "
                  f"{type(exc).__name__}: {exc}")
            continue
        if vid and snippet:
            snippets.setdefault(vid, []).append(str(snippet))
    return {vid: build_video_context(" ".join(parts)) for vid, parts in snippets.items()}


def backfill(
    output_dir: Path = OUTPUT_DIR,
    filter_video_id: Optional[str] = None,
    dry_run: bool = False,
    offline: bool = False,
) -> Dict[str, int]:
    """
    Rewrites title/hashtag metadata for every clip JSON. Returns a summary counter.

    `filter_video_id` selects which files are REWRITTEN, matched as a substring of the
    filename. It never narrows the context: the per-video union is always built from every
    clip of that video present in `output_dir`, because a union of a subset is not a video
    context. (A version that filtered first wrote a per-clip niche for the one file it
    touched, leaving it disagreeing with its four siblings, and reported success.)
    """
    all_files = sorted(output_dir.glob("*.json"))
    if not all_files:
        raise SystemExit(f"no *.json found in {output_dir}")

    # Contexts come from the WHOLE corpus, before any filtering.
    contexts = build_contexts(all_files)

    files = all_files
    if filter_video_id:
        files = [f for f in files if filter_video_id in f.name]
        if not files:
            raise SystemExit(f"filter {filter_video_id!r} matched no file in {output_dir}")

    stats = {"total": 0, "updated": 0, "changed": 0, "would_change": 0,
             "skipped_no_transcript": 0, "skipped_no_context": 0,
             "skipped_full_transcript": 0, "skipped_unknown_provenance": 0,
             "rejected_by_gate": 0, "failed": 0}

    if offline:
        import title_tag_engine
        title_tag_engine.get_jev_api_key = lambda: None

    for path in files:
        stats["total"] += 1
        try:
            data = _load(path)
        except Exception as exc:
            print(f"  [Backfill] unreadable {path.name}: {exc}")
            stats["failed"] += 1
            continue

        # Snapshot so `changed` can mean "files whose content differs", not "keys that
        # differ". Counting per key reported 196 for 45 files, which reads as a bug.
        before_dump = json.dumps(data, sort_keys=True)

        if data.get("seo_topic_source") == "full_transcript":
            # SKIP THE WHOLE FILE, not just its niche.
            #
            # Protecting only `niche`/`seo_topic` is not enough, and I checked rather than
            # assumed: on the one real full-transcript clip the title this tool regenerates
            # from a 30 s snippet is `The Mechanics Of warm Explained Simply`, where the
            # pipeline wrote `The Mechanics Of stop Explained Simply`. Same corruption, one
            # field over -- the title, hashtags and candidate list all come from the same
            # context, and this tool's context is the weaker one.
            #
            # There is therefore nothing here this script can faithfully regenerate. The
            # honest action is to leave the file alone, say so, and point at the real remedy:
            # re-run the pipeline for that video, which still has the transcript.
            stats["skipped_full_transcript"] += 1
            print(f"  [Backfill] {path.name}: written from the FULL transcript "
                  f"(seo_topic_source=full_transcript) -- SKIPPED entirely. This tool only "
                  f"has a 30 s snippet and would degrade the title, hashtags and candidates "
                  f"as well as the niche. Re-run the pipeline for this video to refresh it.")
            continue


        snippet = data.get("transcript_snippet") or ""
        if not snippet.strip():
            stats["skipped_no_transcript"] += 1
            continue

        video_id = data.get("video_id") or ""
        ctx = contexts.get(video_id)
        if ctx is None:
            # Rule 2. The engine treats `video_context=None` as "build one from this clip",
            # which is the per-clip bug this script exists to remove. So do not call it:
            # leave the file entirely alone and say so in the summary.
            print(f"  [Backfill] no video context for {path.name} (video_id={video_id!r}); "
                  f"SKIPPED rather than falling back to a per-clip context")
            stats["skipped_no_context"] += 1
            continue

        dirty = False

        def put(key: str, value) -> None:
            """
            Assign, noting whether this actually changes what is on disk.

            Presence is part of the test: a file with no `seo_topic` and a computed value of
            `None` would compare equal under `data.get(key) != value` and never be written,
            while still counting as `updated`. The summary cannot express that, so the write
            is the thing that decides, not the comparison.
            """
            nonlocal dirty
            if key not in data or data[key] != value:
                dirty = True
            data[key] = value

        # ---- niche and seo_topic, with a provenance guard ----
        #
        # "Unconditional" was right for the 45 pre-key files and wrong in general. This tool
        # only ever sees 30 s snippets; a file from app.py/clipper.py was mined from the WHOLE
        # transcript, so overwriting it swaps better evidence for worse, silently.
        #
        # That is not hypothetical -- it happened. Running this over a freshly generated clip
        # replaced niche='gaming'/seo_topic='chicken' (from ~12 min of speech) with
        # 'general_viral'/'form' (from one 30 s window), on an untracked file with no backup.
        #
        # Remaining case: no recorded source, but the stored values DISAGREE with what this
        # tool derives. That disagreement IS the evidence that something with better inputs
        # wrote it -- most likely app.py before this key existed. Preserve, and say why.
        derived_niche = ctx.get("niche") or data.get("niche") or "general_viral"
        derived_topic = (ctx.get("topics") or [None])[0]
        if (data.get("seo_topic_source") is None
                and (data.get("niche") != derived_niche
                     or data.get("seo_topic") != derived_topic)):
            stats["skipped_unknown_provenance"] += 1
            print(f"  [Backfill] {path.name}: stored niche={data.get('niche')!r} "
                  f"seo_topic={data.get('seo_topic')!r} disagrees with the snippet derivation "
                  f"({derived_niche!r}/{derived_topic!r}) and no seo_topic_source is recorded, "
                  f"so something with better evidence wrote it -- preserved")
        else:
            put("niche", derived_niche)
            put("seo_topic", derived_topic)
            # This tool's evidence level is lower than app.py's. Recording it is what lets a
            # consumer tell the two apart; inferring it from key presence stopped working the
            # moment this script wrote the key at all.
            put("seo_topic_source", "snippet_union")

        # ---- gated: the title itself ----
        title = None
        meta: Dict[str, Any] = {}
        try:
            meta = generate_smart_title_and_hashtags(
                snippet,
                category=data.get("category", "high_value_insight"),
                video_context=ctx,
            )
            title = meta.get("suggested_title", "")
            ok, reason = validate_title(title)
        except Exception as exc:
            print(f"  [Backfill] generation failed for {path.name}: {exc}")
            stats["failed"] += 1
            ok, reason = False, str(exc)

        if ok:
            put("suggested_title", title)
            put("suggested_hashtags", meta.get("suggested_hashtags", []))
            put("candidate_titles", meta.get("candidates", []))
            put("platform_metadata", meta.get("platform_metadata", {}))
            stats["updated"] += 1
        else:
            # Keep the old title -- but the niche and seo_topic set above are still written.
            print(f"  [Backfill] GATE REJECTED {path.name}: {title!r} ({reason}); "
                  f"keeping the existing title, migrating niche/seo_topic anyway")
            stats["rejected_by_gate"] += 1

        if json.dumps(data, sort_keys=True) != before_dump:
            stats["would_change"] += 1

        if dry_run:
            print(f"  [DRY] {path.name} -> {title} | "
                  f"niche={data.get('niche')} | seo_topic={data.get('seo_topic')!r} | "
                  f"{'dirty' if dirty else 'unchanged'}")
            continue

        if not dirty:
            continue

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, **_JSON_WRITE)
        except Exception as exc:
            print(f"  [Backfill] write failed for {path.name}: {exc}")
            stats["failed"] += 1
            stats["updated"] -= 1
            continue
        # Counted only after the write succeeds, so a failed write is not reported as changed.
        stats["changed"] += 1
        # Logged only on a real write of a newly decided title: never on dry runs
        # (which `continue` above), never when the gate rejected and the old title stayed.
        if ok and title:
            log_publication(default_log_path(), build_publication_record(
                source="backfill_titles",
                video_id=data.get("video_id", ""),
                filename=data.get("filename", path.name),
                smart_meta=meta,
            ))

    return stats


def _parse_args(argv: List[str]) -> Dict[str, Any]:
    """
    Rule 4. An unrecognised flag is an error, not a positional argument and not a no-op.

    `--dryrun` is not `--dry-run`. A version of this that ignored unknown flags turned a
    typo into a full write of the live corpus, exit 0, no warning.
    """
    dry = offline = False
    out_dir = OUTPUT_DIR
    rest: List[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in _FLAGS:
            dry = dry or a == "--dry-run"
            offline = offline or a == "--offline"
        elif a in _VALUE_FLAGS:
            if i + 1 >= len(argv):
                raise SystemExit(f"{a} needs a value\n  usage: backfill_titles.py "
                                 f"[FILTER] [{' | '.join(_FLAGS)}] [--output-dir DIR]")
            out_dir = Path(argv[i + 1])
            i += 1
        elif a.startswith("-"):
            raise SystemExit(
                f"unknown flag {a!r} -- refusing to run\n"
                f"  known flags: {', '.join(sorted(_FLAGS))}, "
                f"{', '.join(sorted(_VALUE_FLAGS))} DIR\n"
                f"  a typo here would otherwise be a full write to the live corpus.")
        else:
            rest.append(a)
        i += 1
    if len(rest) > 1:
        raise SystemExit(f"expected at most one FILTER, got {len(rest)}: {rest}")
    return {"dry_run": dry, "offline": offline, "output_dir": out_dir, "filter": rest[0] if rest else None}


def main(argv: List[str]) -> int:
    opts = _parse_args(argv)
    result = backfill(
        output_dir=opts["output_dir"],
        filter_video_id=opts["filter"],
        dry_run=opts["dry_run"],
        offline=opts["offline"],
    )
    print()
    print("BACKFILL SUMMARY")
    print(f"  total            : {result['total']}")
    print(f"  updated          : {result['updated']}")
    print(f"  values changed   : {result['changed']}")
    print(f"  would change     : {result['would_change']}")
    print(f"  no transcript    : {result['skipped_no_transcript']}")
    print(f"  no context       : {result['skipped_no_context']}")
    print(f"  kept full-trans  : {result['skipped_full_transcript']}")
    print(f"  kept unknown prov: {result['skipped_unknown_provenance']}")
    print(f"  gate rejected    : {result['rejected_by_gate']}")
    print(f"  failed           : {result['failed']}")
    if result["skipped_no_context"]:
        print("\n  WARNING: some clips had no video context and were left untouched. Their")
        print("  titles are unchanged, so the per-video niche agreement this migration")
        print("  exists to establish does not hold for them.")
    if result["failed"]:
        print("\n  WARNING: failures above. The corpus is partially migrated; do not ship it.")
    if opts["dry_run"]:
        print("\n(Dry run - no files were written. Re-run without --dry-run to apply.)")
    # A non-zero exit on failure, so a caller cannot mistake a partial run for a clean one.
    return 1 if (result["failed"] or result["skipped_no_context"]) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
